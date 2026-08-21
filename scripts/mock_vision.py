#!/usr/bin/env python3
"""MQTT 모의 비전(재고 감지) 발행기. ROS2/rclpy, YOLO 불필요.

목적: 실제 비전(YOLO) 파이프라인이 line/{lineId}/inventory를 발행하게 되기 전,
Backend의 재고 수신 경로(currentQty 갱신, 재고 이력 DB 적재, WS 브로드캐스트,
임계치 이하 시 승인 대기 이벤트 자동 생성)를 검증하기 위한 임시 스텁.
실물화되면 scripts/vision_bridge.py(stock_state.json 폴링 방식)로 교체한다
(CONNECTION_PLAN.md Phase 3 참고).

COMMAND_SCHEMA.md §10 계약의 참조 구현이다:
  - Inventory pydantic 모델로 조립 (raw dict 손조립 금지)
  - retain=true
  - 변화 시에만 발행 (areaRatio ±0.02 초과 변화 또는 status 전이, 첫 1회는 무조건)
  - 발행 주기 상한 1Hz (--interval 하한 1초)

실행:
    python3 scripts/mock_vision.py
    python3 scripts/mock_vision.py --interval 3
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mqtt_bridge"))

from mqtt_bridge.contracts import (  # noqa: E402
    Inventory,
    InventorySource,
    InventoryStatus,
    now_iso,
)
from mqtt_bridge.mqtt_link import MqttLink  # noqa: E402

# Backend config/registry.yaml의 lines: 와 lineId/partId/thresholdRatio를 맞춰뒀다.
# 라인 구성이 바뀌면 이 목록도 같이 고칠 것.
DEFAULT_LINES = [
    {"lineId": "line-a", "partId": "P-001", "thresholdRatio": 0.05, "cameraId": "cam-line-a"},
    {"lineId": "line-b", "partId": "P-002", "thresholdRatio": 0.05, "cameraId": "cam-line-b"},
    {"lineId": "line-c", "partId": "P-003", "thresholdRatio": 0.05, "cameraId": "cam-line-c"},
    {"lineId": "line-d", "partId": "P-004", "thresholdRatio": 0.05, "cameraId": "cam-line-d"},
    {"lineId": "line-e", "partId": "P-005", "thresholdRatio": 0.05, "cameraId": "cam-line-e"},
    {"lineId": "line-f", "partId": "P-006", "thresholdRatio": 0.05, "cameraId": "cam-line-f"},
]

# §10.1 발행 규칙: 직전 발행값 대비 이만큼 초과 변해야 재발행한다.
PUBLISH_DELTA = 0.02


class MockVision:
    def __init__(self, host: str, port: int, interval: float, lines: list[dict]) -> None:
        self.link = MqttLink(host=host, port=port, client_id="mock-vision")
        self.interval = interval
        self.lines = lines
        # 완전 랜덤보다 그래프가 자연스럽게 이어지도록, 라인별 값을 들고 랜덤워크로 흔든다.
        self._area_ratio = {line["lineId"]: random.uniform(0.2, 0.6) for line in lines}
        # 직전 "발행" 값 (현재 값과 다름 — 변화 시에만 발행 규칙의 기준점)
        self._last_published: dict[str, float] = {}
        self._last_status: dict[str, InventoryStatus] = {}

    def start(self) -> None:
        self.link.connect()
        for _ in range(50):
            if self.link.is_connected:
                break
            time.sleep(0.1)
        if not self.link.is_connected:
            print(f"[mock-vision] 경고: {self.link.host}:{self.link.port} 연결 확인 안 됨 (계속 재시도 중)")
        print(f"[mock-vision] {[line['lineId'] for line in self.lines]} 발행 시작 (주기 {self.interval}s, on-change)")

    def run_forever(self) -> None:
        try:
            while True:
                for line in self.lines:
                    self._tick_one(line)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n[mock-vision] 종료")

    def _tick_one(self, line: dict) -> None:
        line_id = line["lineId"]
        ratio = self._area_ratio[line_id]
        ratio = max(0.0, min(1.0, ratio + random.uniform(-0.03, 0.03)))
        self._area_ratio[line_id] = ratio

        status = InventoryStatus.LOW if ratio <= line["thresholdRatio"] else InventoryStatus.OK

        # §10.1: 변화 시에만 발행 — 첫 1회, ±PUBLISH_DELTA 초과 변화, status 전이.
        last = self._last_published.get(line_id)
        changed = (
            last is None
            or abs(ratio - last) > PUBLISH_DELTA
            or status != self._last_status.get(line_id)
        )
        if not changed:
            return

        inventory = Inventory(
            timestamp=now_iso(),
            lineId=line_id,
            partId=line["partId"],
            areaRatio=round(ratio, 4),
            thresholdRatio=line["thresholdRatio"],
            qtyEstimate=max(0, round(ratio * 100)),
            status=status,
            source=InventorySource.CV_AREA,
            cameraId=line["cameraId"],
        )
        self.link.publish(
            f"line/{line_id}/inventory", inventory.model_dump(mode="json"), qos=1, retain=True
        )
        self._last_published[line_id] = ratio
        self._last_status[line_id] = status
        print(f"[mock-vision] {line_id} areaRatio={ratio:.3f} ({status.value})")


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT 모의 비전(재고 감지) 발행기 (YOLO 대체용 임시)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--interval", type=float, default=5.0, help="라인별 평가 주기(초, 하한 1 — §10.1 최대 1Hz)")
    args = parser.parse_args()

    interval = max(1.0, args.interval)  # §10.1: 발행 주기 상한 1Hz
    vision = MockVision(args.host, args.port, interval, DEFAULT_LINES)
    vision.start()
    vision.run_forever()


if __name__ == "__main__":
    main()
