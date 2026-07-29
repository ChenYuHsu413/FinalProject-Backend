# Bypass Executor — 數位孿生調參驗證 (L1)

一個**旁路執行器**:與治理後端**只透過 HTTP API** 互動,把「已核准且已通過後端
五重檢查」的 `param_tuning` 提案,套用到 `pmsm_sim` 數位孿生上,量測套參前後的
伺服表現,改善/持平則 `verified`、劣化則自動回滾 `rolled_back`,並把驗證報告寫入
`ENGINE_DATA_DIR/param_verify/` 供前端「調整提案頁」呈現。

治理鏈不變:提案 → 人類核准 → 後端五重檢查 → **本執行器驗證**。執行器不改任何
`app/` 治理層程式,身分為 `user_id=svc-executor, role=engineer`。

## 檔案

| 檔案 | 角色 |
|---|---|
| `pmsm_sim.py` | 簡化 FMCRD/PMSM 向量控制數位孿生(梯形規劃 + 位置/速度環 + 劣化注入) |
| `sim_executor.py` | `SimExecutor`(套參+前後量測+**回滾**+**staleness 檢查**)與 `ExecutorLoop`(輪詢/去重/寫報告) |
| `l3_gate.py` | L3 shadow 閘門評估 + `recommend_v2` 建議邏輯(非 L1 迴圈路徑) |
| `param_proposal_bridge.py` | 將 `recommend_v2` 建議轉為後端 `param_tuning` 提案(engineer 身分 POST) |
| `run_executor.py` | 常駐迴圈進入點(本次新增) |
| `requirements.txt` | 執行器**獨立**依賴(不進主專案 `pyproject.toml`) |

`sim_executor.py` / `pmsm_sim.py` / `l3_gate.py` / `param_proposal_bridge.py` 的類別
介面與既有邏輯(含 staleness 與回滾)維持原狀,未改動。

## 安裝

執行器的數值堆疊與後端刻意分離(它只經 HTTP 互動),依賴獨立列於
`executor/requirements.txt`,不動主專案的依賴宣告:

```bash
.venv\Scripts\python.exe -m pip install -r executor/requirements.txt
```

（`numpy` / `requests` 為 L1 迴圈所需;`scikit-learn` 供 `l3_gate` 指標計算,L1
迴圈不使用。）

## 執行(Windows 原生,不依賴 docker)

```bash
python executor/run_executor.py
```

環境變數:

| 變數 | 預設 | 說明 |
|---|---|---|
| `BACKEND_API` | `http://127.0.0.1:8000/api/v1` | 後端 API base |
| `SERVICE_TOKEN` | （必填） | 服務 token;**值以 `@` 開頭時視為檔案路徑**並讀取其內容 |
| `ENGINE_DATA_DIR` | `./.data/engine` | 驗證報告輸出根目錄(`param_verify/` 於其下) |
| `EXECUTOR_INTERVAL_S` | `30` | 輪詢間隔秒數 |

`@` 檔案路徑用法(配合 `scripts/dev_stack.ps1` 產生的 token,或 `%TEMP%\b1_token.txt`):

```powershell
$env:SERVICE_TOKEN = "@$PWD\.localdev\service_token.txt"
python executor/run_executor.py
```

例外會被 catch、印 log 後續跑;每輪印出處理筆數與各筆 outcome。

> **後端需以 `DEPLOY_MODE=lite` 執行**:執行器以 `role=engineer` 讀取核准清單,
> B4(DEPLOY_MODE 開關,DECISIONS D1.8)完成後 engineer 才可讀取/核准
> `param_tuning`。若後端為 `full` 模式,執行器的 `GET /approvals` 會得到 403。
> 用 `pwsh scripts/dev_stack.ps1 -DeployMode lite` 起後端。

## 驗收 (Acceptance)

以下步驟已於 dev 環境實測通過(Windows + `scripts/dev_stack.ps1` 起的後端 + 本地
Postgres)。

**前置**:安裝依賴;後端以 lite 模式執行。

```powershell
pwsh scripts/dev_stack.ps1 -DeployMode lite     # (a) 後端
$env:BACKEND_API   = "http://127.0.0.1:8000/api/v1"
$env:SERVICE_TOKEN = "@$PWD\.localdev\service_token.txt"
$env:ENGINE_DATA_DIR = "$PWD\.data\engine"
$env:EXECUTOR_INTERVAL_S = "5"
python executor/run_executor.py                 # (a) 執行器(另一個終端)
```

**(b) 以 API 建立並核准一筆 `param_tuning` 提案(Acc, delta −10%)**
— 提案由服務身分提出、由**不同 user_id** 的 engineer 核准(職責分立;同人禁核):

```powershell
$h = @{ Authorization="Bearer $((Get-Content .localdev\service_token.txt -Raw).Trim())"; "X-Correlation-ID"=[guid]::NewGuid() }
$propose = $h + @{ "X-User-ID"="svc-diagnosis"; "X-User-Role"="engineer" }
$approve = $h + @{ "X-User-ID"="eng-1";         "X-User-Role"="engineer" }
$body = @{ type="param_tuning"; risk="low"; scenario_id="01_Pick_and_Place"; device="M07";
  reason="acceptance"; summary=@{ param="Acc"; current=600.0; new=540.0; allowed_range=@(200.0,800.0); delta_pct=-10.0 } } | ConvertTo-Json -Depth 5
$p = Invoke-RestMethod "$env:BACKEND_API/approvals" -Method Post -Body $body -ContentType application/json -Headers $propose
Invoke-RestMethod "$env:BACKEND_API/approvals/$($p.approval_id)/approve" -Method Post -Body (@{note="ok"}|ConvertTo-Json) -ContentType application/json -Headers $approve
```

**(c)** 一個輪詢週期內,執行器 log 出現 `outcome=verified`:

```
[executor] 2026-07-29T02:35:10Z processed=1 [M07:verified]
```

**(d)** `ENGINE_DATA_DIR/param_verify/M07_{approval_id}.json` 出現,內含 before/after
量測與 `executed_on=simulated_device`:

```json
{
 "device": "M07", "param": "Acc", "old": 600.0, "new": 540.0, "dv_estimate": 1600.0,
 "before": { "follow_err_mrad": 64.8, "settle_ms": 278.0, "iq_plateau_A": 7.33, "iq_peak_A": 16.2 },
 "after":  { "follow_err_mrad": 61.1, "settle_ms": 288.0, "iq_plateau_A": 7.36, "iq_peak_A": 15.5 },
 "outcome": "verified", "executed_on": "simulated_device", "peak_current_delta_pct": -4.3,
 "approval_id": "APR-...", "approved_by": "eng-1", "ts": "..."
}
```

### staleness / 回滾 是活的(既有邏輯)

- **staleness**:提案的 `summary.current` 與孿生設備現值相對偏差 > 2% ⇒
  `outcome=skipped_stale`(提案基於過期狀態,拒絕執行)。因此若同一設備已被前一筆
  提案調過(例如 Acc 已 600→540),後續 `current=600` 的提案會被正確判為 stale。
  重跑乾淨的 `verified` demo 時,可將孿生狀態重置回基準:把
  `ENGINE_DATA_DIR/sim_device_params.json` 內容改為 `{}`(設備回 `BASE`),
  `param_verify/_executed.json` 改為 `[]`(清除去重集)。
- **回滾**:套參後追隨誤差較套參前劣化 > 5% ⇒ 自動回滾參數,`outcome=rolled_back`。

### 已知限制(不在本次修改範圍)

後端的核准是「決策 ≠ 套用」(DECISIONS D7.3):`approve` 會**先** commit
`state=approved`,**再**於後續 commit 設 `side_effect_status=applied`,兩者之間有一
個短窗(含一次事件發布)。`sim_executor.py` 現行邏輯把「已核准但 `side_effect_status`
尚非 `applied`」與「五重檢查失敗(terminal)」一視同仁地放進去重集永久略過——若某次
輪詢**剛好**落在該短窗,該提案會被永久 blackhole。正常運作(輪詢間隔 30s、核准早已
落定)幾乎不會踩到;但這是既有邏輯的一個 latent bug。因本次要求不得改動既有類別
邏輯,未於此修正。**建議修法**:僅當 `side_effect_status == "failed"`(terminal)時
才加入去重集;為 `None`(尚未套用)時本輪略過、下輪重試。

## (可選)docker compose

`docker-compose.yml` 已加一個 `executor` 服務,與 `api` 共享 engine volume
(`${DATA_ROOT:-./.data}/engine`),`BACKEND_API` 指向 `api:8000`。此服務為便利性質、
未在本機以 docker 實測(執行器主要目標為 Windows 原生執行);其失敗不影響原生流程。
使用時 `.env` 需設 `DEPLOY_MODE=lite` 與 `SERVICE_TOKEN`。

```bash
docker compose up executor
```
