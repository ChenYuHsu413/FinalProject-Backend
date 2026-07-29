# -*- coding: utf-8 -*-
"""run_executor — 常駐迴圈:定期輪詢已核准的 param_tuning 提案,交由
SimExecutor(pmsm_sim 數位孿生)套用並驗證,結果寫入 param_verify/。

原生執行(Windows 為主,不依賴 docker):
    python executor/run_executor.py

以 `python executor/run_executor.py` 執行時,直譯器會把本檔所在目錄
(executor/)放到 sys.path[0],因此 `import pmsm_sim` /
`from sim_executor import ...` 為同層直接 import。下面再顯式插入一次,
讓從其他工作目錄或包裝方式啟動時仍成立。

環境變數:
    BACKEND_API          後端 API base(預設 http://127.0.0.1:8000/api/v1)
    SERVICE_TOKEN        必填。若值以 @ 開頭,視為檔案路徑並讀取其內容為
                         token(配合 dev_stack 的 .localdev\\service_token.txt
                         或 %TEMP%\\b1_token.txt:SERVICE_TOKEN=@C:\\path\\token.txt)
    ENGINE_DATA_DIR      驗證報告輸出根目錄(預設 ./.data/engine)
    EXECUTOR_INTERVAL_S  輪詢間隔秒數(預設 30)

執行身份:user_id=svc-executor, role=engineer。B4 完成後 engineer 已可讀取
核准清單(DEPLOY_MODE=lite 下亦可由 engineer 核准);後端須以 lite 模式執行,
否則 engineer 讀取 /approvals 會得到 403。
"""
from __future__ import annotations

import os
import sys
import time

# 同層直接 import:把 executor/ 放進 sys.path 後再 import 既有模組(不改動它們)。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pmsm_sim  # noqa: E402  — 同層直接 import
from sim_executor import ExecutorLoop, SimExecutor  # noqa: E402


def _resolve_token(raw: str) -> str:
    """SERVICE_TOKEN 支援 @路徑:以 @ 開頭時讀取該檔內容(strip)為 token。"""
    if not raw:
        return ""
    if raw.startswith("@"):
        with open(raw[1:], encoding="utf-8") as f:
            return f.read().strip()
    return raw.strip()


def main() -> None:
    base_url = os.environ.get("BACKEND_API", "http://127.0.0.1:8000/api/v1")
    token = _resolve_token(os.environ.get("SERVICE_TOKEN", ""))
    engine_dir = os.environ.get("ENGINE_DATA_DIR", os.path.join(".", ".data", "engine"))
    interval = float(os.environ.get("EXECUTOR_INTERVAL_S", "30"))

    if not token:
        print("[executor] FATAL: SERVICE_TOKEN 未設定(可用 @檔案路徑)。", flush=True)
        sys.exit(2)

    os.makedirs(engine_dir, exist_ok=True)
    executor = SimExecutor(
        pmsm_sim, state_path=os.path.join(engine_dir, "sim_device_params.json")
    )
    loop = ExecutorLoop(
        base_url, token, executor, engine_dir, user_id="svc-executor", role="engineer"
    )

    # TODO(L2): dv_estimates 未來接 L2 的 EWMA / DOB 擾動扭矩估計。現固定示範值,
    # 讓數位孿生在已知劣化工況(DV≈1600)下量測套參前後表現。
    dv_estimates = {"M07": 1600.0}

    print(
        f"[executor] start | api={base_url} | engine_dir={engine_dir} | "
        f"interval={interval}s | user=svc-executor role=engineer",
        flush=True,
    )
    while True:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            reports = loop.poll_once(dv_estimates=dv_estimates)
            outcomes = ", ".join(f"{r['device']}:{r.get('outcome')}" for r in reports) or "-"
            print(f"[executor] {stamp} processed={len(reports)} [{outcomes}]", flush=True)
        except Exception as exc:  # noqa: BLE001 — 常駐迴圈:任何錯誤印 log 續跑,不中斷
            print(f"[executor] {stamp} ERROR {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
