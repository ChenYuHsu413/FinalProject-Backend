# AI SERVO PLATFORM 後端修改規格(對齊 Flask 前端)

> 文件狀態:後端修改規格草案
> 對齊依據:`design-frontend.md`(前端規劃)、前端實作截圖(login / operator console / engineer health / admin approvals)、`AI SERVO PLATFORM.docx` 既有後端定義
> 目標:讓既有 FastAPI 後端補齊前端 §9.2「依賴確認」與 §14「待決策」所列缺口,支撐 Stage A→E 上線節奏

---

## 0. 現況與缺口總表

### 0.1 既有後端(依前端文件 §9.2 記載)

| 類型 | 端點 / Topic |
|---|---|
| 唯讀 GET | `/l1/realtime`、`/l1/latency`、`/l2/latest`、`/l2/trend`、`/l3/latest`、`/l3/shadow`、`/l3/models`、`/shap/diagnosis`、`/shap/summary`、`/fallback/events`、`/fallback/stats` |
| WebSocket | `ws/l1/inference`、`ws/l1/summary`、`ws/l2/finetune`、`ws/l3/deploy`、`ws/shap/diagnosis`、`ws/fallback/event`、`ws/fallback/escalation` |

### 0.2 缺口(本文件範圍)

| # | 子系統 | 前端依賴 | 優先序 |
|---|---|---|---|
| A | 請求驗證與信任邊界(service token + X-User-* header) | 所有 mutation | P0 |
| B | Dashboard Snapshot 聚合端點 | operator console 首屏、重連恢復 | P0 |
| C | 命令子系統(cycle / mode / e-stop request,狀態機) | operator console 三顆按鈕 | P0 |
| D | 警報子系統(生命週期 + ack) | operator 警報卡 / 警報中心 | P0 |
| E | 稽核子系統(append-only + hash chain) | admin 稽核中心、「稽核鏈完整性 VERIFIED」 | P0 |
| F | 治理核准子系統(promotion / scenario / 調參) | admin approvals 整頁 | P1 |
| G | 系統整合狀態 | admin 整合狀態卡、engineer pipeline | P1 |
| H | 維修回報 | operator maintenance | P1 |
| I | 訓練任務管理(既有 ws/l2、ws/l3 的 REST 補全) | engineer jobs / shadow | P1 |
| J | 資料保留策略 | admin retention | P2 |
| K | 趨勢聚合查詢 | 1h/8h/24h 圖表 | P2 |

使用者/角色管理**不在**後端範圍:依前端架構,身分(帳號、Session、RBAC)由 Flask 自有 PostgreSQL 管理;後端只信任並稽核 Flask 轉附的身分 header(見 §1)。若未來改為 SSO/OIDC,此決策需重新檢視(前端 §14-1)。

---

## 1. 信任邊界與請求驗證(P0)

### 1.1 原則

- 後端**只接受來自 Flask BFF 的請求**:以 mTLS 或 `Authorization: Bearer <service_token>` 驗證來源,token 存於伺服器端 secret store。
- 瀏覽器永不直連 FastAPI / Redis / DB(對齊前端 §5.2)。
- Flask 每個請求必附三個 header,後端**必驗必稽核**,缺一即 `400`:

| Header | 說明 |
|---|---|
| `X-Correlation-ID` | UUID,貫穿 Flask → FastAPI → Redis 事件 → 稽核 |
| `X-User-ID` | 操作者帳號 ID |
| `X-User-Role` | `operator` / `engineer` / `admin` |

- 後端對 mutation 端點做**第二層權限檢查**(permission code 對照表與前端 §6.3 一致,如 `cycle.start`、`alarm.ack`、`model.promote`),不因 Flask 已檢查而略過。角色→permission 對照表由後端持有單一版本,提供 `GET /authz/permissions` 供 Flask 同步,避免兩邊表格漂移。

### 1.2 錯誤格式(全站統一)

```json
{
  "error": {
    "code": "FORBIDDEN | VALIDATION_ERROR | CONFLICT | NOT_FOUND | UPSTREAM_TIMEOUT",
    "message": "human readable",
    "correlation_id": "…",
    "details": {}
  }
}
```

---

## 2. Dashboard Snapshot(P0)

前端首屏與 WebSocket 重連後都需要一次拿齊完整狀態(前端 §9.4)。不要讓 Flask 打 7 支 API 自行拼裝——由後端提供聚合 snapshot,欄位以 operator console 截圖實際渲染為準。

### `GET /ui/snapshot?device=AXIS-04`

```json
{
  "ts": "2026-07-23T09:42:25Z",
  "schema_version": "1.0",
  "device": {"id": "AXIS-04", "cell": "Hsinchu-CellA", "line": "Line02"},
  "scenario": {"id": "S01", "name": "Pick & Place"},
  "control_mode": "NORMAL",
  "system_status": "RUNNING",
  "health_pct": 92,
  "cycle": {"id": "C-08429", "state": "running", "started_at": "…", "elapsed_s": 258},
  "dv": {"value": 0.13, "threshold": 0.35, "delta_5min": -0.02, "status": "normal"},
  "residual": {"value": 0.021, "threshold": 0.035, "sigma3_margin_pct": 40, "status": "in_threshold"},
  "alarms": {"active": 2, "critical": 1, "warning": 1, "oldest_pending_s": 262},
  "model": {"active_version": "v3.2.0", "scenario": "S01"},
  "pipeline": {
    "stages": [
      {"name": "EtherCAT", "metric": "50kHz", "status": "ok"},
      {"name": "Features", "metric": "48 active", "status": "ok"},
      {"name": "Inference", "metric": "0.31ms", "status": "ok"},
      {"name": "Decision", "metric": "Normal", "status": "ok"}
    ],
    "e2e_latency_ms": 0.82, "sla_ms": 1.0
  },
  "health_cards": {
    "comm": {"uptime_pct": 99.98, "packets_lost": 0, "status": "ok"},
    "data_quality": {"score_pct": 99.5, "nan_pct": 0.1, "status": "ok"},
    "model": {"r2": 0.94, "drift_pct": 12, "status": "watch"},
    "fallback": {"failed": 0, "chain_ready": "RF→PID", "status": "ok"},
    "latency": {"inference_ms": 0.31, "p99_ms": 0.45, "status": "ok"}
  }
}
```

備註:
- `health_cards` 供 engineer health 首屏共用同一支 snapshot(以 `?view=engineer` 或直接全給,由 Flask normalizer 取用)。
- `sigma3_margin_pct`、`oldest_pending_s`、`delta_5min` 都是截圖已渲染欄位,由後端計算,前端不自行推導。
- 每個區塊都帶得出 `status`,對齊前端狀態語意(normal/watch/warning/critical/stale)。

---

## 3. 命令子系統(P0)

對齊前端 §14-11。所有命令共用一個狀態機與資料表,類型以 `command_type` 區分。

### 3.1 狀態機

```
submitted → accepted → completed
        ↘  rejected      ↘ failed
                          ↘ timeout(逾時未收到設備確認)
```

- `submitted`:後端收到、驗證通過、已寫入稽核並發佈到 Redis。
- `accepted`:下游(調度器/設備介面)確認接手。
- `completed / failed`:設備端最終結果。
- `timeout`:超過 `confirm_timeout_s` 未獲確認——**維持 timeout,不推定成功或失敗**(對齊前端 §9.4、§8.6-3)。
- E-Stop Request 為高優先命令,同一狀態機,但佇列優先權最高、timeout 更短、稽核標記 `high_risk: true`。

### 3.2 端點

| Method | 端點 | Permission | 說明 |
|---|---|---|---|
| POST | `/commands/cycle/start` | `cycle.start` | body: `device`、`scenario_id`、`reason?` |
| POST | `/commands/cycle/stop` | `cycle.stop` | body: `device`、`reason` |
| POST | `/commands/mode` | `mode.switch` | body: `device`、`target_mode`、`reason` |
| POST | `/commands/estop-request` | `safety.stop_request` | body: `device`、`reason` |
| GET | `/commands/{command_id}` | `dashboard.read` | 單筆狀態查詢(補償輪詢用) |
| GET | `/commands?device=&status=&limit=` | `dashboard.read` | Cycle 紀錄頁資料來源 |

### 3.3 Request / Response

Request(共通):

```json
{
  "device": "AXIS-04",
  "reason": "換線前停機",
  "idempotency_key": "flask-generated-uuid",
  "params": {}
}
```

- `idempotency_key` 由 Flask 產生;後端在 `(command_type, device, idempotency_key)` 上做唯一約束,重複提交回傳**原命令現況**(HTTP 200 + 原 `command_id`),不建新命令。這同時實作前端 §10.2 的防重複點擊。
- 進行中命令衝突(如 cycle 已在 running 再收到 start):回 `409 CONFLICT`,附目前狀態。

Response(202 Accepted):

```json
{
  "command_id": "CMD-2026-000123",
  "status": "submitted",
  "submitted_at": "…",
  "confirm_timeout_s": 10
}
```

### 3.4 事件

Redis channel `ai_servo:command` / WS topic `ws/command/status`:

```json
{
  "event_id": "…", "event_type": "command:status", "timestamp": "…",
  "schema_version": "1.0", "scenario_id": "S01",
  "payload": {
    "command_id": "CMD-2026-000123", "command_type": "cycle.start",
    "device": "AXIS-04", "status": "accepted",
    "operator": "user-linzq", "reason": "…", "correlation_id": "…"
  }
}
```

補齊前端 §9.3 標示為「—」的 `command:status`;`mode:changed` 亦由本子系統在 mode 命令 `completed` 時發佈(解決前端 §14-13:**由後端提供,Flask 不自行推斷**)。

---

## 4. 警報子系統(P0)

### 4.1 生命週期

```
active → acknowledged → resolved
```

- ack 只代表已讀/已認領,**不清除設備異常狀態**(前端 §8.3);resolved 由維修回報或系統偵測殘差恢復後標記。
- 欄位:`alarm_id`、`severity(critical/warning/info)`、`device`、`scenario_id`、`rule`(如 `residual_gt_3sigma`)、`raised_at`、`status`、`ack_by/ack_at/ack_note`、`resolved_at`、`root_cause_ref`(關聯 SHAP 診斷)、`correlation_id`。

### 4.2 端點

| Method | 端點 | Permission |
|---|---|---|
| GET | `/alarms?status=&severity=&device=&from=&to=&page=` | `alarm.read` |
| GET | `/alarms/{id}` | `alarm.read` |
| POST | `/alarms/{id}/ack` | `alarm.ack`(body: `note`,忽略/延後必填原因) |
| POST | `/alarms/{id}/resolve` | `alarm.ack`(body: `maintenance_report_id?`) |

### 4.3 事件

- `alarm:new`、`alarm:updated` 走 Redis channel `ai_servo:alarm` / WS `ws/alarm`,payload schema 同上欄位(解決前端 §14-12)。
- 既有 `ws/fallback/event` 保留為 fallback 專屬事件;**警報是獨立實體**,fallback 觸發時由後端同時開立一筆 alarm,兩者以 `correlation_id` 關聯。

---

## 5. 稽核子系統(P0)

### 5.1 儲存設計

- PostgreSQL append-only 資料表,應用層禁止 UPDATE/DELETE(DB 帳號權限層面直接拒絕)。
- **Hash chain**:每筆 `entry_hash = SHA256(prev_hash + canonical_json(entry))`,支撐 admin 頁「稽核鏈完整性 VERIFIED」徽章——該徽章必須來自定期重驗,不能是布林欄位(對齊前端 §7.5 註記)。
- 欄位對齊前端 §11.2 全清單:`event_id`、`correlation_id`、`command_id?`、`user_id`、`role`、`source_ip`、`action`、`target_device`、`scenario_id`、`old_value`、`new_value`、`reason`、`proposed_at/approved_at/executed_at`、`result`、`model_version?`、`mode?`、`prev_hash`、`entry_hash`。

### 5.2 端點

| Method | 端點 | Permission |
|---|---|---|
| POST | `/audit/events` | (service token only)Flask 端事件(登入、登出、失敗鎖定)寫入 |
| GET | `/audit/events?actor=&action=&from=&to=&page=` | `audit.read`(operator 僅回自身,由後端依 `X-User-Role` 過濾) |
| GET | `/audit/chain/verify` | `audit.read`,回 `{"verified": true, "checked_at": …, "entries": N}` |
| GET | `/audit/export?format=csv` | `audit.export` |

後端自身的所有 mutation(命令、ack、核准、訓練、promotion)**由後端直接寫入**稽核,不依賴 Flask 補記。

---

## 6. 治理核准子系統(P1)

admin approvals 整頁的資料來源。三種核准類型共用一張佇列。

### 6.1 資料模型

```json
{
  "approval_id": "APR-2026-0042",
  "type": "model_promotion | scenario_activation | param_tuning",
  "risk": "low | medium | high",
  "state": "pending → approved | rejected | withdrawn",
  "proposed_by": "user-changwt", "proposed_at": "…",
  "decided_by": null, "decided_at": null, "decision_note": null,
  "summary": {},
  "correlation_id": "…"
}
```

`summary` 依 type 帶截圖所需欄位:

- `model_promotion`:`{"from": "v3.2.0", "to": "v3.2.1", "rmse_improvement_pct": 5.2, "shadow_passed": true, "shadow_window_h": 24}`
- `scenario_activation`:`{"scenario_id": "S04", "name": "高轉速輕載", "similarity_to": "S01", "similarity_pct": 87, "data_quality_pct": 99.1}`
- `param_tuning`:`{"device": "AXIS-04", "param": "Kp", "old": 12.40, "new": 12.75, "delta_pct": 2.8, "allowed_range": [10, 14]}`

### 6.2 端點與規則

| Method | 端點 | Permission |
|---|---|---|
| GET | `/approvals?state=pending&type=&risk=` | `approval.read` |
| GET | `/approvals/summary` | `approval.read`(待辦計數卡:各 type 件數、最久等待) |
| POST | `/approvals/{id}/approve` | `model.promote` 等對應 code,body: `note` |
| POST | `/approvals/{id}/reject` | 同上,body: `note`(必填) |

規則(後端強制,非 UI 約定):
- **同人禁核**:`decided_by != proposed_by`,違反回 `403`。
- 高風險項目可設 `required_approvals: 2`(雙人核准,前端 §14-9 定案後以設定切換)。
- 核准 `model_promotion` 後由後端觸發原子切換,完成時發佈既有 `ws/l3/deploy` 的 `model:changed` 事件——UI 只在收到該事件後更新 active version(前端 §8.4-7)。
- 核准 `scenario_activation` 後進入 Shadow,不直接 active(前端 §8.5-5)。
- 核准 `param_tuning` 後仍需白名單、型別、上下限、變化率、設備狀態五重檢查(前端 §11.3),任一不過即 `failed` 並稽核。

事件:`approval:new`、`approval:decided` 走 Redis `ai_servo:governance` / WS `ws/governance`。

---

## 7. 系統整合狀態(P1)

### `GET /system/integrations`

```json
{
  "services": [
    {"name": "fastapi", "status": "connected", "latency_ms": 12},
    {"name": "redis", "status": "connected", "latency_ms": 3},
    {"name": "postgresql", "status": "connected", "latency_ms": 5},
    {"name": "ntp", "status": "synced", "offset_ms": 2}
  ],
  "version_consistency": {"verified": true, "components": {"api": "0.1.0", "dispatcher": "0.1.0", "schema": "1.0"}},
  "checked_at": "…"
}
```

- 服務斷線/延遲異常時發 `system:connection` 事件(補前端 §9.3 缺口)。
- `version_consistency` 對應 admin 頁「服務版本一致 VERIFIED」。

---

## 8. 維修回報(P1)

| Method | 端點 | Permission |
|---|---|---|
| POST | `/maintenance-reports` | `maintenance.report`(body: `alarm_id?`、`device`、`actions_taken[]`、`result`、`attachments[]?`) |
| GET | `/maintenance-reports?device=&from=&to=` | `alarm.read` |

- 建立回報時可連動 `POST /alarms/{id}/resolve`;後端啟動「殘差恢復觀察」計時,恢復狀態寫回報告(截圖/前端 §7.3 維修回報頁的「殘差恢復狀態」欄位)。

---

## 9. 訓練任務 REST 補全(P1)

既有 `ws/l2/finetune`、`ws/l3/deploy` 只有推播;engineer jobs / shadow 頁還需查詢與觸發:

| Method | 端點 | Permission |
|---|---|---|
| POST | `/training/jobs` | `model.retrain`(body: `type: finetune|full_retrain`、`scenario_id`、`reason`、`data_window`) |
| GET | `/training/jobs?status=&page=` | `model.read` |
| GET | `/training/jobs/{id}` | `model.read`(狀態:`queued/running/evaluating/shadow/passed/failed`,對齊前端 §8.4-3) |
| POST | `/training/jobs/{id}/cancel` | `model.retrain` |
| GET | `/shadow/comparisons?scenario=` | `model.read`(新舊 RMSE、誤報/漏報、延遲、樣本覆蓋——前端 §8.4-4 與 engineer shadow 頁) |

觸發訓練屬 mutation:寫稽核、發 `training:progress` 事件(沿用既有 topic,payload 補 `job_id`、`progress_pct`)。

---

## 10. 資料保留與趨勢聚合(P2)

| Method | 端點 | Permission |
|---|---|---|
| GET/PUT | `/retention/policy` | `system.settings`(7/30/90 天策略、預估容量) |
| GET | `/retention/jobs` | `system.settings`(清理任務狀態、回收量——admin 稽核事件「回收 2.3GB」來源) |
| GET | `/trends?metrics=dv,residual&window=1h|8h|24h&device=` | `trend.read` |

- `/trends` 回**後端彙總**序列(降採樣至 ≤ 500 點/序列),瀏覽器不自行累積(前端 §10.3)。即時 5 分鐘 ring buffer 仍走 WS;歷史窗切換走本端點。

---

## 11. 事件封包統一格式

所有 Redis / WS 事件一律外層信封(對齊前端 §9.3 末段):

```json
{
  "event_id": "uuid",
  "event_type": "alarm:new",
  "timestamp": "UTC ISO8601",
  "scenario_id": "S01",
  "schema_version": "1.0",
  "correlation_id": "…",
  "payload": {}
}
```

- `event_id` 供前端去重(§9.4);`schema_version` 變更走加欄位不刪欄位,破壞性變更升 major 並在 `/system/integrations` 的 version_consistency 反映。
- 高頻來源(inference)由後端節流至 ≤ 10 FPS 再進 Redis UI channel;50 kHz 原始流不進 UI 通道(§9.1)。

---

## 12. 實作順序(對齊 Stage A→E)

| 批次 | 內容 | 解鎖的前端 Stage |
|---|---|---|
| 1 | §1 信任邊界 + §2 Snapshot + §10 trends(唯讀) | Stage A→B:畫面接真資料 |
| 2 | §5 稽核 + §7 整合狀態 | Stage B:admin 頁真資料、登入稽核落地 |
| 3 | §4 警報 + §8 維修回報 | Stage D 第一階(警報確認、維修回報) |
| 4 | §3 命令子系統 | Stage D 第二階(Cycle 命令) |
| 5 | §6 治理核准 + §9 訓練 REST | Stage D 第三階(訓練/上線走核准) |
| 6 | §10 retention + 雙人核准設定 + 匯出 | Stage E 驗收 |

每批次的 Definition of Done:OpenAPI schema 更新、契約測試(前端 §12.1)、稽核覆蓋率 100% mutation、越權測試(以 operator token 打 engineer 端點必須 403)。

---

## 13. 待與前端/現場確認的決策(承接前端 §14)

1. 身分來源定案(本機/AD/OIDC)——影響 §1 是否需改為 JWT 透傳。
2. E-Stop Request 的設備端確認訊號來源與 timeout 值(§3.1)。
3. 雙人核准適用範圍(§6.2 `required_approvals`)。
4. Stage 編號唯一版本、L1 模型唯一規格(前端 §14-6/7)——影響 snapshot `pipeline` 與 `health_cards.model` 欄位語意。
5. 實際 Scenario 數量與 40 Scenarios 交付順序(影響 `/approvals` scenario 流程量級)。
6. 現階段(Mock/測試機)`params` 白名單初版:哪些 Drive/PLC 參數開放調整提案。
