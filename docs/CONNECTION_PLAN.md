# HW ↔ BE 연결 실행 계획

> 2026-08-16, 독립 3개 렌즈(신뢰성 / 전달성·통합순서 / 계약설계) 자문 검토를 종합한
> 실행 계획 (Hardware#5). 계약 변경 사항 자체는 `COMMAND_SCHEMA.md` v2 개정에 반영됐고,
> 이 문서는 **판단 근거와 실행 순서**를 기록한다.

## 총평 (3자 수렴 결론)

**계약과 배관은 이미 건전하다.** mock 기반 전체 왕복(감지→자동 이벤트→승인→4단계→완료)이
실검증됐고, commandId 에코 가드·QoS 배분·contracts 1:1 미러·topic_map으로의 ROS2 의존 격리는
세 렌즈 모두 강점으로 인정했다. 남은 위험은:

- (a) **장애·경계 케이스 방어 약 100줄** — 아래 Phase 1~2
- (b) **미확정 인터페이스의 동결 지연** — ROS2 실토픽명, stock_state.json 실스키마
- (c) **문서-실코드 불일치 함정** — COMMAND_SCHEMA v2 개정으로 소거

잔여 시간의 대부분은 `bridge_node.py`의 `_dispatch_*` 4개 실물화에 투입한다.

## 확정 결정 (3자 합의)

| # | 결정 |
|---|---|
| C1 | 브리지 단위 LWT 1개(`bridge/online`). 로봇별 MQTT 커넥션 분리는 하지 않는다 |
| C2 | **비전 경로는 ROS2를 경유하지 않는다** — stock_bridge.py(JSON→ROS2→MQTT 3-hop) 계획 폐기, YOLO 호스트에서 paho로 `line/{lineId}/inventory` 직접 발행 |
| C3 | STATUS RUNNING = 워치독 keepalive (브리지 20초 간격 발행, BE 타이머 재장전) |
| C4 | 상태 파일 원자 쓰기: tmp + `os.replace()`, 읽기측 mtime 5초 초과 스킵 |
| C5 | 과설계 금지 목록(아래) 준수 — 남는 시간 전부 `_dispatch_*` 실물화에 |
| C6 | retain 규약: online/inventory=retain, cmd/status=retain 금지 |

## 쟁점 판정 기록

| 쟁점 | 판정 | 근거 |
|---|---|---|
| J1. 브리지 생사 감지 심각도 (critical vs low) | **채택** (수정 비용 ~10줄 + BE 라우트 1개) | 실패 모드(브리지 사망 시 대시보드 "이동 중" 영구 고착)가 데모 화면에 직접 노출. 단 그 이상(로봇별 heartbeat 서버 등)은 금지 |
| J2. MOVE_TO destination 어휘 | 닫힌 어휘 `{line-a..line-f, STORAGE}` | BE는 이미 line-a를 송신 중 — 코드 무변경, 문서만 교정. 좌표 변환은 브리지 소유 |
| J3. 타임아웃 대응 | RUNNING 재장전(1차) + 액션별 타임아웃(2차) 병행 | `{PICK_LOAD:120, UNLOAD_RESUME:120, MOVE_TO:90, HOME:90, ABORT:15}` 초안 — 리허설 실측 후 1.5배 마진으로 확정 |
| J4. `_dispatch_home` | 에코 스텁 유지 허용 (최후순위) | 데모 시나리오 성립에 필수 아님. 단 UI에 완료로 표시되므로 시나리오 포함 여부 먼저 확인 |
| J5. timestamp 검증 | 규약 명문화 + 발행측 패턴 검증 + **시간 비교는 수신 시각 기준** | 수신측 거부는 반드시 try/except 안에서 (스레드 사망 경로 금지) |

## 실행 순서 (의존성 순)

### Phase 0 — 동결 (반나절, 다른 모든 작업의 선행 조건)

1. **ROS2 담당과 30분 동결 세션**: 실기 컨테이너에서 `ros2 topic list -t` /
   `ros2 action list -t` / `ros2 interface show` 출력을 받아
   `mqtt_bridge/topic_map.py`의 placeholder 4종(goal_topic, arrival_topic, arm_action,
   gripper_action)과 `/beagle_arrived` 메시지 타입을 실제 값으로 기입·커밋 후 동결 선언.
   **이 세션 전에는 dispatch 실구현 착수 금지.**
2. 비전측 `stock_state.json` **실샘플 1개 확보** → COMMAND_SCHEMA §10a 매핑표 필드명 고정,
   원자 쓰기 규약을 `docs/STATE_FILES.md`로 분리 작성.
3. ~~COMMAND_SCHEMA.md 일괄 개정~~ ✅ (Hardware#5에서 완료) — 양측 contracts 코드 동기
   반영까지. 이 시점부로 팀원 간 작업이 완전 분리된다.

### Phase 1 — BE 방어 (~1일, Phase 0-3에만 의존) → Backend 이슈로 분리

4. **[최우선, 30분]** `app/mqtt/subscriber.py`의 on_message에서 `_route` 호출을
   try/except로 감싸고 topic·payload 원문 `logging.exception` — **계약 위반 메시지 1건이
   paho 수신 스레드를 죽이는 시연 전면 장애 1순위 제거** (현행 코드는 `json.loads`만
   감싸고 pydantic ValidationError는 무방비 — 실코드 확인 완료).
5. `bridge/online` 라우트: false 수신 시 robotIds 전체 offline 전이. online:true 수신 시
   offline→idle 복귀 분기.
6. `orchestrator.py`: `COMMAND_TIMEOUT_SEC` → 액션별 딕셔너리(J3), RUNNING 수신 시 워치독
   재장전, `fail_job` 원자 UPDATE 전환.
7. lifespan 기동 스윕: active + last_command_id 보유 이벤트 일괄 fail — BE 재시작 고착 방지.
8. `_publish_command`에서 `mqtt_client.is_connected` 아니면 발행 대신 즉시 fail_job.
9. ErrorDetail.code str 완화 + timestamp aware validator + 미등록 lineId WARN 로그.

### Phase 2 — 브리지 방어 + 배관 검증 (Phase 0-1에 의존, Phase 1과 병행 가능)

10. `bridge_node.py` `__init__`: connect() 전 `set_last_will("bridge/online",
    {"online": False, "robotIds": [...]}, qos=1, retain=True)` + 접속 직후 online:True
    retained 발행.
11. `_on_mqtt_message`에 (a) robotId별 직전 commandId 중복 가드, (b) 만료 검사
    (COMMAND_SCHEMA §6.1) — 합계 약 10줄.
12. **에코 모드 검증**: `_dispatch_*` 4개를 즉시 `_publish_done` 스텁으로 채워 ROS2
    Docker(`--network host`)에서 기동, mock_robot 끄고 승인→4단계→완료 왕복 확인.
    로봇 없이 Docker↔호스트 배관·계약·타이밍 전부 확정.

### Phase 3 — 비전 실물화 (Phase 0-2에 의존, Phase 2와 병행 가능)

13. `scripts/vision_bridge.py` 신규 (호스트 .venv, paho 직결): stock_state.json 1초 폴링
    (mtime 변경 시만) → INVENTORY retain·on-change·≤1Hz 발행. 검증: BE 자동 감지의
    pending_approval 생성 + FE 표시.
14. YOLO 쓰기 측 tmp+`os.replace()` 원자 교체 적용.

### Phase 4 — dispatch 실물화 + 리허설 (Phase 2-12 통과 후, 이 순서로)

15. `_dispatch_move`: goal_topic 발행 + arrival 콜백에서 done — 가장 단순, 4단계 중 2단계 커버.
16. `_dispatch_arm`: ActionClient goal→result, 그리퍼 전후 순차, **RUNNING keepalive 포함**.
17. `_dispatch_abort`: `cancel_goal_async()` 후 FAILED(ABORTED).
18. `_dispatch_home`: MOVE_TO destination="STORAGE" 재사용 (J4 — 스텁 유지 허용).
19. **리허설**: 실측 소요시간 → 타임아웃 1.5배 마진 확정. mock_robot은 시뮬 로봇 id 전용
    기동(실기 id 구독 금지). `scripts/demo_up.sh` + tmux 창 구성
    (mosquitto/ros2/yolo/vision/be/fe/mock), **죽은 프로세스 재기동까지 연습**.

## 하지 말 것 (3자 전원 합의 — 과설계 금지 목록)

- 로봇별 MQTT 커넥션 분리, persistent session(clean_session=False), 브로커 이중화
- Job 테이블·outbox 패턴, 데드레터 큐
- stock_bridge.py의 ROS2 경유 구현 (C2로 대체)
- schemaVersion 범프·버전 협상, payload pydantic 타입화, ErrorCode enum 세분화
- systemd 기반 부팅 자동화 (1회성 데모에 tmux로 충분)

**판단 기준**: Phase 1~2 방어 코드는 총 100줄 미만. 그 이상의 견고화 투자는 `_dispatch_*`
실물화 시간을 잠식하는 순손실이다. 계약 소비 팀이 늘거나 실기 라인이 2개 이상 되는 시점에
재검토한다.
