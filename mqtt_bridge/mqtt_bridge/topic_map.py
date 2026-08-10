"""robotId <-> 실제 ROS2 토픽/액션 매핑.

이 파일이 이 레포에서 **가장 먼저 실제 값으로 채워야 하는 곳**이다. 여기 있는
토픽/액션 이름은 확정값이 아니라, 주간 보고서에 언급된 노드 이름(bridge_node.py,
arm_control.py, /beagle_arrived, /gripper_controller/gripper_cmd, stock_bridge.py)에서
따온 추정값이다 — 실제 ROS2 쪽(정지우 팀장 작업물) 토픽/액션 이름이 확정되면
아래 딕셔너리만 고치면 된다. bridge_node.py 본체는 이 파일을 통해서만 ROS2
토픽 이름을 알아야 하고, 하드코딩하면 안 된다.

Team1BE/Backend의 config/registry.yaml과 robotId가 반드시 일치해야 한다
(그래야 백엔드가 발행하는 robot/{robotId}/cmd를 브리지가 알아본다).
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import RobotRole


@dataclass(frozen=True)
class RobotTopics:
    """로봇 하나에 대한 ROS2 쪽 연결 정보."""

    role: RobotRole

    # AMR(Beagle) 전용 — MOVE_TO 커맨드를 받으면 이 토픽으로 목적지를 퍼블리시하고,
    # 도착 이벤트는 arrival_topic으로 받는다.
    # TODO: 실제 목적지 발행 토픽/메시지 타입 확정 (지금은 정지우 팀장 bridge_node.py의
    # /beagle_arrived만 알려져 있고, "가라"는 명령을 어떤 토픽으로 주는지는 미확인)
    goal_topic: str | None = None
    arrival_topic: str | None = None  # 예상: "/beagle_arrived" (std_msgs/Bool 추정)

    # STORAGE_ARM / LINE_ARM(OMX-F) 전용 — 관절 포즈 이동은 Action Client로 한다고
    # 보고서에 명시됨(arm_control.py). 액션 이름/타입은 확정 전까지 placeholder.
    # TODO: 실제 Action 이름/타입 확정 (control_msgs/FollowJointTrajectory 추정)
    arm_action: str | None = None
    # 그리퍼 제어. 다음 주 계획에 명시된 액션 토픽.
    gripper_action: str | None = None  # 예상: "/gripper_controller/gripper_cmd"


# robotId -> RobotTopics. Backend config/registry.yaml의 robotId와 반드시 맞출 것.
ROBOT_TOPICS: dict[str, RobotTopics] = {
    "omxf-storage-01": RobotTopics(
        role=RobotRole.STORAGE_ARM,
        arm_action="/omxf_storage_01/arm_control",  # TODO placeholder
        gripper_action="/omxf_storage_01/gripper_controller/gripper_cmd",  # TODO placeholder
    ),
    "beagle-01": RobotTopics(
        role=RobotRole.AMR,
        goal_topic="/beagle_01/goal",  # TODO placeholder
        arrival_topic="/beagle_01/beagle_arrived",  # TODO placeholder
    ),
    "omxf-line-01": RobotTopics(
        role=RobotRole.LINE_ARM,
        arm_action="/omxf_line_01/arm_control",  # TODO placeholder
        gripper_action="/omxf_line_01/gripper_controller/gripper_cmd",  # TODO placeholder
    ),
    # L2/L3(시뮬레이션 로봇: omxf-storage-02/03, beagle-02/03, omxf-line-02/03)는
    # ROS2 실물 노드가 없다면 이 맵에 넣지 않는다 — bridge_node가 매핑 없는
    # robotId의 커맨드는 무시하고 경고만 남긴다 (실기 L1만 우선 연결).
}


def get_topics(robot_id: str) -> RobotTopics | None:
    return ROBOT_TOPICS.get(robot_id)
