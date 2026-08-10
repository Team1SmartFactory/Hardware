"""contracts.py 단독 테스트. rclpy 없이 돌아간다 (pytest 로만 실행 가능):

    pip install -r requirements-dev.txt
    pytest mqtt_bridge/test/test_contracts.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mqtt_bridge.contracts import (  # noqa: E402
    Command,
    CommandAction,
    ErrorCode,
    ErrorDetail,
    RobotRole,
    RobotState,
    Status,
    StatusPayload,
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
