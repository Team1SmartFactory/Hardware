"""ROS2 <-> MQTT 브리지 노드.

역할: Backend(Team1SmartFactory/Backend)가 robot/{robotId}/cmd로 발행하는 커맨드를
받아 ROS2 쪽(로봇 제어)으로 넘기고, ROS2 쪽 결과를 robot/{robotId}/status ·
/telemetry · bridge/online MQTT 토픽으로 되돌려 보낸다. 계약은 docs/COMMAND_SCHEMA.md
(v2, CONNECTION_PLAN.md Phase 2 반영).

⚠️ 지금은 스켈레톤이다. MQTT 송수신 배관 + 커맨드 라우팅 + 방어 규약(LWT, 중복
멱등성, 만료 검사)까지는 동작하지만, 실제 ROS2 토픽/액션과의 연결(각 _dispatch_*
메서드 안)은 비어 있다 — 그래서 지금 이 노드를 그대로 띄워도 STATUS(DONE/FAILED)가
안 나가서 백엔드 쪽은 액션별 타임아웃(60~120초) 뒤에 실패 처리된다. topic_map.py에
실제 ROS2 토픽/액션 이름을 채우고, 아래 TODO 표시된 곳을 구현해야 실제로 로봇이
움직인다.

실행 (ROS2 환경에서):
    ros2 run mqtt_bridge bridge_node
    # 또는: ros2 launch mqtt_bridge bridge.launch.py
"""

from __future__ import annotations

import re
import time

import rclpy
from rclpy.node import Node

from .contracts import (
    Command,
    CommandAction,
    DONE_DETAIL_BY_ACTION,
    ErrorCode,
    ErrorDetail,
    RobotState,
    Status,
    StatusPayload,
    is_expired,
    now_iso,
)
from .mqtt_link import MqttLink
from .topic_map import ROBOT_TOPICS, RobotTopics, get_topics

_CMD_TOPIC_RE = re.compile(r"^robot/(?P<robot_id>[^/]+)/cmd$")


class BridgeNode(Node):
    def __init__(self) -> None:
        super().__init__("mqtt_bridge")

        self.declare_parameter("mqtt_host", "localhost")
        self.declare_parameter("mqtt_port", 1883)
        host = self.get_parameter("mqtt_host").get_parameter_value().string_value
        port = self.get_parameter("mqtt_port").get_parameter_value().integer_value or 1883

        self._managed_robot_ids = list(ROBOT_TOPICS)

        # robotId -> 그 로봇에 마지막으로 전달된 Command. ROS2 콜백(도착 이벤트,
        # 액션 결과 등)이 비동기로 도착했을 때 "이게 어떤 커맨드에 대한 응답인지"
        # 되짚어보는 용도. 커맨드 하나 끝나면 지운다.
        self._pending: dict[str, Command] = {}
        # robotId -> 마지막으로 처리한 commandId. QoS 1 중복 배달 시 재실행을 막는다
        # (COMMAND_SCHEMA.md §6.1).
        self._last_command_id: dict[str, str] = {}

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

        # TODO: 여기서 topic_map.ROBOT_TOPICS를 순회하며 실제 ROS2 subscription/
        # action client를 만들어야 한다. 지금은 골격만 있다 — 예시:
        #
        #   for robot_id, topics in ROBOT_TOPICS.items():
        #       if topics.arrival_topic:
        #           self.create_subscription(
        #               Bool,  # 실제 메시지 타입 확인 필요 (std_msgs/Bool 추정)
        #               topics.arrival_topic,
        #               lambda msg, rid=robot_id: self._on_arrival(rid, msg),
        #               10,
        #           )
        #       if topics.arm_action:
        #           self._arm_clients[robot_id] = ActionClient(
        #               self, FollowJointTrajectory, topics.arm_action  # 실제 액션 타입 확인 필요
        #           )

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
        """action별로 실제 ROS2 쪽에 전달한다. 지금은 전부 TODO."""
        if command.action in (CommandAction.PICK_LOAD, CommandAction.UNLOAD_RESUME):
            self._dispatch_arm(command, topics)
        elif command.action == CommandAction.MOVE_TO:
            self._dispatch_move(command, topics)
        elif command.action == CommandAction.HOME:
            self._dispatch_home(command, topics)
        elif command.action == CommandAction.ABORT:
            self._dispatch_abort(command, topics)

    def _dispatch_arm(self, command: Command, topics: RobotTopics) -> None:
        """PICK_LOAD / UNLOAD_RESUME. payload: {partId, qty, lineId}.

        TODO: topics.arm_action으로 Action Client goal 전송, 결과 콜백에서
        self._publish_status(command, RobotState.DONE 또는 FAILED, ...) 호출.
        그리퍼가 필요하면(topics.gripper_action) 집기/놓기 액션을 arm 이동 전후로 추가.
        """
        self.get_logger().warning(
            f"[TODO] arm_action 미구현 — {command.robotId} {command.action.value} 무시됨 "
            f"(payload={command.payload})"
        )

    def _dispatch_move(self, command: Command, topics: RobotTopics) -> None:
        """MOVE_TO. payload: {destination}. 목적지 발행 + arrival_topic 콜백에서 DONE.

        TODO: topics.goal_topic으로 목적지 퍼블리시. arrival_topic 구독 콜백
        (__init__ 참고)에서 self._pending[robotId]가 아직 이 command면 DONE 발행.
        """
        self.get_logger().warning(
            f"[TODO] goal_topic 미구현 — {command.robotId} MOVE_TO "
            f"{command.payload.get('destination')} 무시됨"
        )

    def _dispatch_home(self, command: Command, topics: RobotTopics) -> None:
        """TODO: HOME은 대개 MOVE_TO의 특수 케이스(destination='STORAGE' 등)이거나
        별도 사전 정의된 포즈로 이동. 로봇 종류에 맞게 구현."""
        self.get_logger().warning(f"[TODO] HOME 미구현 — {command.robotId} 무시됨")

    def _dispatch_abort(self, command: Command, topics: RobotTopics) -> None:
        """TODO: 진행 중인 Action Client goal이 있으면 cancel_goal_async() 호출,
        AMR이면 즉시 정지 토픽 발행 등. 실패해도 예외 던지지 말 것 —
        이미 끝난 작업에 ABORT가 늦게 와도 무시하면 그만이다."""
        self.get_logger().warning(f"[TODO] ABORT 미구현 — {command.robotId} 무시됨")

    # ------------------------------------------------------------------
    # ROS2 -> MQTT
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
        """dispatch 콜백 안에서 성공적으로 끝났을 때 호출할 헬퍼."""
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
