#!/usr/bin/env python3
"""MQTT 모의 로봇 응답기. ROS2/rclpy 불필요 — paho-mqtt만으로 동작한다.

목적: 실제 ROS2 로봇 제어 코드가 준비되기 전, Backend <-> Hardware MQTT 왕복
자체가 되는지 확인하기 위한 임시 스텁. 나중에 실제 로봇/시뮬레이터가 준비되면
이 스크립트는 걷어내고 mqtt_bridge/mqtt_bridge/bridge_node.py의 실제 ROS2
연동으로 교체한다 (README "진짜 브리지 vs 임시 mock" 참고).

동작: robot/+/cmd를 구독하다가 COMMAND를 받으면 ACCEPTED -> (지연) -> DONE
STATUS를 그대로 돌려준다. robotId를 가리지 않고 아무 커맨드에나 응답하므로
L1/L2/L3 어느 라인의 로봇이든 그대로 동작한다.

실행:
    python3 scripts/mock_robot.py
    python3 scripts/mock_robot.py --host localhost --port 1883 --min-delay 1 --max-delay 3
"""

from __future__ import annotations

import argparse
import random
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mqtt_bridge"))

from mqtt_bridge.contracts import (  # noqa: E402
    Command,
    DONE_DETAIL_BY_ACTION,
    RobotState,
    Status,
    StatusPayload,
)
from mqtt_bridge.mqtt_link import MqttLink  # noqa: E402

_CMD_TOPIC_RE = re.compile(r"^robot/(?P<robot_id>[^/]+)/cmd$")


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class MockRobot:
    def __init__(self, host: str, port: int, min_delay: float, max_delay: float) -> None:
        self.link = MqttLink(host=host, port=port, client_id="mock-robot")
        self.link.on_message_callback = self._on_message
        self.min_delay = min_delay
        self.max_delay = max_delay

    def start(self) -> None:
        self.link.connect()
        for _ in range(50):
            if self.link.is_connected:
                break
            time.sleep(0.1)
        if not self.link.is_connected:
            print(f"[mock-robot] 경고: {self.link.host}:{self.link.port} 연결 확인 안 됨 (계속 재시도 중)")
        self.link.subscribe("robot/+/cmd", qos=1)
        print(f"[mock-robot] robot/+/cmd 구독 시작 (broker={self.link.host}:{self.link.port})")

    def _on_message(self, topic: str, payload: bytes) -> None:
        match = _CMD_TOPIC_RE.match(topic)
        if not match:
            return

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
            timestamp=_now_iso(),
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
    args = parser.parse_args()

    robot = MockRobot(args.host, args.port, args.min_delay, args.max_delay)
    robot.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[mock-robot] 종료")


if __name__ == "__main__":
    main()
