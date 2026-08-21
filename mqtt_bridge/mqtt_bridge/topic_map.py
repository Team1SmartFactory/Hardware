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

# MOVE_TO의 destination -> 실제 비글 스테이션 이름. Backend는 "STORAGE" 또는 라인
# id(line-a~f)를 보내는데, 물리 베이는 station_a(보관소)/station_b(라인) 둘뿐이라
# 어느 라인이든 station_b로 간다(ROS2_WIRING.md §3 MOVE_TO 절).
DESTINATION_TO_STATION: dict[str, str] = {
    "STORAGE": "station_a",
    "line-a": "station_b",
    "line-b": "station_b",
    "line-c": "station_b",
    "line-d": "station_b",
    "line-e": "station_b",
    "line-f": "station_b",
}

# UNLOAD_RESUME의 payload.lineId -> 실물 칸(bin). 물리 칸이 4개(bin_a~d)뿐이라
# line-e/line-f는 지원 대상에서 뺐다 — 이 두 라인은 당분간 mock 데이터로 유지
# (2026-08-21 확정, ROS2_WIRING.md §3 UNLOAD_RESUME 절의 제안을 그대로 채택).
LINE_TO_BIN: dict[str, str] = {
    "line-a": "bin_a",
    "line-b": "bin_b",
    "line-c": "bin_c",
    "line-d": "bin_d",
}


def get_topics(robot_id: str) -> RobotTopics | None:
    return ROBOT_TOPICS.get(robot_id)
