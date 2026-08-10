"""MQTT 메시지 계약. Team1SmartFactory/Backend의 app/contracts/{enums,messages}.py와
필드명·값을 1:1로 맞춘 것 — 두 레포가 같은 JSON을 주고받아야 하므로 여기서 임의로
필드를 늘리거나 이름을 바꾸지 않는다. 백엔드 계약이 바뀌면 이 파일도 같이 갱신할 것
(docs/COMMAND_SCHEMA.md 참고).

rclpy에 의존하지 않는 순수 파이썬(pydantic)이라 ROS2 환경 밖에서도 그대로
테스트할 수 있다 (test/test_contracts.py).
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 열거형 (COMMAND_SCHEMA.md 2~5장)
# ---------------------------------------------------------------------------


class RobotRole(str, Enum):
    STORAGE_ARM = "STORAGE_ARM"
    LINE_ARM = "LINE_ARM"
    AMR = "AMR"


class CommandAction(str, Enum):
    PICK_LOAD = "PICK_LOAD"
    MOVE_TO = "MOVE_TO"
    UNLOAD_RESUME = "UNLOAD_RESUME"
    HOME = "HOME"
    ABORT = "ABORT"


class RobotState(str, Enum):
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ErrorCode(str, Enum):
    TIMEOUT = "TIMEOUT"
    BUSY = "BUSY"
    UNSUPPORTED = "UNSUPPORTED"
    HARDWARE = "HARDWARE"
    ABORTED = "ABORTED"


class TelemetrySource(str, Enum):
    GPS = "GPS"
    SLAM = "SLAM"
    ODOM = "ODOM"


# ---------------------------------------------------------------------------
# 메시지 (COMMAND_SCHEMA.md 6~8장)
# ---------------------------------------------------------------------------


class MessageBase(BaseModel):
    timestamp: str
    schemaVersion: Literal[2] = 2


class Command(MessageBase):
    """백엔드 -> 로봇. robot/{robotId}/cmd 토픽에서 수신(브리지 입장에서는 구독)."""

    type: Literal["COMMAND"] = "COMMAND"
    commandId: str
    jobId: str | None = None
    robotId: str
    role: RobotRole
    action: CommandAction
    payload: dict = Field(default_factory=dict)
    timeoutSec: int = 60


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class StatusPayload(BaseModel):
    detail: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    error: ErrorDetail | None = None


class Status(MessageBase):
    """로봇 -> 백엔드. robot/{robotId}/status 토픽으로 발행."""

    type: Literal["STATUS"] = "STATUS"
    commandId: str
    jobId: str | None = None
    robotId: str
    state: RobotState
    payload: StatusPayload = Field(default_factory=StatusPayload)


class Position(BaseModel):
    x: float
    y: float
    theta: float


class Telemetry(MessageBase):
    """로봇 -> 백엔드. robot/{robotId}/telemetry 토픽으로 발행 (1~5Hz 권장)."""

    type: Literal["TELEMETRY"] = "TELEMETRY"
    robotId: str
    position: Position
    battery: float = Field(ge=0, le=1)
    source: TelemetrySource


# STATUS.detail 고정값 (DONE일 때 role/action별 권장 문구). 강제는 아니지만
# 백엔드 로그·디버깅 편의를 위해 이 값들을 쓰는 걸 권장한다.
DONE_DETAIL_BY_ACTION: dict[CommandAction, str] = {
    CommandAction.PICK_LOAD: "LOADED",
    CommandAction.MOVE_TO: "ARRIVED",
    CommandAction.UNLOAD_RESUME: "RESUMED",
    CommandAction.HOME: "HOMED",
    CommandAction.ABORT: "ABORTED",
}
