# COMMAND_SCHEMA — 로봇(하드웨어) ↔ 백엔드 MQTT 계약

> **v2 (2026-08-16 개정)** — 3자 자문 검토(신뢰성/전달성/계약설계, Hardware#5) 반영.
> 이번 개정의 모든 변경은 **additive 또는 행동 규약**이며 wire 포맷 breaking 변경은 없다.
> `schemaVersion`은 2로 동결.
>
> 이 문서가 BE↔HW 계약의 **원본(단일 진실)** 이다. 코드 구현은 Backend
> `app/contracts/{enums,messages}.py` ↔ Hardware `mqtt_bridge/contracts.py` (1:1 미러).
> 계약이 바뀌면 이 문서를 먼저 개정하고 양측 코드를 동기 반영한다.
>
> 필드명은 전부 camelCase다 (Pydantic 모델이 그대로 이 이름을 씀 — 별도 변환 없음).
>
> ⚠️ **장 번호 참고**: Backend 코드 docstring의 "COMMAND_SCHEMA.md N장" 인용 일부는
> 이 문서 확정 전의 구 초안 번호다 (예: ErrorCode를 "4장", INVENTORY를 "6장"으로 인용).
> 어긋나는 경우 **이 문서의 현행 번호가 맞다.**

---

## 0. 토픽·retain·QoS 총괄표

| 토픽 | 방향 | QoS | retain | 발행 주체 |
|---|---|---|---|---|
| `robot/{robotId}/cmd` | BE→로봇 | 1 | **금지** | BE 오케스트레이터 |
| `robot/{robotId}/status` | 로봇→BE | 1 | **금지** | 브리지/어댑터 |
| `robot/{robotId}/telemetry` | 로봇→BE | 0 | 금지 | 브리지/어댑터 (1~5Hz) |
| `robot/{robotId}/online` | 로봇→BE | 1 | true | 개별 어댑터 (LWT) — §9 |
| `bridge/online` | 브리지→BE | 1 | true | 브리지 (LWT) — §9a **신설** |
| `line/{lineId}/inventory` | 비전→BE | 1 | true | 비전 발행기 — §10 |
| `line/{lineId}/bin/{label}/inventory` | 비전→BE | 1 | true | 브리지 — §10.2 **신설** |
| `station/{stationId}/readiness` | 비전→BE | 1 | true | 브리지 — §10.3 **신설** |

**retain 규약 (필수 준수)**
- `online` / `bridge/online` / `inventory`는 **retain=true** — BE가 재시작해도 브로커에서
  마지막 상태를 즉시 복원한다 (level 시맨틱: "현재 상태"를 나르는 메시지).
- `cmd` / `status`는 **retain 금지** — 낡은 커맨드/응답이 재접속 시 재생되는 것은
  최악의 장애 모드다 (edge 시맨틱: "사건"을 나르는 메시지).

---

## 1. 공통 봉투 (MessageBase)

모든 메시지는 아래 두 필드를 공통으로 갖는다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `timestamp` | string | **UTC, ISO 8601, 밀리초 정확히 3자리, `Z` 접미사 필수** — `2026-08-16T04:07:20.123Z`. naive/오프셋(+09:00) 표기 금지 |
| `schemaVersion` | `2` (고정) | 현재 유일한 버전. **동결** — 데모까지 범프 금지 |

- 발행측은 공유 헬퍼(`contracts.now_iso()`)로 생성한다. 수신측은 형식 위반 메시지를
  **로그 남기고 버린다** (예외 전파 금지 — §12 참고).
- **시간 비교는 수신 시각 기준**: 쿨다운·만료 등 시간 판정 로직은 메시지 `timestamp`가 아니라
  수신측 자기 시계를 기준으로 한다 — 호스트/컨테이너/장비 간 시계 스큐에 면역이 되도록.
  (`timestamp`는 진단·로그용이 1차 용도)

### 1.1 계약 진화 규칙 (신설)

1. **optional 필드 추가** = `schemaVersion` 유지. 수신측은 모르는 필드를 무시한다.
2. **필드 제거·개명·타입 변경·enum 값 추가** = breaking — `schemaVersion` 범프 + 팀 합의
   없이는 금지. (enum 값 추가가 breaking인 이유: FE zod가 즉시 거부 → 화면 장애)
3. **계약 위반 메시지 처리** = 수신측은 원문(topic+payload)을 로그로 남기고 그 건만 버린다.
   예외를 위로 던지지 않는다. 데드레터 큐는 두지 않는다 (소비 팀이 늘면 재검토).

---

## 2. 로봇 역할 (RobotRole)

| 값 | 의미 |
|---|---|
| `STORAGE_ARM` | 보관소 OMX-F (부품 적재) |
| `LINE_ARM` | 라인 OMX-F (부품 하역) |
| `AMR` | Beagle (운반 로봇) |

## 3. 커맨드 액션 (CommandAction)

| 값 | 대상 역할 | 의미 |
|---|---|---|
| `PICK_LOAD` | STORAGE_ARM | 부품을 집어 Beagle에 적재 |
| `MOVE_TO` | AMR | 지정 목적지로 이동 |
| `UNLOAD_RESUME` | LINE_ARM | Beagle에서 부품을 내려 라인에 보충 |
| `HOME` | 공통 | 기본 위치로 복귀 |
| `ABORT` | 공통 | 진행 중인 동작 즉시 중단 |

## 4. 로봇 상태 (RobotState — STATUS.state)

`ACCEPTED` → `RUNNING` → `DONE` 또는 `FAILED`

## 5. 에러 (ErrorDetail)

```json
{ "code": "HARDWARE", "message": "그리퍼 걸림", "detailCode": "GRIPPER_JAM_LEFT" }
```

- `code`: **string** (개정 — 구 계약은 닫힌 enum). 표준 5종
  `TIMEOUT` | `BUSY` | `UNSUPPORTED` | `HARDWARE` | `ABORTED` 를 **권장**하며, BE는 이
  5종 외의 값도 거부하지 않고 로그 후 표준 처리(FAILED 전이)한다.
- `detailCode` (optional, 신설): 로봇별 특화 에러 식별자. BE는 저장·로그만 하고 해석하지 않는다.

---

## 6. 백엔드 → 로봇: `robot/{robotId}/cmd` (QoS 1, retain 금지)

백엔드가 발행한다. 로봇(브리지)이 **구독**해야 하는 유일한 토픽.

```json
{
  "type": "COMMAND",
  "timestamp": "2026-08-16T04:00:00.000Z",
  "schemaVersion": 2,
  "commandId": "uuid",
  "jobId": "evt-xxxxxxxx | null",
  "robotId": "beagle-01",
  "role": "AMR",
  "action": "MOVE_TO",
  "payload": { "destination": "line-a" },
  "timeoutSec": 60
}
```

- `commandId`는 매 커맨드마다 새로 발급되는 UUID. **STATUS 응답에 그대로 echo 해야 한다** —
  백엔드가 이 값으로 중복/지각 응답을 걸러낸다(QoS 1 중복 방어).

### 6.1 수신측(브리지) 의무 규약 (신설)

- **만료 검사**: 수신 시각이 `timestamp + timeoutSec`을 지났으면 **실행하지 않고**
  `FAILED(code=TIMEOUT, message="expired")`를 반송한다. 브로커 재접속으로 늦게 배달된
  커맨드가 뒤늦게 로봇을 움직이는 사고 방지.
- **중복 멱등성**: 같은 robotId에 직전과 동일한 `commandId`가 재수신되면(QoS 1 재배달)
  **재실행하지 않고** 마지막으로 보냈던 STATUS만 재발행한다.

### 6.2 action별 payload

| action | payload |
|---|---|
| `PICK_LOAD` | `{ "partId": string, "qty": number, "lineId": string }` |
| `MOVE_TO` | `{ "destination": string }` — **닫힌 어휘**: `line-a`~`line-f` 또는 `"STORAGE"`. 유효 라인 ID의 유일 원천은 Backend `config/registry.yaml` `lines[].lineId` |
| `UNLOAD_RESUME` | `{ "partId": string, "qty": number, "lineId": string }` |
| `HOME` | `{}` (빈 객체 확정) |
| `ABORT` | `{}` (빈 객체 확정) |

- destination → 실좌표(waypoint) 변환은 **브리지 소유** — BE는 논리 목적지만 안다.

---

## 7. 로봇 → 백엔드: `robot/{robotId}/status` (QoS 1, retain 금지)

로봇(브리지)이 **발행**한다. 커맨드 하나당 최소 DONE/FAILED 1회.

```json
{
  "type": "STATUS",
  "timestamp": "2026-08-16T04:00:05.000Z",
  "schemaVersion": 2,
  "commandId": "받았던 commandId 그대로",
  "jobId": "받았던 jobId 그대로 (있었다면)",
  "robotId": "beagle-01",
  "state": "DONE",
  "payload": { "detail": "ARRIVED", "progress": 1.0, "error": null }
}
```

- `payload.detail`: `ACCEPTED`/`RUNNING`일 때는 자유 문자열, `DONE`일 때는 action별 고정값
  권장 — `LOADED`(PICK_LOAD) / `ARRIVED`(MOVE_TO) / `RESUMED`(UNLOAD_RESUME) /
  `HOMED`(HOME) / `ABORTED`(ABORT)
- `payload.error`: `state: "FAILED"`일 때만 §5 ErrorDetail.

### 7.1 RUNNING = 워치독 keepalive (신설)

- **브리지 의무**: `timeoutSec`보다 오래 걸릴 수 있는 액션(arm 동작, 장거리 이동) 실행 중에는
  **최소 20초 간격**으로 `RUNNING` + `progress`를 발행한다.
- **BE 의무**: `commandId`가 일치하는 `RUNNING` 수신 시 해당 커맨드의 타임아웃 워치독을
  **재장전**한다. (계약상 `timeoutSec`은 "총 실행 시간 한도"가 아니라 **"무소식 허용 한도"**
  로 의미가 확정된다.)
- ⚠️ **`commandId`가 백엔드가 마지막으로 기다리던 값과 다르면 백엔드는 조용히 무시한다**
  (중복 배달·지각 도착 방어). 응답할 때 반드시 그 커맨드의 `commandId`를 그대로 넣을 것.

---

## 8. 로봇 → 백엔드: `robot/{robotId}/telemetry` (QoS 0, 1~5Hz)

```json
{
  "type": "TELEMETRY",
  "timestamp": "2026-08-16T04:00:00.100Z",
  "schemaVersion": 2,
  "robotId": "beagle-01",
  "position": { "x": 12.3, "y": 4.5, "theta": 1.57 },
  "battery": 0.82,
  "source": "SLAM"
}
```

- `position.x/y`: **미터 단위** (0~100 상대값 아님 — 그 변환은 BE가 `registry.yaml`
  `layout.bounds`로 수행). `theta`: 라디안. `battery`: 0~1.
- `source`: `GPS` | `SLAM` | `ODOM`
- 좌표계: 원점 = 평면도 좌상단, x → 우, y → 하. (실기 SLAM 연동 착수 시 원점 캘리브레이션
  절차와 함께 재확정 예정)

---

## 9. 개별 어댑터 → 백엔드: `robot/{robotId}/online` (QoS 1, retain, LWT)

```json
{ "online": true }
```

- **역할 한정 (개정)**: 이 토픽은 로봇 어댑터가 **브리지와 별개 프로세스로 돌 때**를 위한
  것이다. 현 구조(브리지 한 프로세스가 실기 로봇 전부 담당)에서는 §9a `bridge/online`이
  생사 신호의 원본이고, 이 토픽은 브리지가 보조로 발행한다.
- MQTT LWT로 등록해 비정상 종료 시 브로커가 자동으로 `online: false`를 발행하게 한다.
- BE는 `online: false` 수신 시 해당 로봇을 `offline`으로 전이. `online: true` 수신 시
  `offline`이던 로봇을 `idle`로 복귀시킨다.

## 9a. 브리지 → 백엔드: `bridge/online` (QoS 1, retain, LWT) — 신설

```json
{ "online": true, "robotIds": ["omxf-storage-01", "beagle-01", "omxf-line-01"], "ts": "2026-08-16T04:00:00.000Z" }
```

- 브리지 프로세스 전체의 생사 신호. **연결 전에 LWT로
  `{"online": false, "robotIds": [...]}` 를 등록**하고, 접속 직후 `online: true`를
  retained로 발행한다.
- BE는 `online: false` 수신 시 `robotIds` 전체를 일괄 `offline` 전이 — 브리지가 죽었는데
  대시보드가 "이동 중"으로 영구 고착되는 것을 막는다 (자문 판정 J1).
- 로봇별 MQTT 커넥션 분리는 **하지 않는다** — 실기 로봇 전부가 브리지 한 프로세스에 매달린
  현 구조에서 장애 도메인 표현으로 브리지 단위 1개가 정확하다.

---

## 10. 비전 → 백엔드: `line/{lineId}/inventory` (QoS 1, retain)

```json
{
  "type": "INVENTORY",
  "timestamp": "2026-08-16T04:00:00.000Z",
  "schemaVersion": 2,
  "lineId": "line-a",
  "partId": "P-001",
  "areaRatio": 0.03,
  "thresholdRatio": 0.05,
  "qtyEstimate": 3,
  "status": "LOW",
  "source": "CV_AREA",
  "cameraId": "cam-line-a",
  "partName": null,
  "requiredQty": null
}
```

| 필드 | 규약 |
|---|---|
| `areaRatio` | 0~1 비율(면적 기준). **판정의 유일한 입력** — BE가 `×100` 해서 `Line.currentQty`(%)로 저장하고 registry의 threshold와 비교해 부족을 판정한다 |
| `status`, `thresholdRatio` | **진단 필드로 격하 (개정)** — 발행측 참고값일 뿐, BE는 판정에 쓰지 않는다. **부족 판정의 유일 판정자는 BE(registry.yaml 기준)** 다. BE 판정과 어긋나면 WARN 로그만 남긴다 (진실의 원천 이원화 제거) |
| `source` | `CV_AREA` \| `CV_DEPTH` \| `LOAD_CELL` |
| `partName`, `requiredQty` | **optional 예약 슬롯 (신설, 기본 null)** — "박스 교체 로직" 확정 시 비전측이 채울 수 있게 예약. BE는 null이면 registry의 partId/capacity로 대체(현행 동작 유지) |

### 10.1 발행 규칙 (신설)

- **retain=true**, **변화 시에만 발행** (`areaRatio` ±0.02 초과 변화 또는 자체 status 전이),
  **최대 1Hz**. 매 프레임 발행 금지 — BE의 이력 테이블과 WS 브로드캐스트가 프레임레이트로
  범람하는 것을 방지.
- **발행 경로 확정 (자문 C2)**: 비전(YOLO) 호스트 프로세스가 **paho-mqtt로 직접 발행**한다.
  ROS2를 경유하지 않는다 (stock_bridge.py의 JSON→ROS2→브리지→MQTT 3-hop 계획 폐기 —
  YOLO 호스트↔ROS2 Docker 경계 문제가 MQTT 직결로 자연 해소).

### 10.2 칸 단위 INVENTORY: `line/{lineId}/bin/{label}/inventory` (신설, 2026-08-31)

line-a는 라인 하나가 아니라 칸 넷이다(Backend#37). 재고 카메라도 칸별로 판정하므로
INVENTORY도 칸별로 나간다 — payload는 §10과 같은 스키마에 `binId`를 채운 것이다.

**토픽을 나누는 이유**: retain은 토픽당 마지막 메시지 하나만 남긴다. 칸 넷이
`line/line-a/inventory` 하나를 공유하면 BE가 재시작했을 때 마지막에 바뀐 칸 하나만
복원되고 나머지 셋은 사라진다.

| 필드 | 값 |
|---|---|
| `binId` | `line-a-bin-a` ~ `line-a-bin-d` (Backend registry.yaml의 binId) |
| `partId` | 그 칸에 적재되는 부품 (P-101~P-104) — §3의 partId→bin과 역방향 |
| `areaRatio` | `1.0`(filled) 또는 `0.0`(empty) |
| `status` | `OK`(filled) / `LOW`(empty) — 진단 필드 |

- 이 카메라는 "부품이 있나 없나"를 볼 뿐 얼마나 찼는지 재지 않는다. 그래서 중간값이
  없다 — 임계치가 0과 1 사이 어디에 있어도 부족/충분은 갈린다.
- 판정이 **안정된 칸만**(`stable=true`) 발행한다. 팔이 칸 위를 지나가는 한두 프레임을
  부족으로 올리면, 사람이 치우지도 않은 칸에 대해 승인 팝업이 뜨고 로봇이 움직인다.
- 발행 주체는 브리지다(§10.1의 "비전이 직접 발행"에 대한 예외). 판정 노드가 ROS2
  노드(`stock_monitor_node`)로 이미 존재하고 PC2에서 돌기 때문에, 그 결과를 이미
  ROS2 도메인에 붙어 있는 브리지가 중계하는 편이 경로가 짧다.

### 10.3 스테이션 준비 상태: `station/{stationId}/readiness` (신설, 2026-08-31)

승인된 보충을 **시작해도 되는지**를 스테이션 하나에 대해 답한다. 웹에서 승인이
떨어져도 창고에 부품이 없거나 비글이 베이에 없으면 팔은 허공을 집는다.

```json
{
  "type": "READINESS", "timestamp": "...", "schemaVersion": 2,
  "stationId": "station-a", "ready": false,
  "checks": {"beagle": true, "part": false},
  "source": "CV_AREA", "cameraId": "cam-warehouse"
}
```

- **retain=true**: BE는 승인 요청을 받는 그 순간의 최신값이 필요하다. 구독을 시작한
  뒤 다음 발행을 기다릴 수 없다.
- `checks`가 결론과 함께 가는 이유: 사용자에게 "창고가 비었습니다"를 보여주려면
  `ready:false`만으로는 부족하다.
- **한 번도 못 받았으면 통과시킬 것**: 비전이 없는 환경(시뮬/개발)에서 이 게이트가
  모든 승인을 막으면 안 된다.

### 10a. 발행자 매핑표: `stock_state.json` → INVENTORY (신설)

비전 파이프라인의 슬롯 모니터링 산출물(`stock_state.json`)을 INVENTORY 메시지로 변환하는
규칙. **비전측 실샘플 확보 후 필드명을 고정한다 — 그 전까지 아래는 잠정.**

| INVENTORY 필드 | 값 |
|---|---|
| `lineId` | 재고함 구역 A~F → `line-a`~`line-f` (알파벳 소문자 매핑) |
| `partId` | Backend `registry.yaml` `lines[].partId` (구역별 고정) |
| `areaRatio` | 슬롯 점유 판정 결과 (0~1) |
| `qtyEstimate` | `round(areaRatio × capacity)` — 임시 규칙, 비전 실측으로 교정 예정 |
| `source` | `"CV_AREA"` 고정 |
| `cameraId` | `cam-line-{x}` (registry.yaml `cameras[].cameraId`와 일치) |

**상태 파일 원자성 규약 (자문 C4)**: `stock_state.json`/`beagle_state.json` 쓰기 측은
tmp 파일에 쓴 뒤 `os.replace()`로 원자 교체한다. 읽기 측은 mtime이 5초 이상 오래된 파일은
스킵하고, 파싱 실패 시 직전 값을 유지한다. (세부는 `docs/STATE_FILES.md`로 분리 예정 —
비전측 실샘플 확보 후 작성)

---

## 11. Job / 승인 / 모드 — 참고용 (REST로 대체됨)

원래 계약 초안에는 `JOB`/`APPROVAL`/`MODE` MQTT 메시지도 있었지만, 실제 구현에서는
전부 **REST API로 대체**됐다 (Backend `docs/API_LIST.md` 참고). 로봇/하드웨어 쪽에서는
이 3종을 신경 쓸 필요 없음 — §6~10만 구현하면 된다.

## 12. 수신측 공통 방어 규약 (신설)

- 계약 위반 메시지(JSON 파싱 실패, 스키마 불일치, timestamp 형식 위반)는 **topic과 payload
  원문을 로그로 남기고 그 건만 버린다.** 예외를 수신 스레드 밖으로 던지지 않는다 —
  깨진 메시지 1건이 수신 루프 전체를 죽이는 것이 최악의 장애 모드다.
- 미등록 lineId/robotId 메시지는 WARN 로그 후 무시 (registry.yaml이 유효 ID의 원천).

## 13. 이 레포(Hardware)가 다루는 범위

| 토픽 | 방향 | 이 레포 범위 |
|---|---|---|
| `robot/{id}/cmd` | BE→로봇 | ✅ 브리지가 구독, ROS2로 변환 |
| `robot/{id}/status` | 로봇→BE | ✅ 브리지가 ROS2에서 받아 발행 (RUNNING keepalive 포함) |
| `robot/{id}/telemetry` | 로봇→BE | ✅ (스켈레톤, ROS2 토픽 연결 TODO) |
| `robot/{id}/online` + `bridge/online` | →BE | ✅ 브리지 LWT |
| `line/{id}/inventory` | 비전→BE | ⚠️ 발행 스크립트(`scripts/vision_bridge.py` 예정)는 이 레포에 두되, YOLO 파이프라인 자체는 범위 밖 |
