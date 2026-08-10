"""MQTT 브로커 연결 래퍼. Backend의 app/mqtt/client.py와 같은 스타일로 맞췄다
(브리지와 백엔드가 나중에 코드를 비교하기 쉽게).

rclpy에 의존하지 않는다 — bridge_node.py가 이 클래스를 rclpy.Node 안에서 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import paho.mqtt.client as mqtt


class MqttLink:
    def __init__(self, host: str, port: int, client_id: str = "hardware-bridge") -> None:
        self.host = host
        self.port = port
        self._client = mqtt.Client(
            client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )
        self._connected = False
        self._subscriptions: list[tuple[str, int]] = []
        self.on_message_callback: Callable[[str, bytes], None] | None = None

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = reason_code == 0
        if self._connected:
            for topic, qos in self._subscriptions:
                self._client.subscribe(topic, qos=qos)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = False

    def _on_message(self, client, userdata, msg) -> None:
        if self.on_message_callback is not None:
            self.on_message_callback(msg.topic, msg.payload)

    def set_last_will(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        """연결이 비정상 종료되면 브로커가 대신 발행해줄 메시지 (COMMAND_SCHEMA.md 9장 online).

        connect() 호출 전에 등록해야 한다.
        """
        self._client.will_set(topic, json.dumps(payload), qos=qos, retain=retain)

    def subscribe(self, topic: str, qos: int = 1) -> None:
        self._subscriptions.append((topic, qos))
        if self._connected:
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: dict, qos: int = 1, retain: bool = False) -> None:
        self._client.publish(topic, json.dumps(payload), qos=qos, retain=retain)

    def connect(self) -> None:
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
