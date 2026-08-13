#!/usr/bin/env python3
"""MQTT 모의 비전(재고 감지) 발행기. ROS2/rclpy, YOLO 불필요.

목적: 실제 비전(YOLO) 파이프라인이 line/{lineId}/inventory를 발행하게 되기 전,
Backend의 재고 수신 경로(currentQty 갱신, 재고 이력 DB 적재, WS 브로드캐스트)를
지금 바로 검증하기 위한 임시 스텁.

⚠️ Backend에는 아직 "임계치 이하 감지 -> 승인 이벤트 자동 생성" 로직이 없다
(2026-08 기준, 알려진 gap). 이 스크립트를 띄워도 currentQty만 갱신될 뿐, 부족
이벤트가 저절로 생기지는 않는다 — 그건 Backend 쪽 별도 작업.

실행:
    python3 scripts/mock_vision.py
    python3 scripts/mock_vision.py --interval 3
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mqtt_bridge"))

from mqtt_bridge.mqtt_link import MqttLink  # noqa: E402

# Backend config/registry.yaml의 lines: 와 lineId/partId/thresholdRatio를 맞춰뒀다.
# 라인 구성이 바뀌면 이 목록도 같이 고칠 것.
DEFAULT_LINES = [
    {"lineId": "L1", "partId": "P-001", "thresholdRatio": 0.05, "cameraId": "cam-L1"},
    {"lineId": "L2", "partId": "P-002", "thresholdRatio": 0.05, "cameraId": "cam-L2"},
    {"lineId": "L3", "partId": "P-003", "thresholdRatio": 0.05, "cameraId": "cam-L3"},
]


def _now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


class MockVision:
    def __init__(self, host: str, port: int, interval: float, lines: list[dict]) -> None:
        self.link = MqttLink(host=host, port=port, client_id="mock-vision")
        self.interval = interval
        self.lines = lines
        # 완전 랜덤보다 그래프가 자연스럽게 이어지도록, 라인별 값을 들고 랜덤워크로 흔든다.
        self._area_ratio = {line["lineId"]: random.uniform(0.2, 0.6) for line in lines}

    def start(self) -> None:
        self.link.connect()
        for _ in range(50):
            if self.link.is_connected:
                break
            time.sleep(0.1)
        if not self.link.is_connected:
            print(f"[mock-vision] 경고: {self.link.host}:{self.link.port} 연결 확인 안 됨 (계속 재시도 중)")
        print(f"[mock-vision] {[line['lineId'] for line in self.lines]} 발행 시작 (주기 {self.interval}s)")

    def run_forever(self) -> None:
        try:
            while True:
                for line in self.lines:
                    self._publish_one(line)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            print("\n[mock-vision] 종료")

    def _publish_one(self, line: dict) -> None:
        line_id = line["lineId"]
        ratio = self._area_ratio[line_id]
        ratio = max(0.0, min(1.0, ratio + random.uniform(-0.03, 0.03)))
        self._area_ratio[line_id] = ratio

        payload = {
            "type": "INVENTORY",
            "timestamp": _now_iso(),
            "schemaVersion": 2,
            "lineId": line_id,
            "partId": line["partId"],
            "areaRatio": round(ratio, 4),
            "thresholdRatio": line["thresholdRatio"],
            "qtyEstimate": max(0, round(ratio * 100)),
            "status": "LOW" if ratio <= line["thresholdRatio"] else "OK",
            "source": "CV_AREA",
            "cameraId": line["cameraId"],
        }
        self.link.publish(f"line/{line_id}/inventory", payload, qos=1)
        print(f"[mock-vision] {line_id} areaRatio={ratio:.3f} ({payload['status']})")


def main() -> None:
    parser = argparse.ArgumentParser(description="MQTT 모의 비전(재고 감지) 발행기 (YOLO 대체용 임시)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--interval", type=float, default=5.0, help="라인별 발행 주기(초)")
    args = parser.parse_args()

    vision = MockVision(args.host, args.port, args.interval, DEFAULT_LINES)
    vision.start()
    vision.run_forever()


if __name__ == "__main__":
    main()
