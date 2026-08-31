# ROS2_WIRING — mqtt_bridge를 실제 로봇 시스템에 잇는 구현 계획

> 2026-08-20, 로봇 제어(ROS2) 쪽에서 작성. topic_map.py의 추정값들을 실제 값으로
> 확정하고, `_dispatch_*` 4개를 어떻게 채울지 정한다. README의 "채워야 할 것"
> 1~2번이 이 문서의 범위다.

## 0. 로봇 제어(ROS2) 코드의 실제 위치 — 확인 끝

README에서 "정지우 팀장님께 확인 필요"라던 저장소는
**`github.com/noeyod02/omx-beagle-smart-factory`(브랜치 `main`)** 이다.
작업 사본은 PC1 `/home/itec/open_manipulator`(브랜치 `feature-stock-relay`,
main으로 푸시됨), PC2 `~/ros2_ws/src/open_manipulator`.
아래 모든 토픽/노드는 그 저장소의 `open_manipulator_playground` 패키지 것이다.

## 1. 제일 중요한 설계 사실: 팔은 "액션"이 아니라 "태스크 매니저"로 부린다

topic_map.py는 팔마다 `arm_action`(FollowJointTrajectory) + `gripper_action`을
추정해 뒀는데, **그 계층으로 내려가면 안 된다.** 실제 시스템에는 스테이션마다
`stock_task_manager_node`가 상주하며, 티칭된 좌표·경유점·후퇴 고도·그리퍼 폭을
전부 알고 "어디서 집어 어디에 놓아라" 한 문장짜리 transfer를 12~13스텝으로
수행한다. 브리지가 raw 액션으로 관절을 직접 던지면 이 안전장치를 전부 우회하게
되고, 같은 컨트롤러에 커맨더가 둘이 되는 순간 goal끼리 CANCELED로 서로 죽인다
(실사고 전력 있음).

따라서 `RobotTopics`는 팔에 대해 이렇게 바뀌어야 한다:

```python
@dataclass(frozen=True)
class RobotTopics:
    role: RobotRole
    # 팔(STORAGE_ARM/LINE_ARM): 태스크 매니저의 transfer/state 토픽 (std_msgs/String, JSON)
    transfer_topic: str | None = None   # 발행: {"from": ..., "to": ..., "id": ...}
    state_topic: str | None = None      # 구독: {"state", "job", "last_job": {...}, ...}
    # AMR(Beagle)
    goal_topic: str | None = None       # 발행: 스테이션 이름 (std_msgs/String)
    beagle_state_topic: str | None = None  # 구독: {"state","station","ready_for_arm",...}
    estop_topic: str | None = None
```

## 2. 실제 값 (topic_map.py에 들어갈 것)

| robotId | 실제 연결 | 비고 |
|---|---|---|
| `omxf-storage-01` | transfer `/station_a/stock/transfer`, state `/station_a/stock/task_state` | 보관소 팔 (PC1) |
| `beagle-01` | goal `/beagle/goto`, state `/beagle/state`, estop `/beagle/estop` | 브릿지 노드는 Docker 컨테이너에서 상주 |
| `omxf-line-01` | transfer `/station_b/stock/transfer`, state `/station_b/stock/task_state` | 라인 팔 (PC1), 셀 `bin_a`/`bin_b` 담당 |
| `omxf-line-07` | transfer `/station_c/stock/transfer`, state `/station_c/stock/task_state` | 라인 팔 (PC2), 셀 `bin_c`/`bin_d`. 2026-08-31 티칭 완료로 합류 |

> `omxf-line-07`인 이유: 이 표는 원래 `omxf-line-02`를 예약해 뒀지만, 그 robotId는
> Backend `config/registry.yaml`에서 시뮬 line-b의 팔이 이미 쓰고 있다. robotId는
> 전역 유일해야 하고 겹치면 line-b로 간 커맨드가 실물 팔을 움직인다 — 시뮬이
> 점유한 02~06을 피해 07로 부여했다.

토픽 타입은 `/beagle/estop`만 `std_msgs/Bool`이고 나머지는 전부
`std_msgs/String`(JSON 문자열)이다. ROS2 환경은 도메인 0,
기본 rmw(fastrtps) — PC1/PC2 크로스머신 통신은 검증돼 있고, 브리지는 PC1
호스트에서 돌리면 된다 (⚠️ 컨테이너 안에서 돌리면 RMW_IMPLEMENTATION=zenoh /
ROS_DOMAIN_ID=30 기본값 때문에 아무것도 안 보인다 — 컨테이너에서 돌려야 한다면
`unset RMW_IMPLEMENTATION; export ROS_DOMAIN_ID=0 FASTDDS_BUILTIN_TRANSPORTS=UDPv4`).

## 3. 커맨드 번역 (`_dispatch_*` 구현 사양)

### PICK_LOAD (STORAGE_ARM)

```
발행: /station_a/stock/transfer  {"from": "warehouse", "to": "carrier", "id": <commandId>}
```

완료 판정: `/station_a/stock/task_state`의 `last_job.id == commandId`가 되는
순간 — `result: "ok"` → DONE(`LOADED`), `"failed"`/`"rejected"` → FAILED
(`last_job.error`를 message로). 태스크 매니저는 요청에 실은 `id`를 결과에
그대로 echo하므로 commandId 왕복 방어가 공짜로 된다. `state`가 `busy`인 동안
RUNNING을 흘려보내면 된다.

주의: A의 warehouse pick_points는 **소모 예산**(런치당 2회, 사람이 부품을
재보급)이다. 소진되면 `last_job.result: "failed"` + "the warehouse is empty"로
오고 태스크 매니저가 `blocked`로 멈춘다 → FAILED(HARDWARE)로 올리고, 복구는
현장에서 재보급 후 태스크 매니저 재시작.

### MOVE_TO (AMR)

```
발행: /beagle/goto  data: "<station_a | station_b>"
```

destination 매핑이 필요하다: Backend는 `"L1"`/`"line-a"`류의 라인 id 또는
`"STORAGE"`를 보낸다 → `STORAGE`→`station_a`, 라인 id→`station_b`(어느 라인이든
물리 베이는 하나다). 매핑 테이블은 topic_map.py에 상수로 둔다.

완료 판정: `/beagle/state`(0.5s 주기 JSON)에서 `station == 목표` **그리고**
`ready_for_arm: true`가 되는 순간 DONE(`ARRIVED`). 발행 시점에 이미 목표
스테이션이면 즉시 DONE. 실패는 **`state`가 `error`/`estop`일 때**이고 그때
`detail`이 사유 문자열이다 — `detail` 유무로 판정하면 안 된다: 주행 중에도
`detail`에 루트 키(`station_a->station_b`)가 채워진다(#13에서 정정).

### UNLOAD_RESUME (LINE_ARM)

```
발행: /station_b/stock/transfer  {"from": "carrier", "to": "<bin_a|bin_b>", "id": <commandId>}
```

완료 판정은 PICK_LOAD와 동일(DONE detail은 `RESUMED`). **`payload.lineId →
칸(bin) 매핑을 확정해야 한다** — 우리 쪽 목적지는 `bin_a`/`bin_b`(+예정
`bin_c`/`bin_d`)이고 Backend registry는 `line-a`~`line-f` 6구역이다. 실물 칸은
4개뿐이므로 `line-a→bin_a, line-b→bin_b, line-c→bin_c, line-d→bin_d,
line-e/f→미지원(FAILED UNSUPPORTED)`을 제안한다. Backend registry.yaml과 같이
정할 것.

### HOME

팔: 모든 transfer가 마지막 스텝으로 home 복귀를 포함하므로 **별도 동작 없이
즉시 DONE(`HOMED`)** 응답이 맞다 (idle이 아닐 때는 FAILED BUSY).
비글: `MOVE_TO station_a`와 동일하게 처리.

### ABORT

비글: `/beagle/estop`에 **`std_msgs/Bool` `data: true`** 발행(다른 토픽과 달리
String이 아니다 — #13에서 정정). 래치라 해제는 `data: false`인데 해제 커맨드는
현 계약에 없음(phase 2). 팔: 태스크 매니저에 중단 인터페이스가 없다 —
정직하게 **FAILED(UNSUPPORTED)** 로 응답한다. (추가하려면 로봇 저장소 쪽
task manager에 abort 토픽을 넣는 작업이 선행돼야 함 — phase 2.)

## 4. 오케스트레이터 충돌 — 반드시 정할 운영 규칙

Backend orchestrator의 PICK_LOAD→MOVE_TO→UNLOAD_RESUME→MOVE_TO 4단계는 로봇
저장소의 `stock_relay_node`가 하는 일과 **동일하다.** 지휘자가 둘이면 같은
태스크 매니저에 transfer가 겹쳐 들어가 한쪽이 rejected로 죽는다.

규칙: **대시보드 연동 모드에서는 `/stock/refill_request`를 아무도 발행하지
않는다** (relay 노드는 그 요청 없이는 영원히 idle이므로 띄워둬도 무해). 단독
데모 때만 refill_request를 쓴다. 브리지 README/런치 문서에 이 규칙을 명시할 것.

## 5. 이 계약에서 아직 못 주는 것

- **telemetry (§8)**: 비글은 SLAM/GPS가 없고 데드레코닝뿐이라 x/y 포즈 스트림이
  없다. QoS 0 선택 항목이므로 phase 1에서는 발행 생략. (원하면 루트 진행률로
  1차원 보간 위치를 합성하는 게 phase 2 후보.)
- ~~**inventory (§10)**~~: 2026-08-31 해소. 재고 카메라 ROI가 실측으로 채워지고
  `stock_monitor_node`가 칸 a~d를 실제로 판정하게 되면서, 브리지가 `/stock/status`를
  칸 단위 INVENTORY로 중계한다(§10.2). 판정이 안정된 칸만 올린다 — 팔이 칸 위를
  지나가는 프레임을 부족으로 올리면 사람이 치우지도 않은 칸에 로봇이 움직인다.
- ~~**승인 전 확인**~~: 2026-08-31 신설. 창고에 부품이, 베이에 비글이 있는지를
  `/stock/station_a_ready` -> `station/{stationId}/readiness`로 중계한다(§10.3).
  백엔드는 승인 시점에 이 값을 보고 거절할 수 있다.

## 6. 작업 순서 (DoD 포함)

1. `topic_map.py`를 §1~2대로 개편 — DoD: 단위 테스트에서 세 robotId가 실제
   토픽 문자열로 해석됨.
2. `bridge_node.py`에 팔 state 구독(스테이션당 1개) + `last_job.id` 매칭 로직,
   비글 state 구독 + 도착 판정 구현. `_dispatch_arm/_move/_home/_abort`를 §3
   사양대로 채움 — DoD: ROS2 환경에서 브리지 + 실물 스택을 띄우고
   `mosquitto_pub`로 §6 커맨드 4종을 손으로 넣어 STATUS가 스키마대로 돌아옴.
3. Backend 붙여 mock 검증 시나리오 재현(`PUT /api/lines/L1/stock` 부족 이벤트)
   — DoD: 실물 4단계가 끝까지 돌고 라인 상태가 `restocking`→`normal` 복귀.
   **완료(2026-08-21)**: 실물 5회 완주(1회 약 80 s), 커맨드 4종 STATUS 왕복 확인.
4. ~~`scripts/mock_robot.py` 삭제~~ → **삭제 대신 스코프 축소로 변경(이슈 #15)**:
   시뮬 라인(line-b~f)의 가상 robotId(omxf-*-02/03 등)는 계속 mock_robot이
   응답해야 하므로 지울 수 없다. 대신 기본값으로 실기 robotId
   (topic_map.ROBOT_TOPICS)를 무시하게 하여 실기 브리지와 동시 기동을 안전하게
   만들었다 — 전부 응답하는 기존 동작은 `--all` 플래그로만.
   (mock_vision.py는 §5 inventory 갭이 해소될 때까지 유지.)

## 7. 실행 배치 참고

- 브리지: PC1 호스트, `ros2 launch mqtt_bridge bridge.launch.py` (도메인 0 기본값
  그대로). MQTT는 Backend docker-compose의 Mosquitto(`localhost:1883`).
- 로봇 스택 기동 순서는 로봇 저장소 문서 기준: **Beagle 브릿지(컨테이너) 먼저,
  팔 런치는 그 다음** (동글 스캔이 Dynamixel 버스를 끊는 문제). 브리지(MQTT)는
  순서 무관.
- PC2의 station_c는 티칭 완료 후 이 문서의 표에 합류시킨다.
