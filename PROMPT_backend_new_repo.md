# 開發任務 Prompt:AI SERVO PLATFORM 後端(新 repo)+ GCP 部署

> 用法:將本文件與下列三份規格放進新 repo 根目錄,把本文件內容作為開發任務的主 prompt。
> 必讀規格(權威來源,本 prompt 只做裁決與補充,不重複其內容):
> 1. `docs/design-frontend.md` — Flask 前端規劃(角色、頁面、BFF 端點、事件、Stage A–E)
> 2. `docs/design-backend.md` — 治理層後端規格(信任邊界、snapshot、命令、警報、稽核、核准)
> 3. `docs/後端資料規格書.md` — AI 引擎層規格(L1/L2/L3、SHAP、Fallback、排程、檔案儲存、WS topic)

---

## 1. 任務目標

在全新 repo 建立 AI SERVO PLATFORM 的後端,部署於 GCP Compute Engine 單一 VM,以 docker compose 運行。後端由兩層構成,**同一個 FastAPI app、不同 router 群**:

- **治理層(Governance)**:依 `design-backend.md`。命令狀態機、警報生命週期、稽核 hash chain、核准流程、snapshot、系統整合狀態。資料存 PostgreSQL。
- **引擎層(Engine)**:依 `後端資料規格書.md`。L1/L2/L3 唯讀查詢端點、SHAP、Fallback、residual/scenario-library/control-mode/ensemble/data-lifecycle 查詢。資料來源為 ML 管線輸出檔(JSON/JSONL/SQLite)。

現階段(Stage A/B)**沒有真實 50kHz 資料流與真模型服務**:引擎層以 **mock simulator** 產生符合規格 schema 的假資料檔與 Redis 事件。程式介面設計必須讓「換成真管線輸出」只需替換資料來源,不改 API 層。

## 2. 技術棧(已定案,勿更換)

Python 3.12、FastAPI + Pydantic v2、SQLAlchemy 2.0(async)+ Alembic、PostgreSQL 16、redis-py(asyncio)、arq(背景 worker)、pytest + httpx AsyncClient + fakeredis + schemathesis、uvicorn、docker compose。單體分層,不做微服務,不引入 Celery/Kafka/K8s。

## 3. 兩份後端規格的衝突裁決(最重要,先讀)

兩份規格重疊處依下表裁決。實作時遇到本表未列的矛盾:**治理與安全相關以 `design-backend.md` 為準,模型與資料 schema 以 `後端資料規格書.md` 為準**,並在 `docs/DECISIONS.md` 記錄裁決。

| # | 衝突 | 裁決 |
|---|---|---|
| 1 | `後端資料規格書.md` §十一 `POST /api/v1/control/command`(同步回 success)vs `design-backend.md` §3 命令狀態機 | **§十一作廢**。所有控制命令一律走 `design-backend.md` §3:`/commands/*`、202 + `command_id` + `idempotency_key`、`submitted→accepted→completed/failed/timeout`,timeout 由 arq worker 判定。`GET /api/v1/control/status` 保留,欄位併入 snapshot。有效命令集沿用 §十一定義:`ON/OFF/CYCLE_START/CYCLE_STOP/EMERGENCY_STOP`(E-Stop 為 request 語意)。 |
| 2 | 儲存:資料規格書全檔案式(JSONL/JSON/SQLite)vs 治理規格 PostgreSQL | **雙軌並存**。ML 管線產物(`models.jsonl`、`L3_*_ranking.json`、shadow/diagnosis JSON、`shap_logs.jsonl`、`param_changes.jsonl`、`scenario_library.json`)維持檔案式,路徑由 `ENGINE_DATA_DIR` 環境變數統一;治理資料(commands、alarms、audit、approvals、maintenance_reports)一律 PostgreSQL。Fallback 的 SQLite hash chain 屬引擎層保留,但 fallback escalation 發生時治理層**同步開立一筆 alarm**(以 correlation_id 關聯)。 |
| 3 | WS:資料規格書定義 `ws://{host}/ws/{topic}` 直連 vs 前端架構 Flask-SocketIO 訂 Redis | **Topic = Redis channel 命名**(`ai_servo:l1_summary`、`ai_servo:fallback_event`…對映資料規格書 §3.2 全表 + `design-backend.md` 新增的 command/alarm/governance/system channel)。FastAPI **只發佈 Redis,不對瀏覽器開 WS**。可另提供 `/ws/debug/{topic}` 單一除錯端點(僅 dev profile 啟用)。事件一律用 `design-backend.md` §11 統一信封(event_id/event_type/timestamp/scenario_id/schema_version/correlation_id/payload),資料規格書 §3.2 的 payload 塞進 `payload` 欄位。 |
| 4 | `後端資料規格書.md` §十二 expert-intervention API vs 治理層核准+稽核 | **併入治理層**。專家手動調參 = `param_tuning` 核准流程(`design-backend.md` §6);純記錄型介入寫入稽核表(action=`expert_intervention`)。不另建 `expert_logs.jsonl`;`GET /expert-intervention` 以稽核查詢 + filter 實作,回傳格式相容資料規格書定義。 |
| 5 | Scenario ID 格式(`01_Pick_and_Place` vs `S01`) | **後端統一長格式** `01_Pick_and_Place`(資料規格書為準,三個 active:01/18/34,庫容量 40)。前端顯示縮寫由 Flask normalizer 處理,後端不出現 `S01`。 |
| 6 | 資料保留:資料規格書 §十 `GET /api/v1/data-lifecycle`(唯讀)vs 治理規格 §10 `GET/PUT /retention/policy` | 兩者合併:讀取端點依資料規格書 schema;`PUT` 修改走治理層(permission `system.settings` + 稽核)。 |
| 7 | 路徑前綴 | 全部端點掛 `/api/v1/`。`design-backend.md` 中未帶前綴的端點補上(如 `/api/v1/commands/cycle/start`)。 |

## 4. Repo 結構

```
backend/
├── app/
│   ├── main.py                  # app factory + lifespan(DB/Redis 連線、dev 時啟動 simulator)
│   ├── core/                    # settings(pydantic-settings)、security(service token + X-User-* 驗證)、errors(統一錯誤格式)、permissions(角色→permission code 單一對照表)
│   ├── domain/                  # 純邏輯、不碰 IO:command 狀態機、alarm 生命週期、approval 規則(同人禁核)、fallback 鏈(資料規格書 §五)、control-mode 狀態機(§九)
│   ├── routers/
│   │   ├── governance/          # snapshot、commands、alarms、audit、approvals、maintenance、integrations、trends、retention
│   │   └── engine/              # l1、l2、l3、shap、fallback、residual、scenario_library、ensemble、control_mode、data_lifecycle、scenarios
│   ├── services/                # 業務編排
│   ├── repositories/
│   │   ├── pg/                  # SQLAlchemy(治理資料)
│   │   └── files/               # 引擎檔案讀取(ENGINE_DATA_DIR),介面化以便日後換真管線
│   ├── events/                  # Redis publisher、事件信封 model、channel 常數
│   └── mock/                    # simulator:產生引擎層假資料檔 + 依排程表(資料規格書 §十三)發 Redis 事件 + 造治理假資料(待核准項、警報、稽核)
├── worker/                      # arq:command timeout 掃描(每秒)、稽核鏈重驗(每小時)、資料清理(每日)、mock 排程觸發
├── alembic/                     # 含稽核表 append-only 三層防護 migration(應用層禁改、REVOKE UPDATE/DELETE、BEFORE trigger RAISE)
├── tests/                       # unit(domain 狀態機全轉移路徑)/ contract(schemathesis 打 OpenAPI)/ integration(越權測試:operator token 打 engineer 端點必 403)
├── deploy/                      # compose、Caddyfile、GCP 指令稿(見 §6)
├── docs/                        # 三份規格 + DECISIONS.md
├── docker-compose.yml           # api / worker / postgres / redis(+ 既有 flask 前端服務)
├── .env.example
└── README.md                    # 含「是什麼/不是什麼」誠實限制章節
```

## 5. 實作順序(依序交付,每批次過測試才進下一批)

1. **骨架**:app factory、settings、統一錯誤格式、service token + X-User-* middleware、permissions 表 + `GET /api/v1/authz/permissions`、CI(ruff + pytest + docker build)。
2. **稽核子系統**:PG 表 + hash chain + 三層 append-only 防護 + `/audit/*` 端點 + worker 重驗任務。所有後續 mutation 都依賴它。
3. **引擎層唯讀端點 + mock simulator**:實作 `後端資料規格書.md` §二/§七/§八/§九/§十 全部 GET 端點,資料來源為 simulator 產生的檔案;simulator 依 §十三排程表發 Redis 事件(1s summary、1min finetune、事件型 fallback/shap)。
4. **Snapshot + trends**:`design-backend.md` §2/§10,聚合引擎層資料。此批完成 = Flask 前端可全面接真格式(Stage A→B)。
5. **警報 + 維修回報**:`design-backend.md` §4/§8;fallback escalation 自動開 alarm。
6. **命令子系統**:`design-backend.md` §3(含裁決 #1),worker timeout 掃描,`command:status` 與 `mode:changed` 事件。
7. **核准 + 訓練 REST + 整合狀態**:`design-backend.md` §6/§7/§9;model promotion 核准後改寫 `models.jsonl` status 並發 `model:changed`。
8. **retention 合併端點 + 匯出 + 部署硬化**(§6)。

每批 Definition of Done:OpenAPI 更新、該批 schemathesis 契約測試通過、mutation 100% 寫稽核、越權測試通過、`docs/DECISIONS.md` 記錄本批裁決。

## 6. GCP 部署(Compute Engine 單 VM + docker compose)

**架構**:一台 VM 跑五個 compose 服務:`caddy`(反向代理 + 自動 HTTPS)→ `flask`(前端,唯一對外)、`api`(FastAPI,僅內網)、`worker`、`postgres`、`redis`。所有服務同一 docker network;Postgres/Redis/FastAPI **不建對外防火牆規則、不 publish port 到 host**(只有 caddy publish 80/443)。

**deploy/ 需交付**:

1. `docker-compose.yml` + `docker-compose.prod.yml`(prod:關 debug WS、關 simulator 或以 `MOCK_MODE=true` 顯式開啟、restart: unless-stopped、healthcheck 全服務)。
2. `Caddyfile`:反代 flask;若無網域先用 `:80` + IP,保留換網域啟用自動 TLS 的註解。
3. `gcp-setup.sh`(冪等,可重跑):
   - 防火牆:僅開 80/443(`--target-tags=web`),移除既有 5000 對外規則;SSH 建議走 IAP(`gcloud compute ssh --tunnel-through-iap`),不開 22 對公網。
   - VM 建議規格:`e2-medium`(2 vCPU/4GB)、Ubuntu 24.04 LTS、開機磁碟 30GB + 額外 Persistent Disk 20GB 掛 `/srv/data`(Postgres volume 與 `ENGINE_DATA_DIR` 都放這裡)。
   - 快照排程:`gcloud compute resource-policies create snapshot-schedule daily-backup --daily-schedule --start-time=18:00 --max-retention-days=14` 並綁定資料磁碟。
4. `deploy.sh`:git pull → build → `alembic upgrade head` → `compose up -d` → healthcheck 驗證;支援 `rollback`(前一 image tag)。
5. Secrets:`.env` 只存 VM 上(`chmod 600`),repo 只有 `.env.example`;service token、DB 密碼在 `gcp-setup.sh` 以 `openssl rand -hex 32` 產生。不用 GCP Secret Manager(單 VM 階段過度工程),但程式讀取介面留抽換空間。
6. 備份除磁碟快照外,加每日 `pg_dump` 到 `/srv/data/backups/`(worker 排程),保留 7 份。
7. `docs/DEPLOYMENT.md` runbook:從零開機到服務上線、驗證清單(healthcheck、稽核鏈 verify、Flask 登入走通)、災難復原(從快照重建)、90 天試用期滿的遷移步驟(compose + 資料磁碟可整包搬遷)。

## 7. 全域約束

- 瀏覽器永不直連 FastAPI/Redis/Postgres;FastAPI 僅信任帶 service token 的 Flask 請求;mutation 缺 `X-Correlation-ID`/`X-User-ID`/`X-User-Role` 一律 400。
- 命令逾時維持 `timeout`,不推定成功或失敗;HTTP 200/202 不等於設備已執行。
- 高頻事件節流:UI 通道 ≤ 10 FPS(`ws/l1/inference` 對映 channel 依此降頻);50kHz 僅存在於引擎層敘事,不進 UI 通道。
- 時間一律 UTC ISO8601;`schema_version` 只加欄位不刪欄位。
- 誠實敘述:README 與 API 描述不得宣稱「IEC 61508 已認證」「已連接真實設備」;mock 模式須在 `/api/v1/system/integrations` 回應中標示 `"mock_mode": true`。
- 不做:使用者 CRUD(屬 Flask)、SSO、K8s、微服務拆分、真實 EtherCAT/PLC 介接。

## 8. 首個里程碑驗收(批次 1–4 完成時)

- `docker compose up` 一鍵起全部服務,`/api/v1/health` 綠燈。
- Flask 前端(既有 mock 版)改指向本 API 後,operator console / engineer health / admin approvals 三頁所有欄位皆由本後端供應,無前端硬編碼假資料。
- `GET /api/v1/audit/chain/verify` 回 `verified: true`。
- schemathesis 對 OpenAPI 全端點跑通;`pytest` 全綠;以 operator 身分呼叫 engineer 端點得 403 且該次嘗試出現在稽核表。
