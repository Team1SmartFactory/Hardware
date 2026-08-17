"""MQTT 메시지 계약. Team1SmartFactory/Backend의 app/contracts/{enums,messages}.py와
필드명·값을 1:1로 맞춘 것 — 두 레포가 같은 JSON을 주고받아야 하므로 여기서 임의로
필드를 늘리거나 이름을 바꾸지 않는다. 백엔드 계약이 바뀌면 이 파일도 같이 갱신할 것
(docs/COMMAND_SCHEMA.md 참고).

rclpy에 의존하지 않는 순수 파이썬(pydantic)이라 ROS2 환경 밖에서도 그대로
테스트할 수 있다 (test/test_contracts.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def now_iso() -> str:
    """계약 timestamp 형식 (COMMAND_SCHEMA.md §1): UTC, 밀리초 정확히 3자리, 'Z' 고정.

    발행측은 반드시 이 헬퍼를 쓴다 — bridge_node/mock_robot/mock_vision에 같은 구현이
    3중 복제돼 있던 것을 여기로 통합 (Hardware#5).
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


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
    """표준 에러 코드 5종 — COMMAND_SCHEMA.md §5 개정으로 wire 상에서는 자유 문자열이
    허용되지만(ErrorDetail.code: str), 발행측은 가능한 한 이 5종을 쓰는 걸 권장한다."""

    TIMEOUT = "TIMEOUT"
    BUSY = "BUSY"
    UNSUPPORTED = "UNSUPPORTED"
    HARDWARE = "HARDWARE"
    ABORTED = "ABORTED"


class TelemetrySource(str, Enum):
    GPS = "GPS"
    SLAM = "SLAM"
    ODOM = "ODOM"


class InventoryStatus(str, Enum):
    """INVENTORY.status — §10 개정으로 진단 필드로 격하됨 (부족 판정의 유일 판정자는
    BE의 registry.yaml 임계치 비교). Backend app/contracts/enums.py와 1:1."""

    OK = "OK"
    LOW = "LOW"


class InventorySource(str, Enum):
    CV_AREA = "CV_AREA"
    CV_DEPTH = "CV_DEPTH"
    LOAD_CELL = "LOAD_CELL"


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
    """COMMAND_SCHEMA.md §5. code는 자유 문자열(표준 5종은 ErrorCode 상수 권장),
    detailCode는 로봇별 특화 에러 식별자(BE는 저장·로그만 하고 해석 안 함)."""

    code: str
    message: str
    detailCode: str | None = None


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


class Inventory(MessageBase):
    """비전 -> 백엔드. line/{lineId}/inventory 토픽으로 발행 (COMMAND_SCHEMA.md §10).

    retain=true, 변화 시에만(±0.02 또는 status 전이), 최대 1Hz — §10.1 발행 규칙.
    status/thresholdRatio는 진단 필드(BE가 판정에 쓰지 않음), partName/requiredQty는
    "박스 교체 로직" 확정 전 예약 슬롯(기본 null).
    """

    type: Literal["INVENTORY"] = "INVENTORY"
    lineId: str
    partId: str
    areaRatio: float = Field(ge=0, le=1)
    thresholdRatio: float = Field(ge=0, le=1)
    qtyEstimate: int
    status: InventoryStatus
    source: InventorySource
    cameraId: str
    partName: str | None = None
    requiredQty: int | None = None


# STATUS.detail 고정값 (DONE일 때 role/action별 권장 문구). 강제는 아니지만
# 백엔드 로그·디버깅 편의를 위해 이 값들을 쓰는 걸 권장한다.
DONE_DETAIL_BY_ACTION: dict[CommandAction, str] = {
    CommandAction.PICK_LOAD: "LOADED",
    CommandAction.MOVE_TO: "ARRIVED",
    CommandAction.UNLOAD_RESUME: "RESUMED",
    CommandAction.HOME: "HOMED",
    CommandAction.ABORT: "ABORTED",
}


def is_expired(command: Command, *, now: datetime | None = None) -> bool:
    """COMMAND_SCHEMA.md §6.1: 수신 시각이 timestamp+timeoutSec을 지났으면 만료.

    rclpy에 의존하지 않는 순수 함수로 bridge_node.py 밖에 둬서(PROJECT_RULES.md
    "rclpy 의존은 bridge_node.py 한 파일에 격리") ROS2 없이 테스트할 수 있게 한다.
    `now`는 테스트에서 시각을 고정하기 위한 주입 포인트 — 실서비스에서는 생략한다.
    """
    try:
        issued_at = datetime.fromisoformat(command.timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False  # 형식이 이상하면 만료 판정을 포기하고 통과시킨다(보수적 선택)
    reference = now if now is not None else datetime.now(timezone.utc)
    return reference > issued_at + timedelta(seconds=command.timeoutSec)
