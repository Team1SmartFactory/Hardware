# Hardware — 로봇(ROS2) ↔ 백엔드(MQTT) 브리지

`Team1SmartFactory` 스마트 팩토리 재고 관리 시스템의 세 번째 축. 지금까지 있던
두 시스템—**대시보드 백엔드**(FastAPI, MQTT 기반)와 **로봇 제어**(ROS2, YOLO 비전)—가
서로 다른 저장소에서 따로 개발되면서 아직 소프트웨어적으로 이어져 있지 않다.
이 레포는 그 둘을 잇는 **MQTT ↔ ROS2 브리지**를 담는다.

```
[비전(YOLO) + 로봇 제어(ROS2)]  ──?──  [이 레포: mqtt_bridge]  ──MQTT──  [Team1SmartFactory/Backend]  ──REST/WS──  [Team1SmartFactory/Frontend]
        기존 별도 저장소                    지금 여기, 스켈레톤 상태              계약: docs/COMMAND_SCHEMA.md
        아직 연결 안 됨                     scripts/mock_*.py로 임시 대체 중
```

## 지금 상태 (2026-08-13 기준)

- **진짜 ROS2 연동은 아직 스켈레톤 단계** (`mqtt_bridge/`) — 커맨드 파싱·라우팅
  배관은 동작하지만, 실제 ROS2 토픽/액션과의 연결은 전부 TODO.
- **대신 `scripts/mock_robot.py` + `scripts/mock_vision.py`로 Backend와의 MQTT
  왕복 전체를 실제로 검증 완료.** 로컬 Mosquitto + Backend + 두 mock 스크립트를
  같이 띄우고, `PUT /api/lines/{id}/stock`(관리자 수동 지정)으로 부족 이벤트를
  만들었더니 PICK_LOAD → MOVE_TO → UNLOAD_RESUME → MOVE_TO(복귀) 4단계가 전부
  mock 로봇 응답으로 끝까지 진행되고, 라인이 `restocking` → `normal`로 정확히
  복귀하는 것까지 확인함. 재고(`line/{id}/inventory`) 경로도 currentQty 갱신·
  이력 DB 적재·WS 브로드캐스트까지 정상 동작 확인. (자세한 건 "빠른 시작" 참고)
- 즉 **연결 배관 자체는 증명됐고**, 남은 건 `mock_robot.py`/`mock_vision.py`를
  실제 ROS2/YOLO로 바꿔치기하는 것뿐이다.
- ⚠️ `line/{id}/inventory`는 mock으로 흘려보내도 currentQty만 갱신될 뿐, 임계치
  이하로 떨어져도 승인 이벤트가 자동 생성되지는 않는다 — Backend에 그 로직
  자체가 아직 없음(별도 gap, 이 레포 범위 밖).
- 로봇 제어(ROS2)/비전(YOLO) 쪽 실제 코드는 이 레포에 없다 — 위치 **확인 완료
  (2026-08-20)**: `github.com/noeyod02/omx-beagle-smart-factory`의
  `open_manipulator_playground` 패키지다 (PC1 `/home/itec/open_manipulator`,
  PC2 `~/ros2_ws/src/open_manipulator`). 실제 토픽/노드 기준의 배선 계획은
  **[`docs/ROS2_WIRING.md`](docs/ROS2_WIRING.md)** 로 정리했다 — topic_map.py의
  추정값(raw 액션 직접 호출)은 그 문서대로 태스크 매니저 계층으로 바꿔야 한다.

## 빠른 시작 — mock으로 Backend 연결 왕복 검증

ROS2/실제 하드웨어 없이, Backend와의 MQTT 왕복이 되는지 지금 바로 확인할 수 있다.

```bash
# 0. 로컬 MQTT 브로커 (brew install mosquitto 또는 Backend의 docker-compose)
mosquitto -c /path/to/mosquitto.conf   # listener 1883, allow_anonymous true

# 1. Backend (별도 터미널, Team1SmartFactory/Backend 레포에서)
uvicorn app.main:app --port 8000

# 2. 이 레포에서 — 모의 로봇 + 모의 비전 동시 실행 (각각 별도 터미널)
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
python3 scripts/mock_robot.py
python3 scripts/mock_vision.py

# 3. 부족 이벤트를 하나 만들어서 4단계 전부 도는지 확인
curl -X PUT http://localhost:8000/api/lines/L1/stock \
  -H "Content-Type: application/json" -d '{"verdict":"shortage","by":"관리자"}'
# mock_robot.py 터미널에 PICK_LOAD -> MOVE_TO -> UNLOAD_RESUME -> MOVE_TO 순으로
# 커맨드가 찍히고, 몇 초 뒤 GET /api/snapshot에서 L1.status가 다시 normal이면 성공.
```

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
├── scripts/                       # 임시 mock — 실제 ROS2/YOLO 준비되면 걷어낼 것
│   ├── mock_robot.py              # robot/+/cmd에 ACCEPTED->DONE으로 자동 응답 (rclpy 불필요)
│   └── mock_vision.py             # line/{id}/inventory 주기 발행 (rclpy 불필요)
└── mqtt_bridge/                   # ROS2 ament_python 패키지 (진짜 브리지, 아직 스켈레톤)
    ├── package.xml / setup.py / setup.cfg
    ├── mqtt_bridge/
    │   ├── contracts.py           # Backend와 동일한 메시지 모델 (pydantic, rclpy 불필요)
    │   ├── mqtt_link.py           # paho-mqtt 얇은 래퍼 (rclpy 불필요) — scripts/도 이걸 재사용
    │   ├── topic_map.py           # ★ robotId -> 실제 ROS2 토픽/액션 매핑 (TODO 채울 곳)
    │   └── bridge_node.py         # rclpy.Node 본체 — MQTT<->ROS2 라우팅
    ├── launch/bridge.launch.py
    ├── config/bridge_config.example.yaml
    └── test/test_contracts.py     # rclpy 없이 도는 단위 테스트
```

**`scripts/` vs `mqtt_bridge/` 구분**: `scripts/`는 ROS2/실제 로봇 없이 Backend 연결만
먼저 검증하려고 만든 임시 mock이다(이 파일들 자체가 최종 산출물이 아님). 진짜
구현은 `mqtt_bridge/bridge_node.py`이고, ROS2 쪽이 준비되면 `scripts/`는 지우고
`bridge_node.py`의 TODO를 채우는 게 목표다.

## 채워야 할 것 (우선순위 순)

1. **`mqtt_bridge/topic_map.py`** — 정지우 팀장님 ROS2 쪽 실제 토픽/액션 이름으로
   교체. 지금 들어있는 값(`/beagle_01/goal`, `/beagle_01/beagle_arrived`,
   `/omxf_storage_01/arm_control` 등)은 전부 주간보고서에 언급된 노드 이름에서
   유추한 추정값이다.
2. **`mqtt_bridge/bridge_node.py`의 `_dispatch_*` 메서드 4개** — 각 메서드
   안에 실제 ROS2 publish/action call을 넣고, 결과 콜백에서
   `self._publish_done(command)` 또는 `self._publish_failed(command, ...)` 호출.
3. **`line/{lineId}/inventory` 발행** — 지금은 `scripts/mock_vision.py`가 대신
   흘려보내고 있음(임시). 비전(YOLO) 쪽이 직접 발행하거나 별도로 이 브리지에
   합류시킬지 결정 필요 — 이 레포 범위 밖(§12 COMMAND_SCHEMA.md 참고).
4. Backend 쪽에도 별도로 채워야 할 게 있음 — INVENTORY 수신 시 임계치 이하로
   떨어지면 자동으로 `pending_approval` 이벤트를 만드는 로직이 아직 없음
   (Backend 레포에 이슈 등록 예정, 이 레포 작업과는 별개).
5. 위 1~2가 끝나면 `scripts/mock_robot.py`·`scripts/mock_vision.py`는 삭제.

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
| [noeyod02/omx-beagle-smart-factory](https://github.com/noeyod02/omx-beagle-smart-factory) | 로봇 제어(ROS2)/비전(YOLO). 배선 계획: [docs/ROS2_WIRING.md](docs/ROS2_WIRING.md) |
