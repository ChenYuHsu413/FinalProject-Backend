# -*- coding: utf-8 -*-
"""sim_executor — 已核准調參提案的模擬設備執行器(數位孿生)。

流程:輪詢 GET /approvals?type=param_tuning&state=approved
  → 過濾 side_effect_status == "applied"(已通過後端五重檢查)且未執行者
  → 對該 device 的模擬器實例套用參數 → 執行前後驗證 transition
  → 改善或持平 → verified;劣化超過容忍 → 回滾參數 → rolled_back
  → 驗證報告寫入 ENGINE_DATA_DIR/param_verify/,供前端調整提案頁呈現。

設計:ExecutorInterface 抽象;現掛 SimExecutor(pmsm_sim 數位孿生),
未來接實機時實作 DriveExecutor(SLMP/Modbus)即可,治理鏈不變。
"""
from __future__ import annotations

import json
import os
import time

import uuid

import requests

PARAM_MAP = {"Kp": "kp_pos", "Ki": "ki_vel", "Acc": "a_max"}   # 提案參數 → 模擬器參數
REGRESSION_TOL = 1.05        # 追隨誤差劣化 >5% 即回滾


class SimExecutor:
    """以 pmsm_sim 為受控設備;每台 device 一份參數狀態。"""

    def __init__(self, sim_module, state_path="sim_device_params.json"):
        self.sim = sim_module
        self.state_path = state_path
        self.params = self._load()

    def _load(self):
        if os.path.exists(self.state_path):
            return json.load(open(self.state_path, encoding="utf-8"))
        return {}

    def _save(self):
        json.dump(self.params, open(self.state_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    def device_params(self, device):
        if device not in self.params:
            self.params[device] = {"Kp": self.sim.BASE["kp_pos"],
                                   "Ki": self.sim.BASE["ki_vel"],
                                   "Acc": self.sim.BASE["a_max"]}
        return self.params[device]

    def _gains(self, p):
        return {"kp_pos": p["Kp"], "ki_vel": p["Ki"], "a_max": p["Acc"]}

    def _measure(self, gains, dv):
        r = self.sim.simulate_transition(dv=dv, gains=gains)
        m = self.sim.metrics(r)
        return {"follow_err_mrad": round(m["follow_err_mrad"], 1),
                "settle_ms": round(m["settle_ms"], 0),
                "iq_plateau_A": round(m["iq_plateau_A"], 2),
                "iq_peak_A": round(float(abs(r["iq"]).max()), 1)}

    def apply_and_verify(self, device, param, new_value, dv_estimate,
                         proposal_current=None, stale_tol=0.02):
        """套用參數,前後量測,劣化即回滾。回傳驗證報告 dict。

        staleness 檢查:提案的 current 若與設備現值偏差 > stale_tol(相對),
        代表提案基於過期狀態,拒絕執行(outcome=skipped_stale)。"""
        p = self.device_params(device)
        old_value = p[param]
        if proposal_current is not None and abs(old_value) > 1e-9 and \
           abs(proposal_current - old_value) / abs(old_value) > stale_tol:
            return {"device": device, "param": param, "old": old_value,
                    "new": float(new_value), "proposal_current": proposal_current,
                    "outcome": "skipped_stale",
                    "executed_on": "simulated_device",
                    "note": "提案 current 與設備現值不符,基於過期狀態,拒絕執行"}
        before = self._measure(self._gains(p), dv_estimate)
        p[param] = float(new_value)
        after = self._measure(self._gains(p), dv_estimate)
        regressed = after["follow_err_mrad"] > before["follow_err_mrad"] * REGRESSION_TOL
        if regressed:
            p[param] = old_value           # 回滾
        self._save()
        return {"device": device, "param": param,
                "old": old_value, "new": float(new_value),
                "dv_estimate": dv_estimate,
                "before": before, "after": after,
                "outcome": "rolled_back" if regressed else "verified",
                "executed_on": "simulated_device",     # 誠實標示
                "peak_current_delta_pct": round(
                    (after["iq_peak_A"] - before["iq_peak_A"]) / before["iq_peak_A"] * 100, 1)}


class ExecutorLoop:
    def __init__(self, base_url, token, executor, engine_data_dir,
                 user_id="svc-executor", role="admin"):
        self.base = base_url.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}",
                  "X-User-ID": user_id, "X-User-Role": role}

        self.ex = executor
        self.out_dir = os.path.join(engine_data_dir, "param_verify")
        os.makedirs(self.out_dir, exist_ok=True)
        self.done_path = os.path.join(self.out_dir, "_executed.json")
        self.done = set(json.load(open(self.done_path))) if os.path.exists(self.done_path) else set()

    def _headers(self):
        return {**self.h, "X-Correlation-ID": str(uuid.uuid4())}

    def poll_once(self, dv_estimates=None):
        """執行一輪;dv_estimates: {device: 目前 DV 估計}(來自 L2/DOB)。"""
        r = requests.get(f"{self.base}/approvals",
                         params={"type": "param_tuning", "state": "approved"},
                         headers=self._headers(), timeout=10)
        r.raise_for_status()
        reports = []
        for a in r.json()["approvals"]:
            if a["approval_id"] in self.done:
                continue
            if a.get("side_effect_status") != "applied":     # 未過五重檢查者不執行
                self.done.add(a["approval_id"]); continue
            s = a["summary"] or {}
            dv = (dv_estimates or {}).get(a["device"], 0.0)
            rep = self.ex.apply_and_verify(a["device"], s["param"], s["new"], dv,
                                           proposal_current=s.get("current"))
            rep.update({"approval_id": a["approval_id"],
                        "approved_by": a.get("decided_by"),
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            path = os.path.join(self.out_dir, f"{a['device']}_{a['approval_id']}.json")
            json.dump(rep, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            self.done.add(a["approval_id"])
            reports.append(rep)
        json.dump(sorted(self.done), open(self.done_path, "w"))
        return reports
