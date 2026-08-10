# Hardware — 로봇(ROS2) ↔ 백엔드(MQTT) 브리지

`Team1SmartFactory` 스마트 팩토리 재고 관리 시스템의 세 번째 축. 지금까지 있던
두 시스템—**대시보드 백엔드**(FastAPI, MQTT 기반)와 **로봇 제어**(ROS2, YOLO 비전)—가
서로 다른 저장소에서 따로 개발되면서 아직 소프트웨어적으로 이어져 있지 않다.
이 레포는 그 둘을 잇는 **MQTT ↔ ROS2 브리지**를 담는다.

```
[비전(YOLO) + 로봇 제어(ROS2)]  ──?──  [이 레포: mqtt_bridge]  ──MQTT──  [Team1SmartFactory/Backend]  ──REST/WS──  [Team1SmartFactory/Frontend]
        기존 별도 저장소                    지금 여기, 스켈레톤 상태              계약: docs/COMMAND_SCHEMA.md
```

## 지금 상태 (2026-08-10 기준)

- **스켈레톤 단계.** MQTT 송수신 배관(연결, 구독, 커맨드 파싱·라우팅)까지는
  동작 확인됨(`pytest`로 검증). **실제 ROS2 토픽/액션과의 연결은 전부 TODO.**
- 이 상태로 그냥 띄우면: Backend가 커맨드를 보내도 아무 일도 안 일어나고, 60초
  뒤 Backend 쪽에서 타임아웃으로 실패 처리된다. (지금 실제로 벌어지고 있는
  상황과 동일 — 이 레포가 그 간극을 메우기 위해 생겼다.)
- 로봇 제어(ROS2)/비전(YOLO) 쪽 실제 코드는 이 레포에 없다. 원래 참고하려던
  저장소 URL(`jisooohh/SmartFactoryStockControl`)이 현재 `Team1SmartFactory/Frontend`로
  리다이렉트되는 걸 확인함 — **정지우 팀장님께 YOLO/ROS2 코드의 실제 현재 위치를
  확인 필요.** 확인되면 이 README와 `mqtt_bridge/mqtt_bridge/topic_map.py`를
  갱신할 것.

## 먼저 볼 문서

**[`docs/COMMAND_SCHEMA.md`](docs/COMMAND_SCHEMA.md)** — 로봇과 백엔드가 주고받는
MQTT 메시지 계약 전체(토픽, 페이로드, 상태 전이). Backend 레포 코드가 계속
참조하는데 정작 Backend에는 없던 문서라, 여기를 원본으로 둔다. **백엔드
계약이 바뀌면 이 문서부터 갱신.**

## 구조

```
Hardware/
├── docs/
│   └── COMMAND_SCHEMA.md          # MQTT 계약 (원본)
└── mqtt_bridge/                   # ROS2 ament_python 패키지
    ├── package.xml / setup.py / setup.cfg
    ├── mqtt_bridge/
    │   ├── contracts.py           # Backend와 동일한 메시지 모델 (pydantic, rclpy 불필요)
    │   ├── mqtt_link.py           # paho-mqtt 얇은 래퍼 (rclpy 불필요)
    │   ├── topic_map.py           # ★ robotId -> 실제 ROS2 토픽/액션 매핑 (TODO 채울 곳)
    │   └── bridge_node.py         # rclpy.Node 본체 — MQTT<->ROS2 라우팅
    ├── launch/bridge.launch.py
    ├── config/bridge_config.example.yaml
    └── test/test_contracts.py     # rclpy 없이 도는 단위 테스트
```

## 채워야 할 것 (우선순위 순)

1. **`mqtt_bridge/topic_map.py`** — 정지우 팀장님 ROS2 쪽 실제 토픽/액션 이름으로
   교체. 지금 들어있는 값(`/beagle_01/goal`, `/beagle_01/beagle_arrived`,
   `/omxf_storage_01/arm_control` 등)은 전부 주간보고서에 언급된 노드 이름에서
   유추한 추정값이다.
2. **`mqtt_bridge/bridge_node.py`의 `_dispatch_*` 메서드 4개** — 각 메서드
   안에 실제 ROS2 publish/action call을 넣고, 결과 콜백에서
   `self._publish_done(command)` 또는 `self._publish_failed(command, ...)` 호출.
3. **`line/{lineId}/inventory` 발행** — 이 레포 범위 밖(§12 COMMAND_SCHEMA.md
   참고). 비전(YOLO) 쪽이 직접 발행하거나 별도로 이 브리지에 합류시킬지 결정 필요.
4. Backend 쪽에도 별도로 채워야 할 게 있음 — INVENTORY 수신 시 임계치 이하로
   떨어지면 자동으로 `pending_approval` 이벤트를 만드는 로직이 아직 없음
   (Backend 레포에 이슈 등록 예정, 이 레포 작업과는 별개).

## 개발 환경

### ROS2 없이 계약 부분만 테스트

```bash
pip install -r requirements-dev.txt
pytest mqtt_bridge/test/test_contracts.py
```

### ROS2 환경에서 브리지 빌드·실행

```bash
# colcon 워크스페이스의 src/ 밑에 이 mqtt_bridge/ 디렉토리를 두고
colcon build --packages-select mqtt_bridge
source install/setup.bash
ros2 launch mqtt_bridge bridge.launch.py mqtt_host:=localhost mqtt_port:=1883
```

`mqtt_host`/`mqtt_port`는 **`Team1SmartFactory/Backend`의 `MQTT_BROKER_HOST`/
`MQTT_BROKER_PORT`와 반드시 같은 브로커**를 가리켜야 한다 (Backend 개발 환경은
`docker-compose`로 로컬 Mosquitto를 띄움, Backend README 참고).

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| [Team1SmartFactory/Backend](https://github.com/Team1SmartFactory/Backend) | FastAPI 대시보드 백엔드. MQTT 계약의 "다른 쪽 끝" |
| [Team1SmartFactory/Frontend](https://github.com/Team1SmartFactory/Frontend) | 대시보드 화면 |
| 로봇 제어(ROS2)/비전(YOLO) | 저장소 위치 확인 중 (위 "지금 상태" 참고) |
