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


def test_destination_to_station_maps_storage_and_all_lines():
    """ROS2_WIRING.md §3 MOVE_TO: STORAGE->station_a, 어느 라인이든 station_b."""
    assert DESTINATION_TO_STATION["STORAGE"] == "station_a"
    for line_id in ("line-a", "line-b", "line-c", "line-d", "line-e", "line-f"):
        assert DESTINATION_TO_STATION[line_id] == "station_b"


def test_line_to_bin_only_covers_physical_bins():
    """물리 칸은 bin_a~d 4개뿐 — line-e/line-f는 2026-08-21 확정대로 매핑에서 뺐다
    (bridge_node가 이걸 보고 UNSUPPORTED로 실패시켜야 한다)."""
    assert LINE_TO_BIN == {
        "line-a": "bin_a",
        "line-b": "bin_b",
        "line-c": "bin_c",
        "line-d": "bin_d",
    }
    assert "line-e" not in LINE_TO_BIN
    assert "line-f" not in LINE_TO_BIN


def test_robot_topics_registry_matches_backend_registry_robot_ids():
    """topic_map.py의 키 3개는 Backend config/registry.yaml의 실기 robotId와
    일치해야 브리지가 커맨드를 알아본다."""
    assert set(ROBOT_TOPICS) == {"omxf-storage-01", "beagle-01", "omxf-line-01"}
