# COMMAND_SCHEMA — 로봇(하드웨어) ↔ 백엔드 MQTT 계약

> 이 문서는 `Team1SmartFactory/Backend`의 실제 Pydantic 코드(`app/contracts/enums.py`,
> `app/contracts/messages.py`, `app/mqtt/subscriber.py`, `app/core/orchestrator.py`)에서
> 역으로 뽑아낸 것이다. 백엔드 코드 여기저기서 "COMMAND_SCHEMA.md N장"을 참조하는데
> 정작 이 문서가 Backend 레포에 없어서, 실제 계약과 어긋나지 않게 이 레포(Hardware)에
> 원본으로 둔다. **백엔드 계약이 바뀌면 이 문서도 같이 갱신할 것.**
>
> 필드명은 전부 camelCase다 (Pydantic 모델이 그대로 이 이름을 씀 — 별도 변환 없음).

---

## 1. 공통 봉투 (MessageBase)

모든 메시지는 아래 두 필드를 공통으로 갖는다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `timestamp` | ISO 8601 문자열 | 메시지 생성 시각 |
| `schemaVersion` | `2` (고정) | 현재 유일한 버전 |

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

## 5. 에러 코드 (ErrorCode)

`TIMEOUT` | `BUSY` | `UNSUPPORTED` | `HARDWARE` | `ABORTED`

---

## 6. 백엔드 → 로봇: `robot/{robotId}/cmd` (발행, QoS 1)

백엔드가 발행한다. 로봇(어댑터)이 **구독**해야 하는 유일한 토픽.

```json
{
  "type": "COMMAND",
  "timestamp": "2026-08-10T00:00:00.000Z",
  "schemaVersion": 2,
  "commandId": "uuid",
  "jobId": "evt-xxxxxxxx | null",
  "robotId": "beagle-01",
  "role": "AMR",
  "action": "MOVE_TO",
  "payload": { "...": "action별로 다름, 7장 참고" },
  "timeoutSec": 60
}
```

- `commandId`는 매 커맨드마다 새로 발급되는 UUID. **STATUS 응답에 그대로 echo 해야 한다** —
  백엔드가 이 값으로 중복/지각 응답을 걸러낸다(QoS 1 중복 방어).
- `timeoutSec`(기본 60초) 안에 STATUS(DONE 또는 FAILED)가 안 오면, 백엔드가 스스로
  실패로 간주하고 다음 단계를 진행하지 않는다 — 어댑터가 이 시간 안에 반드시 응답해야 함.

### action별 payload

| action | payload |
|---|---|
| `PICK_LOAD` | `{ "partId": string, "qty": number, "lineId": string }` |
| `MOVE_TO` | `{ "destination": string }` — 라인 id(`"L1"`) 또는 `"STORAGE"` |
| `UNLOAD_RESUME` | `{ "partId": string, "qty": number, "lineId": string }` |
| `HOME` | `{}` |
| `ABORT` | `{}` |

---

## 7. 로봇 → 백엔드: `robot/{robotId}/status` (구독, QoS 1)

로봇(어댑터)이 **발행**해야 한다. 커맨드 하나당 최소 DONE/FAILED 1회, 중간에
ACCEPTED/RUNNING을 얼마든지 더 보내도 된다(선택).

```json
{
  "type": "STATUS",
  "timestamp": "2026-08-10T00:00:05.000Z",
  "schemaVersion": 2,
  "commandId": "받았던 commandId 그대로",
  "jobId": "받았던 jobId 그대로 (있었다면)",
  "robotId": "beagle-01",
  "state": "DONE",
  "payload": {
    "detail": "ARRIVED",
    "progress": 1.0,
    "error": null
  }
}
```

- `payload.detail`: `ACCEPTED`/`RUNNING`일 때는 자유 문자열, `DONE`일 때는 role별
  고정값 권장 — `LOADED`(PICK_LOAD) / `ARRIVED`(MOVE_TO) / `RESUMED`(UNLOAD_RESUME) /
  `HOMED`(HOME) / `ABORTED`(ABORT)
- `payload.error`: `state: "FAILED"`일 때만 `{ "code": ErrorCode, "message": string }`

⚠️ **`commandId`가 백엔드가 마지막으로 기다리던 값과 다르면 백엔드는 조용히 무시한다**
(중복 배달·지각 도착 방어, `app/core/orchestrator.py`의 가드). 응답할 때 반드시 그
커맨드의 `commandId`를 그대로 넣을 것.

---

## 8. 로봇 → 백엔드: `robot/{robotId}/telemetry` (구독, QoS 0)

평면도 탭 실시간 위치 갱신용 고빈도 스트림. **1~5Hz 권장**(백엔드가 화면에는 1초
간격으로 부드럽게 보간해서 보여주므로, 그보다 낮아도 무방).

```json
{
  "type": "TELEMETRY",
  "timestamp": "2026-08-10T00:00:00.100Z",
  "schemaVersion": 2,
  "robotId": "beagle-01",
  "position": { "x": 12.3, "y": 4.5, "theta": 1.57 },
  "battery": 0.82,
  "source": "SLAM"
}
```

- `position.x/y`: **미터 단위**(0~100 상대값 아님 — 그 변환은 백엔드가 `registry.yaml`의
  `layout.bounds`로 함). `theta`: 라디안.
- `battery`: 0~1
- `source`: `GPS` | `SLAM` | `ODOM`

---

## 9. 로봇 → 백엔드: `robot/{robotId}/online` (구독, QoS 1) — LWT

로봇 어댑터 프로세스의 생사 신호. **MQTT LWT(Last Will and Testament)로 등록**해서,
연결이 비정상 종료돼도 브로커가 자동으로 `online: false`를 발행하게 할 것.

```json
{ "online": true }
```

- 백엔드는 `online` 필드만 읽는다(다른 필드 무시). `online: false` 수신 시 해당 로봇을
  즉시 `offline` 상태로 전이한다. `online: true`는 별도 처리 안 함 — 곧이어 STATUS로
  실제 상태가 오는 걸 기다림.

---

## 10. 로봇/카메라 → 백엔드: `line/{lineId}/inventory` (구독, QoS 1)

천장 카메라(CV) 재고 감지 결과. **비전 파이프라인(정지우 팀장 YOLO 쪽)이 최종적으로
발행해야 하는 토픽** — 지금 이 레포의 브리지는 로봇 제어(§6~9)만 다루고, 이 토픽은
비전 쪽 산출물이 직접 발행하거나 이 브리지에 합류시켜야 함(TODO, 아래 "남은 일" 참고).

```json
{
  "type": "INVENTORY",
  "timestamp": "2026-08-10T00:00:00.000Z",
  "schemaVersion": 2,
  "lineId": "L1",
  "partId": "P-001",
  "areaRatio": 0.03,
  "thresholdRatio": 0.05,
  "qtyEstimate": 3,
  "status": "LOW",
  "source": "CV_AREA",
  "cameraId": "cam-L1"
}
```

- `areaRatio`/`thresholdRatio`: 0~1 비율(면적 기준, 개수 아님). 백엔드가 `* 100`해서
  `Line.currentQty`(%)로 저장.
- `status`: `OK` | `LOW` (참고용 — 백엔드는 지금 이 필드가 아니라 `areaRatio` vs
  라인 threshold를 직접 비교해서 판정한다는 점에 유의. 이 필드는 향후 활용 예정)
- `source`: `CV_AREA` | `CV_DEPTH` | `LOAD_CELL`

⚠️ **백엔드에 자동 부족 감지 로직 자체가 아직 없음** (2026-08-10 기준). 이 토픽이
들어와도 `Line.currentQty`만 갱신되고, 임계치 이하로 떨어져도 승인 대기 이벤트가
자동 생성되지 않는다 — Backend 쪽 별도 작업 필요(Backend 레포에 이슈 등록 예정).

---

## 11. Job / 승인 / 모드 — 참고용 (지금은 REST로 대체됨)

원래 계약 초안에는 `JOB`/`APPROVAL`/`MODE` MQTT 메시지도 있었지만, 실제 구현에서는
전부 **REST API로 대체**됐다 (Backend `docs/API_LIST.md` 8장 "기존 초안과 달라진 점"
참고). 로봇/하드웨어 쪽에서는 이 3종을 신경 쓸 필요 없음 — §6~10만 구현하면 된다.

---

## 12. 이 레포(Hardware)가 지금 다루는 범위

| 토픽 | 방향 | 이 레포 범위 |
|---|---|---|
| `robot/{id}/cmd` | 백엔드→로봇 | ✅ 브리지가 구독, ROS2로 변환 |
| `robot/{id}/status` | 로봇→백엔드 | ✅ 브리지가 ROS2에서 받아 발행 |
| `robot/{id}/telemetry` | 로봇→백엔드 | ✅ (스켈레톤만, ROS2 토픽 연결 TODO) |
| `robot/{id}/online` | 로봇→백엔드 | ✅ LWT 등록 (스켈레톤) |
| `line/{id}/inventory` | 카메라→백엔드 | ❌ 범위 밖 — 비전(YOLO) 쪽에서 직접 발행하거나 별도 통합 필요 |
