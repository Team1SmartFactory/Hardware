"""브리지 노드 실행용 launch 파일.

사용:
    ros2 launch mqtt_bridge bridge.launch.py
    ros2 launch mqtt_bridge bridge.launch.py mqtt_host:=192.168.0.10 mqtt_port:=1883
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    mqtt_host_arg = DeclareLaunchArgument(
        "mqtt_host",
        default_value="localhost",
        description="MQTT 브로커 호스트 (Backend의 MQTT_BROKER_HOST와 동일해야 함)",
    )
    mqtt_port_arg = DeclareLaunchArgument(
        "mqtt_port",
        default_value="1883",
        description="MQTT 브로커 포트",
    )

    bridge_node = Node(
        package="mqtt_bridge",
        executable="bridge_node",
        name="mqtt_bridge",
        output="screen",
        parameters=[
            {
                "mqtt_host": LaunchConfiguration("mqtt_host"),
                "mqtt_port": LaunchConfiguration("mqtt_port"),
            }
        ],
    )

    return LaunchDescription([mqtt_host_arg, mqtt_port_arg, bridge_node])
