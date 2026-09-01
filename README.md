# Hardware — 로봇(ROS2) ↔ 백엔드(MQTT) 브리지

`Team1SmartFactory` 스마트 팩토리 재고 관리 시스템의 세 번째 축. 지금까지 있던
두 시스템—**대시보드 백엔드**(FastAPI, MQTT 기반)와 **로봇 제어**(ROS2, YOLO 비전)—가
서로 다른 저장소에서 따로 개발되면서 아직 소프트웨어적으로 이어져 있지 않다.
이 레포는 그 둘을 잇는 **MQTT ↔ ROS2 브리지**를 담는다.

```
[비전(CV) + 로봇 제어(ROS2)]  ──MQTT(실물 검증 완료)──  [이 레포: mqtt_bridge]  ──MQTT──  [Team1SmartFactory/Backend]  ──REST/WS──  [Team1SmartFactory/Frontend]
  noeyod02/omx-beagle-smart-factory                     _dispatch_*/readiness/condition             계약: docs/COMMAND_SCHEMA.md
  station-a 도착·칸 재고 판정 실물 검증 완료             중계 구현 완료, 실물 검증 완료               연결 배관 실물 검증 완료
```

## 지금 상태 (2026-09-01 기준)

- **station A 비글 존재 확인 + line-a 칸(bin) 자동 부족 감지, 실물 검증까지 완료.**
  비전 저장소(`noeyod02/omx-beagle-smart-factory`)가 `/stock/status`(칸 filled/empty)와
  `/stock/station_a_ready`(`{beagle, part}` 체크)를 발행하면, 이 레포의 `bridge_node.py`가
  안정화 필터링 후 각각 `line/{lineId}/bin/{label}/inventory`와
  `station/{stationId}/readiness`로 MQTT 중계 → Backend가 칸 단위 `ShortageEvent`
  자동 생성 + 승인 직전 station 준비 상태 게이팅(`app/core/readiness.py`) → Frontend
  반영까지 전부 실물 로봇/카메라로 검증 완료. 정확도도 실사용 기준으로 충분한 것으로
  확인됨(2026-09-01).
- **`_dispatch_*` 4개 + `RESUME`(멈춘 팔 복구) + `CONDITION`(blocked 보고)** 모두
  실물 ROS2 환경/실물 로봇으로 검증 완료 — 더 이상 단위 테스트뿐인 상태가 아님.
- **`scripts/mock_robot.py` + `scripts/mock_vision.py`로 Backend와의 MQTT
  왕복 전체를 실제로 검증 완료.** 로컬 Mosquitto + Backend + 두 mock 스크립트를
  같이 띄우고, `PUT /api/lines/{id}/stock`(관리자 수동 지정)으로 부족 이벤트를
  만들었더니 PICK_LOAD → MOVE_TO → UNLOAD_RESUME → MOVE_TO(복귀) 4단계가 전부
  mock 로봇 응답으로 끝까지 진행되고, 라인이 `restocking` → `normal`로 정확히
  복귀하는 것까지 확인함. 재고(`line/{id}/inventory`) 경로도 currentQty 갱신·
  이력 DB 적재·WS 브로드캐스트까지 정상 동작 확인. (자세한 건 "빠른 시작" 참고,
  단 line-a는 이제 실물 칸 단위 검증이 끝나 mock 없이도 실동작함 — mock은 시뮬
  라인(line-b~f)용으로 계속 유효)
- INVENTORY 임계치 이하 감지 시 승인 대기 이벤트 자동 생성도 Backend 쪽에 구현·
  라이브 검증 완료 — 관리자 수동 지정 없이도 부족 감지부터 로봇 4단계까지
  전부 자동으로 도는 것까지 확인함.
- 로봇 제어(ROS2)/비전(CV) 쪽 실제 코드는 이 레포에 없다 — 위치 **확인 완료
  (2026-08-20)**: `github.com/noeyod02/omx-beagle-smart-factory`의
  `open_manipulator_playground` 패키지다 (PC1 `/home/itec/open_manipulator`,
  PC2 `~/ros2_ws/src/open_manipulator`). 실제 토픽/노드 기준의 배선 계획은
  **[`docs/ROS2_WIRING.md`](docs/ROS2_WIRING.md)** 로 정리했다. 참고로 재고/도착
  판정은 YOLO 객체 탐지가 아니라 **면적비 기반 CV 판정**(`InventorySource.CV_AREA`)이다.
- station B/C용 readiness는 아직 배선 전 — 지금은 station-a만 하드코딩 구독
  (`bridge_node.py`의 `STATION_READY_TOPIC`). 필요해지면 그때 확장.

## 빠른 시작 — mock으로 Backend 연결 왕복 검증

ROS2/실제 하드웨어 없이, Backend와의 MQTT 왕복이 되는지 지금 바로 확인할 수 있다.

```bash
# 0. 로컬 MQTT 브로커 — 데모 환경(우분투, FE/BE/HW 전부 로컬 동일 머신)이면
#    scripts/setup_mosquitto.sh 하나로 설치+설정+왕복 테스트까지 끝난다.
#    (다른 OS에서 개발 중이면 brew install mosquitto 등으로 직접 띄우고
#    listener 1883 / allow_anonymous true만 맞추면 된다 — 인증을 쓰지 않는다)
./scripts/setup_mosquitto.sh

# 1. Backend (별도 터미널, Team1SmartFactory/Backend 레포에서)
uvicorn app.main:app --port 8000

# 2. 이 레포에서 — 모의 로봇 + 모의 비전 동시 실행 (각각 별도 터미널)
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
python3 scripts/mock_robot.py --all   # --all: 실기 robotId까지 응답 (브리지 없이 단독 검증할 때만!)
python3 scripts/mock_vision.py

# 3. 부족 이벤트를 하나 만들어서 4단계 전부 도는지 확인
curl -X PUT http://localhost:8000/api/lines/line-a/stock \
  -H "Content-Type: application/json" -d '{"verdict":"shortage","by":"관리자"}'
# mock_robot.py 터미널에 PICK_LOAD -> MOVE_TO -> UNLOAD_RESUME -> MOVE_TO 순으로
# 커맨드가 찍히고, 몇 초 뒤 GET /api/snapshot에서 line-a의 status가 다시 normal이면 성공.
```

## 먼저 볼 문서

- **[`docs/COMMAND_SCHEMA.md`](docs/COMMAND_SCHEMA.md)** — 로봇/비전과 백엔드가 주고받는
  MQTT 메시지 계약 전체(토픽·retain·QoS 총괄표, 페이로드, 수신측 방어 규약). 이 계약의
  **원본(단일 진실)**. 계약이 바뀌면 이 문서부터 개정하고 양측 contracts 코드를 동기 반영.
- **[`docs/CONNECTION_PLAN.md`](docs/CONNECTION_PLAN.md)** — HW↔BE 연결 실행 계획.
  3자 자문 검토의 확정 결정·쟁점 판정·실행 순서(Phase 0~4)·과설계 금지 목록.
- 팀 공통 개발 규칙은 `Team1SmartFactory/Backend`의 `docs/PROJECT_RULES.md` 참고.

## 구조

```
Hardware/
├── docs/
│   └── COMMAND_SCHEMA.md          # MQTT 계약 (원본)
├── scripts/
│   ├── setup_mosquitto.sh         # 데모 환경(우분투) MQTT 브로커 설치+설정+검증 (몇 번 돌려도 안전)
│   ├── mock_robot.py              # 시뮬 로봇(line-b~f) 응답기 — 기본값은 실기 robotId 무시, --all로 전체 응답 (rclpy 불필요)
│   └── mock_vision.py             # 임시 mock — line/{id}/inventory 주기 발행 (rclpy 불필요)
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

1. ~~`mqtt_bridge/topic_map.py`~~ — **완료 (2026-08-21)**. `docs/ROS2_WIRING.md`
   기준 실제 토픽으로 교체함 (팔은 raw Action이 아니라 스테이션 태스크 매니저의
   transfer/state 토픽).
2. ~~`mqtt_bridge/bridge_node.py`의 `_dispatch_*` 메서드 4개~~ — **완료
   (2026-08-21), 실물 ROS2 환경/실물 로봇 검증까지 완료 (2026-09-01)**,
   `docs/ROS2_WIRING.md` §3 사양대로 구현함.
3. ~~`line/{lineId}/bin/{label}/inventory`·`station/{stationId}/readiness` 발행~~
   — **완료, 실물 검증 완료 (2026-09-01)**. 로봇 저장소의 `stock_monitor_node`/
   `stock_arrival_node`가 실제 카메라 캘리브레이션을 마치고 칸 filled/empty·
   station-a beagle 도착 여부를 발행 중이며, 정확도도 실사용 기준 충분함을 확인함.
   line-a 외 시뮬 라인(line-b~f)의 `line/{lineId}/inventory`는 여전히
   `scripts/mock_vision.py`가 대신 흘려보낸다(의도된 동작 — 실물이 없는 라인이므로).
4. ~~`scripts/mock_robot.py` 삭제~~ → 시뮬 라인(line-b~f)의 가상 robotId 응답을
   계속 맡아야 해서 삭제 대신 **실기 robotId 제외가 기본값**이 되도록 스코프를
   줄였다(이슈 #15) — 실기 브리지와 같이 띄워도 안전하며, 계속 유지한다.
5. station B/C용 readiness 배선 — 필요해지면 진행 (현재는 station-a만 지원,
   범위 밖 아님/우선순위 낮음으로 보류).

⚠️ **운영 규칙**: 대시보드 연동 모드에서는 로봇 저장소의 `/stock/refill_request`를
아무도 발행하면 안 된다 — Backend orchestrator와 로봇 저장소의
`stock_relay_node`가 같은 일을 해서, 지휘자가 둘이 되면 태스크 매니저에 transfer가
겹쳐 들어가 한쪽이 죽는다(ROS2_WIRING.md §4).

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

### 우분투 데모 환경 — FE/BE/HW를 한 머신에 로컬로 띄우기

실제 데모는 로봇 제어(ROS2)/비전(YOLO)이 우분투에서 돌기 때문에, 이 브리지도 같은
우분투 머신에서 실행해야 한다(`mqtt_bridge`가 `rclpy` 의존이라 다른 OS에서는 애초에
안 돌아간다). Backend/Frontend는 OS를 가리지 않으므로 같은 머신에 같이 올리는 게
가장 단순하다 — Backend·Frontend 코드도 이미 `localhost` 기준으로 설정돼 있어
따로 고칠 게 없다.

```bash
# 1. MQTT 브로커 (최초 1회, 이후엔 systemd가 자동 기동)
./scripts/setup_mosquitto.sh

# 2. Backend
cd ../Backend && .venv/bin/uvicorn app.main:app --port 8000

# 3. 이 레포 — ROS2 브리지
colcon build --packages-select mqtt_bridge && source install/setup.bash
ros2 run mqtt_bridge bridge_node

# 4. Frontend
cd ../Frontend/src/dashboard-frontend && npm run dev
```

브리지가 뜨면 `bridge/online:true`가 자동 발행되고 Backend 로그에 로봇들이 idle로
잡히면 정상 연결이다. `mosquitto_sub -h localhost -t '#' -v`로 전체 토픽을 실시간
훑어보면 뭐가 오가는지 바로 보여서 디버깅에 유용하다.

## 관련 저장소

| 저장소 | 역할 |
|---|---|
| [Team1SmartFactory/Backend](https://github.com/Team1SmartFactory/Backend) | FastAPI 대시보드 백엔드. MQTT 계약의 "다른 쪽 끝" |
| [Team1SmartFactory/Frontend](https://github.com/Team1SmartFactory/Frontend) | 대시보드 화면 |
| [noeyod02/omx-beagle-smart-factory](https://github.com/noeyod02/omx-beagle-smart-factory) | 로봇 제어(ROS2)/비전(YOLO). 배선 계획: [docs/ROS2_WIRING.md](docs/ROS2_WIRING.md) |
