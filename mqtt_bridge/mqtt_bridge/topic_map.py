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
    # omxf-line-02(PC2, station_c 담당)는 티칭·레이아웃이 아직 안 끝나서 태스크
    # 매니저가 없다 — 실물 노드가 뜨면 이 맵에 추가한다(ROS2_WIRING.md §2). 이 맵에
    # 없는 robotId의 커맨드는 bridge_node가 무시하고 경고만 남긴다 — line-b~line-f는
    # 그래서 지금 전부 mock_robot.py/mock_vision.py의 시뮬레이션 응답으로 대체된다.
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

# UNLOAD_RESUME의 payload.lineId -> 실물 칸(bin). line-a만 실물로 쓰기로 확정
# (2026-08-24, 이슈 #17) — 위 DESTINATION_TO_STATION과 같은 이유로 line-b~f는
# 뺐다. bin_b는 station_b의 같은 팔(omxf-line-01)이 물리적으로 닿을 수 있는
# 칸이지만, registry.yaml이 line-b를 아직 실물로 안 돌리고 있어서 의도적으로
# 안 쓴다 — 팔이 안 닿아서가 아니라 위 라우팅 이유 때문이니 헷갈리지 말 것.
LINE_TO_BIN: dict[str, str] = {
    "line-a": "bin_a",
}


def get_topics(robot_id: str) -> RobotTopics | None:
    return ROBOT_TOPICS.get(robot_id)
