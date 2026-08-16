"""contracts.py 단독 테스트. rclpy 없이 돌아간다 (pytest 로만 실행 가능):

    pip install -r requirements-dev.txt
    pytest mqtt_bridge/test/test_contracts.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re  # noqa: E402

from mqtt_bridge.contracts import (  # noqa: E402
    Command,
    CommandAction,
    ErrorCode,
    ErrorDetail,
    Inventory,
    InventorySource,
    InventoryStatus,
    RobotRole,
    RobotState,
    Status,
    StatusPayload,
    now_iso,
)


def test_command_parses_backend_wire_format():
    """Backend의 orchestrator._publish_command가 실제로 발행하는 형태와 맞는지 확인."""
    raw = {
        "type": "COMMAND",
        "timestamp": "2026-08-10T00:00:00.000Z",
        "schemaVersion": 2,
        "commandId": "cmd-1",
        "jobId": "evt-1",
        "robotId": "beagle-01",
        "role": "AMR",
        "action": "MOVE_TO",
        "payload": {"destination": "L1"},
        "timeoutSec": 60,
    }
    command = Command.model_validate(raw)
    assert command.robotId == "beagle-01"
    assert command.role == RobotRole.AMR
    assert command.action == CommandAction.MOVE_TO
    assert command.payload["destination"] == "L1"


def test_status_serializes_to_backend_expected_shape():
    status = Status(
        timestamp="2026-08-10T00:00:05.000Z",
        commandId="cmd-1",
        jobId="evt-1",
        robotId="beagle-01",
        state=RobotState.DONE,
        payload=StatusPayload(detail="ARRIVED", progress=1.0),
    )
    dumped = status.model_dump(mode="json")
    assert dumped["type"] == "STATUS"
    assert dumped["state"] == "DONE"
    assert dumped["payload"]["detail"] == "ARRIVED"
    assert dumped["payload"]["error"] is None


def test_status_with_error_serializes_error_detail():
    status = Status(
        timestamp="2026-08-10T00:00:05.000Z",
        commandId="cmd-1",
        robotId="beagle-01",
        state=RobotState.FAILED,
        payload=StatusPayload(error=ErrorDetail(code=ErrorCode.HARDWARE, message="그리퍼 걸림")),
    )
    dumped = status.model_dump(mode="json")
    assert dumped["payload"]["error"]["code"] == "HARDWARE"
    assert dumped["payload"]["error"]["detailCode"] is None


def test_error_detail_accepts_nonstandard_code_and_detail_code():
    """COMMAND_SCHEMA §5 개정: code는 자유 문자열 허용, detailCode는 optional."""
    detail = ErrorDetail(code="GRIPPER_FAULT", message="커스텀 에러", detailCode="GRIPPER_JAM_LEFT")
    dumped = detail.model_dump(mode="json")
    assert dumped["code"] == "GRIPPER_FAULT"
    assert dumped["detailCode"] == "GRIPPER_JAM_LEFT"


def test_now_iso_matches_contract_format():
    """§1: UTC, 밀리초 정확히 3자리, 'Z' 접미사."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", now_iso())


def test_inventory_serializes_to_contract_shape():
    """§10: mock_vision/vision_bridge가 발행하는 INVENTORY의 wire 형태 검증."""
    inventory = Inventory(
        timestamp="2026-08-16T00:00:00.000Z",
        lineId="line-a",
        partId="P-001",
        areaRatio=0.03,
        thresholdRatio=0.05,
        qtyEstimate=3,
        status=InventoryStatus.LOW,
        source=InventorySource.CV_AREA,
        cameraId="cam-line-a",
    )
    dumped = inventory.model_dump(mode="json")
    assert dumped["type"] == "INVENTORY"
    assert dumped["schemaVersion"] == 2
    assert dumped["lineId"] == "line-a"
    assert dumped["status"] == "LOW"
    # §10 개정: 예약 슬롯은 기본 null로 직렬화된다 (BE는 null이면 registry 값으로 대체)
    assert dumped["partName"] is None
    assert dumped["requiredQty"] is None
