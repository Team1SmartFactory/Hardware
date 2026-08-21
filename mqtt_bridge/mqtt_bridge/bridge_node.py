"""ROS2 <-> MQTT 브리지 노드.

역할: Backend(Team1SmartFactory/Backend)가 robot/{robotId}/cmd로 발행하는 커맨드를
받아 ROS2 쪽(로봇 제어)으로 넘기고, ROS2 쪽 결과를 robot/{robotId}/status ·
/telemetry · bridge/online MQTT 토픽으로 되돌려 보낸다. 계약은 docs/COMMAND_SCHEMA.md
(v2, CONNECTION_PLAN.md Phase 2 반영). 실제 로봇 연동 사양은 docs/ROS2_WIRING.md
(로봇 제어 쪽에서 확정, 2026-08-20).

ROS2 쪽 실제 인터페이스는 raw Action이 아니라 스테이션마다 상주하는
stock_task_manager_node에 transfer/state 토픽(std_msgs/String, JSON)으로 말을
거는 구조다(ROS2_WIRING.md §1) — 태스크 매니저가 안전장치(경유점·후퇴 고도·충돌
회피)를 전부 갖고 있어서, 브리지가 관절을 직접 던지면 그 안전장치를 우회하게
된다. topic_map.py에 로봇별 실제 토픽이 채워져 있으니 여기서는 하드코딩하지
않는다.

⚠️ 오케스트레이터 충돌 주의(ROS2_WIRING.md §4): 대시보드 연동 모드에서는 로봇
저장소의 `/stock/refill_request`를 아무도 발행하면 안 된다 — Backend orchestrator의
PICK_LOAD→MOVE_TO→UNLOAD_RESUME→MOVE_TO 4단계와 로봇 저장소의 stock_relay_node가
같은 일을 해서, 지휘자가 둘이 되면 같은 태스크 매니저에 transfer가 겹쳐 들어가
한쪽이 rejected로 죽는다.

실행 (ROS2 환경에서):
    ros2 run mqtt_bridge bridge_node
    # 또는: ros2 launch mqtt_bridge bridge.launch.py
"""

from __future__ import annotations

import json
import re
import time
from functools import partial

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, String

from .contracts import (
    Command,
    CommandAction,
    DONE_DETAIL_BY_ACTION,
    ErrorCode,
    ErrorDetail,
    RobotRole,
    RobotState,
    Status,
    StatusPayload,
    is_expired,
    now_iso,
)
from .mqtt_link import MqttLink
from .topic_map import DESTINATION_TO_STATION, LINE_TO_BIN, ROBOT_TOPICS, RobotTopics, get_topics

_CMD_TOPIC_RE = re.compile(r"^robot/(?P<robot_id>[^/]+)/cmd$")

# ROS2_WIRING.md §3 HOME 절: 비글의 HOME은 "STORAGE로 가는 MOVE_TO"와 동일하게 처리.
_HOME_DESTINATION = "STORAGE"


class BridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("mqtt_bridge")

        self.declare_parameter("mqtt_host", "localhost")
        self.declare_parameter("mqtt_port", 1883)
        host = self.get_parameter("mqtt_host").get_parameter_value().string_value
        port = self.get_parameter("mqtt_port").get_parameter_value().integer_value or 1883

        self._managed_robot_ids = list(ROBOT_TOPICS)

        # robotId -> 그 로봇에 마지막으로 전달된 Command. ROS2 콜백(태스크 매니저
        # state, 비글 state)이 비동기로 도착했을 때 "이게 어떤 커맨드에 대한
        # 응답인지" 되짚어보는 용도. 커맨드 하나 끝나면 지운다.
        self._pending: dict[str, Command] = {}
        # robotId -> 마지막으로 처리한 commandId. QoS 1 중복 배달 시 재실행을 막는다
        # (COMMAND_SCHEMA.md §6.1).
        self._last_command_id: dict[str, str] = {}
        # robotId -> MOVE_TO/HOME으로 목표한 스테이션. _on_beagle_state에서 도착
        # 판정할 때 이 값과 비교한다.
        self._pending_station: dict[str, str] = {}
        # robotId -> 가장 최근 관측한 task_state/beagle state (JSON dict). HOME의
        # idle 판정, MOVE_TO/HOME의 "이미 도착해 있으면 즉시 DONE" 판정에 쓴다.
        self._arm_last_state: dict[str, dict] = {}
        self._beagle_last_state: dict[str, dict] = {}

        # robotId -> rclpy Publisher. topic_map.py에 정의된 로봇만 채워진다.
        self._transfer_pubs: dict[str, object] = {}
        self._goal_pubs: dict[str, object] = {}
        self._estop_pubs: dict[str, object] = {}

        self._mqtt = MqttLink(host=host, port=port, client_id="hardware-bridge")
        self._mqtt.on_message_callback = self._on_mqtt_message
        # 브리지 프로세스 전체의 생사 신호(§9a) — 커넥션 하나당 LWT는 하나뿐이라
        # 로봇별이 아니라 "이 브리지가 관리하는 로봇 전부"를 한 번에 알린다
        # (CONNECTION_PLAN.md C1: 로봇별 커넥션 분리는 과설계로 명시적 금지).
        self._mqtt.set_last_will(
            "bridge/online",
            {"online": False, "robotIds": self._managed_robot_ids},
            qos=1,
            retain=True,
        )
        self._mqtt.connect()
        for _ in range(50):  # 최대 5초, mock_robot.py/mock_vision.py와 동일한 대기 패턴
            if self._mqtt.is_connected:
                break
            time.sleep(0.1)
        self._mqtt.subscribe("robot/+/cmd", qos=1)
        self._publish_bridge_online()

        # ROS2 쪽 — 스테이션 태스크 매니저 / 비글의 발행·구독을 전부 topic_map.py
        # 기준으로만 연다(하드코딩 금지, ROS2_WIRING.md §2).
        for robot_id, topics in ROBOT_TOPICS.items():
            if topics.transfer_topic:
                self._transfer_pubs[robot_id] = self.create_publisher(String, topics.transfer_topic, 10)
            if topics.state_topic:
                self.create_subscription(
                    String, topics.state_topic, partial(self._on_arm_state, robot_id), 10
                )
            if topics.goal_topic:
                self._goal_pubs[robot_id] = self.create_publisher(String, topics.goal_topic, 10)
            if topics.beagle_state_topic:
                self.create_subscription(
                    String, topics.beagle_state_topic, partial(self._on_beagle_state, robot_id), 10
                )
            if topics.estop_topic:
                # 다른 토픽과 달리 estop만 std_msgs/Bool이다(실물 beagle_bridge_node의
                # 구독 타입) — String으로 발행하면 타입 불일치로 조용히 버려진다(#13).
                self._estop_pubs[robot_id] = self.create_publisher(Bool, topics.estop_topic, 10)

        self.get_logger().info(f"MQTT 브리지 시작 (broker={host}:{port}, 관리 로봇: {self._managed_robot_ids})")

    def _publish_bridge_online(self) -> None:
        self._mqtt.publish(
            "bridge/online",
            {"online": True, "robotIds": self._managed_robot_ids, "ts": now_iso()},
            qos=1,
            retain=True,
        )

    # ------------------------------------------------------------------
    # MQTT -> ROS2
    # ------------------------------------------------------------------

    def _on_mqtt_message(self, topic: str, payload: bytes) -> None:
        match = _CMD_TOPIC_RE.match(topic)
        if not match:
            return

        try:
            command = Command.model_validate_json(payload)
        except Exception:  # noqa: BLE001 — 계약과 안 맞는 메시지는 조용히 버림
            self.get_logger().warning(f"COMMAND 파싱 실패, 무시: {topic} {payload!r}")
            return

        topics = get_topics(command.robotId)
        if topics is None:
            self.get_logger().warning(
                f"topic_map.py에 없는 robotId: {command.robotId} — 무시 "
                "(config/registry.yaml의 robotId와 다른지 확인)"
            )
            return

        # §6.1 중복 수신 멱등성: 같은 로봇에 직전과 동일한 commandId가 재수신되면
        # (QoS 1 재배달) 재실행하지 않고 마지막으로 보냈던 ACCEPTED만 재발행한다.
        if self._last_command_id.get(command.robotId) == command.commandId:
            self.get_logger().info(
                f"중복 커맨드 재수신, 재실행 없이 ACCEPTED만 재발행: {command.robotId} {command.commandId}"
            )
            self._publish_status(command, RobotState.ACCEPTED, detail="ACCEPTED")
            return

        # §6.1 만료 검사: 수신 시각이 timestamp+timeoutSec을 지났으면 실행하지 않고
        # 즉시 FAILED(TIMEOUT)를 반송한다 — 브로커 재접속으로 늦게 배달된 커맨드가
        # 뒤늦게 로봇을 움직이는 사고를 막는다.
        if is_expired(command):
            self.get_logger().warning(f"만료된 커맨드, 실행하지 않고 즉시 FAILED 반송: {command.robotId} {command.commandId}")
            self._publish_failed(command, ErrorCode.TIMEOUT.value, "expired")
            return

        self._last_command_id[command.robotId] = command.commandId
        self._pending[command.robotId] = command
        self._publish_status(command, RobotState.ACCEPTED, detail="ACCEPTED")
        self._dispatch(command, topics)

    def _dispatch(self, command: Command, topics: RobotTopics) -> None:
        """action별로 실제 ROS2 쪽에 전달한다."""
        if command.action in (CommandAction.PICK_LOAD, CommandAction.UNLOAD_RESUME):
            self._dispatch_arm(command, topics)
        elif command.action == CommandAction.MOVE_TO:
            self._dispatch_move(command, topics)
        elif command.action == CommandAction.HOME:
            self._dispatch_home(command, topics)
        elif command.action == CommandAction.ABORT:
            self._dispatch_abort(command, topics)

    def _dispatch_arm(self, command: Command, topics: RobotTopics) -> None:
        """PICK_LOAD / UNLOAD_RESUME. 스테이션 태스크 매니저에 transfer 요청 하나를
        보낸다 — 집기/놓기·경유점·그리퍼는 전부 태스크 매니저가 알아서 한다
        (ROS2_WIRING.md §1, §3). 완료 판정은 여기서 하지 않고 _on_arm_state가
        last_job.id == commandId를 보고 DONE/FAILED를 올린다.
        """
        publisher = self._transfer_pubs.get(command.robotId)
        if publisher is None:
            self.get_logger().warning(f"{command.robotId}: transfer_topic 미설정 — FAILED")
            self._publish_failed(command, ErrorCode.UNSUPPORTED.value, "transfer_topic not configured")
            return

        if command.action == CommandAction.PICK_LOAD:
            from_slot, to_slot = "warehouse", "carrier"
        else:  # UNLOAD_RESUME
            line_id = command.payload.get("lineId")
            bin_id = LINE_TO_BIN.get(line_id)
            if bin_id is None:
                # 물리 칸이 4개(bin_a~d)뿐이라 line-e/line-f는 실물 로봇 지원 대상이
                # 아니다(2026-08-21 확정) — 정직하게 실패시키고 mock 데이터로 대체.
                self.get_logger().warning(
                    f"{command.robotId}: UNLOAD_RESUME lineId={line_id!r}에 대응하는 실물 칸 없음 — FAILED"
                )
                self._publish_failed(command, ErrorCode.UNSUPPORTED.value, f"no physical bin for lineId={line_id}")
                return
            from_slot, to_slot = "carrier", bin_id

        payload = {"from": from_slot, "to": to_slot, "id": command.commandId}
        publisher.publish(String(data=json.dumps(payload)))

    def _dispatch_move(self, command: Command, topics: RobotTopics) -> None:
        """MOVE_TO. payload: {destination}. destination을 실제 스테이션 이름으로
        바꿔 비글에 목표를 발행한다 — 도착 판정은 _on_beagle_state에 맡긴다.
        """
        destination = command.payload.get("destination")
        target_station = DESTINATION_TO_STATION.get(destination)
        if target_station is None:
            self.get_logger().warning(f"{command.robotId}: MOVE_TO destination={destination!r} 매핑 없음 — FAILED")
            self._publish_failed(
                command, ErrorCode.UNSUPPORTED.value, f"no station mapping for destination={destination}"
            )
            return
        self._go_to_station(command, target_station)

    def _dispatch_home(self, command: Command, topics: RobotTopics) -> None:
        """팔은 모든 transfer의 마지막 스텝이 home 복귀라 별도 동작 없이 바로
        응답한다 — idle이면 DONE, 아니면 FAILED(BUSY). 비글은 STORAGE로 가는
        MOVE_TO와 동일하게 처리한다(ROS2_WIRING.md §3 HOME 절)."""
        if topics.role == RobotRole.AMR:
            target_station = DESTINATION_TO_STATION[_HOME_DESTINATION]
            self._go_to_station(command, target_station)
            return

        last_state = self._arm_last_state.get(command.robotId) or {}
        # 아직 state를 한 번도 못 받았으면 idle로 간주한다 — 커맨드가 오기 전이면
        # 대개 대기 중이었을 가능성이 높다(보수적으로 실패시키는 것보다 낫다).
        state = last_state.get("state", "idle")
        if state == "idle":
            self._publish_done(command)
        else:
            self._publish_failed(command, ErrorCode.BUSY.value, f"arm state={state}, not idle")

    def _dispatch_abort(self, command: Command, topics: RobotTopics) -> None:
        """비글: /beagle/estop 발행 후 즉시 DONE. 팔: 태스크 매니저에 중단
        인터페이스가 없어 정직하게 FAILED(UNSUPPORTED)로 응답한다
        (ROS2_WIRING.md §3 ABORT 절)."""
        publisher = self._estop_pubs.get(command.robotId)
        if publisher is None:
            self._publish_failed(command, ErrorCode.UNSUPPORTED.value, "no abort interface for this robot")
            return
        publisher.publish(Bool(data=True))  # 래치됨 — 해제(data=False)는 현 계약에 없음
        self._publish_done(command)

    def _go_to_station(self, command: Command, target_station: str) -> None:
        """비글에게 target_station으로 가라고 지시한다. 최근 관측한 상태로 봤을 때
        이미 그 스테이션에 도착해 있으면(ready_for_arm까지 참) 새로 움직이지 않고
        즉시 DONE — MOVE_TO/HOME 둘 다 이 경로를 공유한다."""
        last_state = self._beagle_last_state.get(command.robotId)
        if last_state and last_state.get("station") == target_station and last_state.get("ready_for_arm"):
            self._publish_done(command)
            return

        publisher = self._goal_pubs.get(command.robotId)
        if publisher is None:
            self._publish_failed(command, ErrorCode.UNSUPPORTED.value, "goal_topic not configured")
            return
        self._pending_station[command.robotId] = target_station
        publisher.publish(String(data=target_station))

    # ------------------------------------------------------------------
    # ROS2 -> MQTT (팔/비글 state 구독 콜백)
    # ------------------------------------------------------------------

    def _on_arm_state(self, robot_id: str, msg: String) -> None:
        """스테이션 태스크 매니저의 task_state 구독 콜백. §3 PICK_LOAD/UNLOAD_RESUME
        완료 판정: state가 busy인 동안 RUNNING, last_job.id가 대기 중인 commandId와
        일치하면 result에 따라 DONE/FAILED."""
        try:
            state = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warning(f"{robot_id}: task_state JSON 파싱 실패, 무시: {msg.data!r}")
            return
        self._arm_last_state[robot_id] = state

        command = self._pending.get(robot_id)
        if command is None or command.action not in (CommandAction.PICK_LOAD, CommandAction.UNLOAD_RESUME):
            return  # HOME 판정용으로만 쓰거나, 대기 중인 커맨드가 없으면 상태만 기록

        if state.get("state") == "busy":
            self._publish_status(command, RobotState.RUNNING, detail="BUSY")
            return

        last_job = state.get("last_job")
        if not last_job or last_job.get("id") != command.commandId:
            return  # 다른 커맨드의 결과이거나 아직 안 끝남

        if last_job.get("result") == "ok":
            self._publish_done(command)
        else:
            # blocked(예: 보관소 재고 소진 "the warehouse is empty")도 여기로 온다 —
            # 태스크 매니저가 last_job.result를 failed/rejected로 채워서 준다
            # (ROS2_WIRING.md §3 PICK_LOAD 절).
            self._publish_failed(command, ErrorCode.HARDWARE.value, last_job.get("error") or "task failed")

    def _on_beagle_state(self, robot_id: str, msg: String) -> None:
        """비글 state 구독 콜백(0.5s 주기). §3 MOVE_TO/HOME 완료 판정: station이
        목표와 같고 ready_for_arm이 참이면 DONE, detail에 에러 문자열이 있으면
        FAILED."""
        try:
            state = json.loads(msg.data)
        except (json.JSONDecodeError, TypeError):
            self.get_logger().warning(f"{robot_id}: beagle state JSON 파싱 실패, 무시: {msg.data!r}")
            return
        self._beagle_last_state[robot_id] = state

        command = self._pending.get(robot_id)
        if command is None or command.action not in (CommandAction.MOVE_TO, CommandAction.HOME):
            return

        # detail은 에러 전용 필드가 아니다 — 주행 중에는 루트 키('station_a->station_b'),
        # nudge 중에는 'nudge +0.050 m'가 채워진다. detail 유무로 판정하면 출발하는
        # 순간 FAILED로 오판한다(#13). 에러는 state가 error/estop일 때뿐이고, 그때
        # detail이 사유 문자열이다.
        beagle_state = state.get("state")
        if beagle_state in ("error", "estop"):
            self._publish_failed(
                command,
                ErrorCode.HARDWARE.value,
                state.get("detail") or f"beagle state={beagle_state}",
            )
            self._pending_station.pop(robot_id, None)
            return

        target = self._pending_station.get(robot_id)
        if target is not None and state.get("station") == target and state.get("ready_for_arm"):
            self._publish_done(command)
            self._pending_station.pop(robot_id, None)
            return

        self._publish_status(command, RobotState.RUNNING, detail="MOVING")

    # ------------------------------------------------------------------
    # ROS2 -> MQTT (커맨드 상태 발행)
    # ------------------------------------------------------------------

    def _publish_status(
        self,
        command: Command,
        state: RobotState,
        detail: str | None = None,
        error: ErrorDetail | None = None,
    ) -> None:
        status = Status(
            timestamp=now_iso(),
            commandId=command.commandId,
            jobId=command.jobId,
            robotId=command.robotId,
            state=state,
            payload=StatusPayload(detail=detail, error=error),
        )
        self._mqtt.publish(f"robot/{command.robotId}/status", status.model_dump(mode="json"), qos=1)

        if state in (RobotState.DONE, RobotState.FAILED):
            self._pending.pop(command.robotId, None)

    def _publish_done(self, command: Command) -> None:
        """dispatch/state 콜백 안에서 성공적으로 끝났을 때 호출할 헬퍼."""
        self._publish_status(command, RobotState.DONE, detail=DONE_DETAIL_BY_ACTION.get(command.action))

    def _publish_failed(self, command: Command, code: str, message: str) -> None:
        self._publish_status(command, RobotState.FAILED, error=ErrorDetail(code=code, message=message))

    def destroy_node(self) -> bool:
        # 정상 종료 시에도 곧바로 offline을 알린다 — LWT는 비정상 종료(프로세스 강제
        # 종료, 네트워크 단절)에 대한 백업이고, 정상 종료 경로에서는 굳이 브로커가
        # keepalive 타임아웃을 기다릴 필요 없이 즉시 알리는 편이 낫다.
        self._mqtt.publish(
            "bridge/online", {"online": False, "robotIds": self._managed_robot_ids}, qos=1, retain=True
        )
        self._mqtt.disconnect()
        return super().destroy_node()


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
