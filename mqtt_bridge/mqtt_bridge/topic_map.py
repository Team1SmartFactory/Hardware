"""robotId <-> 실제 ROS2 토픽 매핑. docs/ROS2_WIRING.md 기준 (2026-08-20 확정).

실제 로봇 저장소: github.com/noeyod02/omx-beagle-smart-factory
(open_manipulator_playground 패키지). 배선 근거·완료 판정 로직은 ROS2_WIRING.md 참고.

⚠️ 팔(STORAGE_ARM/LINE_ARM)은 raw Action(FollowJointTrajectory)이 아니라 스테이션마다
상주하는 stock_task_manager_node에 transfer/state 토픽(std_msgs/String, JSON)으로
말을 건다 — 태스크 매니저가 티칭된 좌표·경유점·그리퍼 폭을 전부 알아서 처리한다.
브리지가 관절을 직접 raw 액션으로 던지면 이 안전장치를 우회하게 되고, 같은
컨트롤러에 커맨더가 둘이 되는 순간 goal끼리 서로 CANCELED로 죽인다(실사고 전력
있음 — ROS2_WIRING.md §1). bridge_node.py도 이 이유로 Action Client를 안 쓴다.

Team1SmartFactory/Backend의 config/registry.yaml과 robotId가 반드시 일치해야 한다
(그래야 백엔드가 발행하는 robot/{robotId}/cmd를 브리지가 알아본다).
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RobotRole


@dataclass(frozen=True)
class RobotTopics:
    """로봇 하나에 대한 ROS2 쪽 연결 정보.

    estop_topic만 std_msgs/Bool(data=true로 래치)이고 나머지는 전부
    std_msgs/String(JSON 문자열)이다 — 실물 beagle_bridge_node의 구독 타입 기준(#13).
    """

    role: RobotRole

    # 팔(STORAGE_ARM/LINE_ARM) 전용 — 태스크 매니저의 transfer/state 토픽.
    #   발행(transfer): {"from": ..., "to": ..., "id": <commandId>}
    #   구독(state): {"state": "idle"|"busy"|"blocked",
    #                 "last_job": {"id": ..., "result": "ok"|"failed"|"rejected", "error": ...} | null}
    transfer_topic: str | None = None
    state_topic: str | None = None

    # AMR(Beagle) 전용
    goal_topic: str | None = None  # 발행: 목표 스테이션 이름 문자열 (예: "station_a")
    # 구독: {"state": ..., "station": ..., "ready_for_arm": bool, "detail": str | null}
    beagle_state_topic: str | None = None
    # ABORT 시 발행 (팔에는 대응하는 인터페이스가 없음). ⚠️ 타입이 std_msgs/Bool.
    estop_topic: str | None = None


# robotId -> RobotTopics. Backend config/registry.yaml의 robotId와 반드시 맞출 것.
ROBOT_TOPICS: dict[str, RobotTopics] = {
    "omxf-storage-01": RobotTopics(
        role=RobotRole.STORAGE_ARM,
        transfer_topic="/station_a/stock/transfer",
        state_topic="/station_a/stock/task_state",
    ),
    "beagle-01": RobotTopics(
        role=RobotRole.AMR,
        goal_topic="/beagle/goto",
        beagle_state_topic="/beagle/state",
        estop_topic="/beagle/estop",
    ),
    "omxf-line-01": RobotTopics(
        role=RobotRole.LINE_ARM,
        transfer_topic="/station_b/stock/transfer",
        state_topic="/station_b/stock/task_state",
    ),
    # PC2의 세 번째 팔(station_c, 칸 c/d 담당). 2026-08-31 티칭·레이아웃 완료로
    # 합류했다. ⚠️ ROS2_WIRING.md §2가 예약해 둔 이름은 omxf-line-02였지만 그
    # robotId는 시뮬 line-b의 팔이 이미 쓰고 있다(registry.yaml) — 전역 유일해야
    # 하는 값이라 겹치면 line-b의 커맨드가 이 실물 팔로 샌다. 그래서 시뮬이
    # 점유한 02~06을 피해 07로 부여했다.
    "omxf-line-07": RobotTopics(
        role=RobotRole.LINE_ARM,
        transfer_topic="/station_c/stock/transfer",
        state_topic="/station_c/stock/task_state",
    ),
    # 이 맵에 없는 robotId의 커맨드는 bridge_node가 무시하고 경고만 남긴다 —
    # line-b~line-f는 그래서 지금 전부 mock_robot.py/mock_vision.py의 시뮬레이션
    # 응답으로 대체된다.
}

# 재고 비전(로봇 저장소 stock_monitor_node)이 보는 칸 이름 -> 대시보드의 binId.
# 칸 단위 INVENTORY를 line/{lineId}/bin/{label}/inventory로 중계할 때 쓴다
# (이슈 #27). Backend config/registry.yaml의 line-a bins[].binId/label과 맞출 것.
STOCK_BIN_TO_LINE: dict[str, tuple[str, str, str]] = {
    # monitor의 bin id: (lineId, binId, label)
    "bin_a": ("line-a", "line-a-bin-a", "a"),
    "bin_b": ("line-a", "line-a-bin-b", "b"),
    "bin_c": ("line-a", "line-a-bin-c", "c"),
    "bin_d": ("line-a", "line-a-bin-d", "d"),
}

# 칸 -> 그 칸에 부품을 놓는 partId. PART_TO_BIN의 역방향으로, 칸 단위 INVENTORY에
# partId를 채우는 데만 쓴다.
BIN_TO_PART: dict[str, str] = {
    "bin_a": "P-101",
    "bin_b": "P-102",
    "bin_c": "P-103",
    "bin_d": "P-104",
}

# MOVE_TO의 destination -> 실제 비글 스테이션 이름. line-a만 실물로 쓰기로 확정
# (이슈 #17) — Backend config/registry.yaml이 line-b~f의 로봇 역할을 전부 가상
# robotId로 라우팅하고 있어서, 이 실물 브리지(ROBOT_TOPICS)에는 애초에 도달하지
# 않는다. 여기 없는 destination은 UNSUPPORTED로 명시적으로 거부된다 — registry.yaml이
# 실수로든 의도적으로든 line-b~f를 실물 robotId로 잘못 라우팅해도 조용히 받지
# 않고 확실하게 실패한다. line-b 이후를 실물로 확장하려면 registry.yaml에서 해당
# 라인의 로봇 역할을 실물 robotId로 먼저 바꾸고, 여기에도 항목을 추가할 것.
DESTINATION_TO_STATION: dict[str, str] = {
    "STORAGE": "station_a",
    "line-a": "station_b",
}

# UNLOAD_RESUME의 payload.partId -> 실물 칸(bin). line-a는 "라인 하나 = 부품
# 하나"가 아니라 실물 로봇팔(omxf-line-01, station_b)이 닿을 수 있는 칸 4개에
# 서로 다른 부품이 적재되는 구조다(Backend#37, 2026-08-24 확정) — 그래서 목적지는
# lineId가 아니라 그 작업의 partId로 정해야 한다. payload에 partId가 이미
# 실려오므로 COMMAND 계약(wire 포맷) 변경은 없다, 값만 이걸로 해석한다.
# Backend config/registry.yaml의 line-a bins[].partId와 반드시 맞출 것.
PART_TO_BIN: dict[str, str] = {
    "P-101": "bin_a",
    "P-102": "bin_b",
    "P-103": "bin_c",
    "P-104": "bin_d",
}


def get_topics(robot_id: str) -> RobotTopics | None:
    return ROBOT_TOPICS.get(robot_id)
