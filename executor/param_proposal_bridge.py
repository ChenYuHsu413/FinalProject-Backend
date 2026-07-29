# -*- coding: utf-8 -*-
"""param_proposal_bridge — 將 recommend_v2 的建議轉為後端正式提案。

流程:診斷觸發 → recommend_v2 產出行動 → 本模組將「可執行」的行動
(白名單內、變化率 ≤ 後端上限)組成 param_tuning 提案 → 以 engineer
身份 POST /approvals → 進入 admin 核准佇列與稽核鏈。

對映(後端白名單 = {Kp, Ki, Acc},單步變化率上限 10%):
  tier-3 降載   → Acc 每步 −10%(−50% 需分五步,依後端變化率政策分段)
  動態型整定    → Kp/Ki ±10% 內
  tier-2 DOB    → 非數值參數,不在白名單 → 保留於 reason 供人工參採
  tier-1 維修   → 非參數,由告警/維修單流程承接
"""
from __future__ import annotations

import uuid

import requests

DEFAULT_BOUNDS = {           # commissioning 界限(per-asset 可覆寫)
    "Kp": (10.0, 30.0),
    "Ki": (20.0, 120.0),
    "Acc": (200.0, 800.0),
}
MAX_STEP_PCT = 10.0          # 與後端 check_param_tuning.MAX_DELTA_PCT 一致


class ProposalBridge:
    def __init__(self, base_url, token, user_id="svc-diagnosis", role="engineer",
                 bounds=None):
        self.base = base_url.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}",
                  "X-User-ID": user_id, "X-User-Role": role}

        self.bounds = dict(DEFAULT_BOUNDS); self.bounds.update(bounds or {})

    def _headers(self):
        return {**self.h, "X-Correlation-ID": str(uuid.uuid4())}

    def _propose(self, *, device, scenario_id, param, current, target, risk, reason):
        lo, hi = self.bounds[param]
        target = max(lo, min(hi, target))
        delta_pct = round((target - current) / current * 100, 1)
        if abs(delta_pct) > MAX_STEP_PCT:      # 依後端變化率政策截步
            target = round(current * (1 + (MAX_STEP_PCT if delta_pct > 0 else -MAX_STEP_PCT) / 100), 3)
            delta_pct = MAX_STEP_PCT if delta_pct > 0 else -MAX_STEP_PCT
        body = {"type": "param_tuning", "risk": risk,
                "scenario_id": scenario_id, "device": device,
                "reason": reason[:1000],
                "summary": {"param": param, "current": current, "new": target,
                            "allowed_range": [lo, hi], "delta_pct": delta_pct}}
        r = requests.post(f"{self.base}/approvals", json=body, headers=self._headers(), timeout=10)
        r.raise_for_status()
        return r.json()

    def submit_from_recommendation(self, actions, *, device, scenario_id,
                                   current_params, severity="medium"):
        """actions: recommend_v2() 之輸出;回傳建立的提案清單。"""
        created = []
        for a in actions:
            if a["type"] == "derating":
                cur = current_params["Acc"]
                created.append(self._propose(
                    device=device, scenario_id=scenario_id, param="Acc",
                    current=cur, target=cur * 0.9, risk=severity,
                    reason=f"[recommend_v2 tier-3 降載] {a['action']}|依據:{a['basis']}"
                           f"|後端變化率政策:每步 −10%,目標 −50% 需分段提案"))
            elif a["type"] == "tuning":
                cur = current_params["Kp"]
                created.append(self._propose(
                    device=device, scenario_id=scenario_id, param="Kp",
                    current=cur, target=cur * 1.08, risk="low",
                    reason=f"[recommend_v2 動態型整定] {a['action']}|依據:{a['basis']}"))
            # compensation(DOB)與 maintenance/monitoring 不產生參數提案:
            # 前者非白名單數值參數,後兩者由告警與維修單流程承接。
        return created
