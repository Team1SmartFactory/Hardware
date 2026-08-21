#!/usr/bin/env python3
"""MQTT 모의 로봇 응답기. ROS2/rclpy 불필요 — paho-mqtt만으로 동작한다.

목적: 실제 ROS2 로봇 제어 코드가 준비되기 전, Backend <-> Hardware MQTT 왕복
자체가 되는지 확인하기 위한 임시 스텁. 나중에 실제 로봇/시뮬레이터가 준비되면
이 스크립트는 걷어내고 mqtt_bridge/mqtt_bridge/bridge_node.py의 실제 ROS2
연동으로 교체한다 (README "진짜 브리지 vs 임시 mock" 참고).

동작: robot/+/cmd를 구독하다가 COMMAND를 받으면 ACCEPTED -> (지연) -> DONE
STATUS를 그대로 돌려준다.

기본값으로 **실기 robotId(topic_map.ROBOT_TOPICS에 있는 것)는 무시**한다 —
CONNECTION_PLAN.md Phase 4-19(mock은 시뮬 로봇 id 전용) 및 이슈 #15: 실기
mqtt_bridge와 같이 띄워도 이중 응답이 나지 않아, 실기 라인(line-a)과 시뮬
라인(line-b~f)을 동시에 살릴 수 있다. 실기 브리지 없이 mock만으로 전체 왕복을
보고 싶을 때(README 빠른 시작)만 --all로 기존처럼 전부 응답하게 한다.

실행:
    python3 scripts/mock_robot.py            # 시뮬 로봇만 응답 (실기 브리지와 동시 기동 안전)
    python3 scripts/mock_robot.py --all      # 전부 응답 (브리지 없이 mock 단독 검증용)
    python3 scripts/mock_robot.py --host localhost --port 1883 --min-delay 1 --max-delay 3
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mqtt_bridge"))

from mqtt_bridge.contracts import (  # noqa: E402
    Command,
    DONE_DETAIL_BY_ACTION,
    RobotState,
    Status,
    StatusPayload,
    now_iso,
)
from mqtt_bridge.mqtt_link import MqttLink  # noqa: E402
from mqtt_bridge.topic_map import ROBOT_TOPICS  # noqa: E402

_CMD_TOPIC_RE = re.compile(r"^robot/(?P<robot_id>[^/]+)/cmd$")

# 실기 브리지(bridge_node.py)가 응답을 책임지는 robotId — 여기 응답하면 이중
# 응답으로 로직이 꼬인다 (이슈 #15). --all일 때만 무시하고 전부 응답.
REAL_ROBOT_IDS = frozenset(ROBOT_TOPICS)


class MockRobot:
    def __init__(self, host: str, port: int, min_delay: float, max_delay: float,
                 answer_all: bool = False) -> None:
        self.link = MqttLink(host=host, port=port, client_id="mock-robot")
        self.link.on_message_callback = self._on_message
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.answer_all = answer_all

    def start(self) -> None:
        self.link.connect()
        for _ in range(50):
            if self.link.is_connected:
                break
            time.sleep(0.1)
        if not self.link.is_connected:
            print(f"[mock-robot] 경고: {self.link.host}:{self.link.port} 연결 확인 안 됨 (계속 재시도 중)")
        self.link.subscribe("robot/+/cmd", qos=1)
        scope = "전체 robotId 응답 (--all)" if self.answer_all else \
            f"시뮬 로봇만 응답, 실기 제외: {sorted(REAL_ROBOT_IDS)}"
        print(f"[mock-robot] robot/+/cmd 구독 시작 (broker={self.link.host}:{self.link.port}, {scope})")

    def _on_message(self, topic: str, payload: bytes) -> None:
        match = _CMD_TOPIC_RE.match(topic)
        if not match:
            return
        if not self.answer_all and match.group("robot_id") in REAL_ROBOT_IDS:
            return  # 실기 브리지 담당 — 응답하면 이중 응답 (이슈 #15)

        try:
            command = Command.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"[mock-robot] COMMAND 파싱 실패, 무시: {exc}")
            return

        print(f"[mock-robot] <- {command.robotId} {command.action.value} (commandId={command.commandId})")
        self._publish(command, RobotState.ACCEPTED, detail="ACCEPTED")

        # 콜백 스레드를 그대로 막지 않도록, 지연-응답은 별도 스레드에서 처리한다
        # (여러 라인의 커맨드가 겹칠 때 서로 밀리지 않게).
        threading.Thread(target=self._respond_done, args=(command,), daemon=True).start()

    def _respond_done(self, command: Command) -> None:
        delay = random.uniform(self.min_delay, self.max_delay)
        time.sleep(delay)
        self._publish(command, RobotState.DONE, detail=DONE_DETAIL_BY_ACTION.get(command.action))
        print(f"[mock-robot] -> {command.robotId} DONE ({delay:.1f}s 후)")

    def _publish(self, command: Command, state: RobotState, detail: str | None = None) -> None:
        status = Status(
            timestamp=now_iso(),
            commandId=command.commandId,
            jobId=command.jobId,
            robotId=command.robotId,
            state=state,
            payload=StatusPayload(detail=detail),
        )
        self.link.publish(f"robot/{command.robotId}/status", status.model_dump(mode="json"), qos=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT 모의 로봇 응답기 (ROS2 불필요)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--min-delay", type=float, default=1.0, help="ACCEPTED -> DONE 사이 최소 지연(초)")
    parser.add_argument("--max-delay", type=float, default=3.0, help="ACCEPTED -> DONE 사이 최대 지연(초)")
    parser.add_argument("--all", action="store_true",
                        help="실기 robotId에도 응답 (실기 브리지 없이 mock 단독으로 왕복 검증할 때만)")
    args = parser.parse_args()

    robot = MockRobot(args.host, args.port, args.min_delay, args.max_delay, answer_all=args.all)
    robot.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[mock-robot] 종료")


if __name__ == "__main__":
    main()
