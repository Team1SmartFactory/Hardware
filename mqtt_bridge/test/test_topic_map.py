"""topic_map.py 단독 테스트. rclpy 없이 돌아간다 (pytest 로만 실행 가능):

    pip install -r requirements-dev.txt
    pytest mqtt_bridge/test/test_topic_map.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mqtt_bridge.contracts import RobotRole  # noqa: E402
from mqtt_bridge.topic_map import (  # noqa: E402
    DESTINATION_TO_STATION,
    LINE_TO_BIN,
    ROBOT_TOPICS,
    get_topics,
)


def test_get_topics_returns_none_for_unknown_robot_id():
    """omxf-line-02(PC2)는 아직 티칭 미완이라 맵에 없다 — bridge_node가 이걸 보고
    경고만 남기고 무시해야 한다."""
    assert get_topics("omxf-line-02") is None
    assert get_topics("does-not-exist") is None


def test_storage_arm_uses_transfer_state_topics_not_raw_action():
    """ROS2_WIRING.md §1: 팔은 raw Action이 아니라 태스크 매니저 transfer/state."""
    topics = get_topics("omxf-storage-01")
    assert topics is not None
    assert topics.role == RobotRole.STORAGE_ARM
    assert topics.transfer_topic == "/station_a/stock/transfer"
    assert topics.state_topic == "/station_a/stock/task_state"


def test_line_arm_uses_station_b():
    topics = get_topics("omxf-line-01")
    assert topics is not None
    assert topics.role == RobotRole.LINE_ARM
    assert topics.transfer_topic == "/station_b/stock/transfer"
    assert topics.state_topic == "/station_b/stock/task_state"


def test_beagle_has_goal_state_and_estop_topics():
    topics = get_topics("beagle-01")
    assert topics is not None
    assert topics.role == RobotRole.AMR
    assert topics.goal_topic == "/beagle/goto"
    assert topics.beagle_state_topic == "/beagle/state"
    assert topics.estop_topic == "/beagle/estop"


def test_destination_to_station_covers_only_line_a():
    """이슈 #17: line-a만 실물로 쓰기로 확정 — registry.yaml이 line-b~f를 가상
    robotId로 라우팅해서 이 브리지엔 애초에 도달하지 않는다. 여기 없는
    destination은 UNSUPPORTED로 명시적으로 거부돼야 한다(조용히 받으면 안 됨)."""
    assert DESTINATION_TO_STATION == {
        "STORAGE": "station_a",
        "line-a": "station_b",
    }
    for line_id in ("line-b", "line-c", "line-d", "line-e", "line-f"):
        assert line_id not in DESTINATION_TO_STATION


def test_line_to_bin_covers_only_line_a():
    """이슈 #17: line-a만 실물로 쓰기로 확정. bin_b는 station_b의 같은 팔이
    물리적으로 닿을 수 있는 칸이지만, registry.yaml이 line-b를 아직 실물로 안
    돌리고 있어서 의도적으로 뺐다(팔이 안 닿아서가 아님)."""
    assert LINE_TO_BIN == {"line-a": "bin_a"}
    for line_id in ("line-b", "line-c", "line-d", "line-e", "line-f"):
        assert line_id not in LINE_TO_BIN


def test_robot_topics_registry_matches_backend_registry_robot_ids():
    """topic_map.py의 키 3개는 Backend config/registry.yaml의 실기 robotId와
    일치해야 브리지가 커맨드를 알아본다."""
    assert set(ROBOT_TOPICS) == {"omxf-storage-01", "beagle-01", "omxf-line-01"}
