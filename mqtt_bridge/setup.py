from setuptools import find_packages, setup

package_name = "mqtt_bridge"

setup(
    name=package_name,
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/bridge.launch.py"]),
        ("share/" + package_name + "/config", ["config/bridge_config.example.yaml"]),
    ],
    install_requires=["setuptools", "paho-mqtt>=2.1.0,<3", "pydantic>=2,<3"],
    zip_safe=True,
    maintainer="jam0629",
    maintainer_email="a691285@gmail.com",
    description="Team1SmartFactory 로봇(ROS2) <-> 백엔드(MQTT) 브리지. docs/COMMAND_SCHEMA.md 참고.",
    license="TODO",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "bridge_node = mqtt_bridge.bridge_node:main",
        ],
    },
)
