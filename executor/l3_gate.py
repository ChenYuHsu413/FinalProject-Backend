# -*- coding: utf-8 -*-
"""L3 雲端層:shadow 閘門評估 + 版本 registry。

閘門準則(全部須通過才晉升):
  G1  run 層級 R² > 0.90
  G2  MAE 優於 naive(訓練集平均值)baseline
  G3  LO 敏感區(DV 10~900)MAE < 120 —— 針對已知資料缺陷區間的守門
所有評估在「稽核集」(有 SME 檢測標籤的 run)上進行,對應論文中
DV/DL 可由檢測程序取得的假設;重播 demo 中以留出標籤扮演稽核標籤。
"""
import json
import time
import numpy as np
from sklearn.metrics import r2_score, mean_absolute_error

GATES = {"G1_r2_min": 0.90, "G2_beat_naive": True, "G3_lo_mae_max": 120.0}


def evaluate_candidate(y_true, y_pred, y_naive):
    lo_mask = (y_true > 10) & (y_true < 900)
    m = {
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 1),
        "naive_mae": round(float(mean_absolute_error(y_true, y_naive)), 1),
        "lo_mae": round(float(mean_absolute_error(y_true[lo_mask], y_pred[lo_mask])), 1)
        if lo_mask.any() else None,
    }
    checks = {
        "G1_r2": m["r2"] > GATES["G1_r2_min"],
        "G2_naive": m["mae"] < m["naive_mae"],
        "G3_lo": (m["lo_mae"] is None) or (m["lo_mae"] < GATES["G3_lo_mae_max"]),
    }
    m["gates"] = checks
    m["decision"] = "PROMOTE" if all(checks.values()) else "ROLLBACK"
    return m


class Registry:
    """append-only JSONL 事件 registry。"""

    def __init__(self, path):
        self.path = path

    def log(self, event_type, payload):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": event_type, **payload}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


# 調參建議模板(demo 性質,明示非閉環控制)
PARAM_TEMPLATE = {
    "torque": [("P1-40", "位置環增益", "檢視是否因負載上升需重新整定")],
    "iq": [("P1-44", "速度環增益", "檢視電流響應餘裕")],
    "generic": [("維護動作", "機構檢查", "對應阻力負載上升:檢查螺桿潤滑/異物/棒體卡滯")],
}


def diagnosis_note(top_features):
    notes = list(PARAM_TEMPLATE["generic"])
    joined = " ".join(top_features)
    if "torque" in joined:
        notes += PARAM_TEMPLATE["torque"]
    if "iq" in joined:
        notes += PARAM_TEMPLATE["iq"]
    return [
        {"item": a, "name": b, "note": c, "disclaimer": "規則模板示範,非閉環控制"}
        for a, b, c in notes
    ]

# ===== 研究改進版建議邏輯(2026-07-26)=====
# 依據:DOB 摩擦補償文獻(IEEE 509430 等)、US7719221 專利(補償扭矩兼作
# 劣化監測)、三菱 MR-J5 機械診斷/摩擦估測/lost motion 補償架構、
# 健康感知控制(HAC)文獻(以降載換取 RUL 延長)。
# 核心規則:雙症狀判讀 ——
#   症狀一(追隨誤差/整定劣化):可由控制側「補償」
#   症狀二(平台電流/扭矩上升):任何控制動作皆無法消除(物理:馬達必須
#   供應克服阻力的扭矩),唯機構維修可治本。
# 模擬驗證(pmsm_sim, DV=1600):增益+10% 誤差僅 -4%;DOB 前饋誤差
# 45.1 mrad ≈ 健康值 44.1;降載 a_max-50% 峰值電流 -22%(週期 +32%);
# 平台電流在所有控制動作下不變(7.3A),僅機構修復回到 0.65A。

def recommend_v2(symptoms):
    """symptoms: dict(track_err_up=bool, plateau_current_up=bool,
                      oscillation_up=bool, severity=str)"""
    acts = []
    if symptoms.get("plateau_current_up"):
        acts.append({"tier": 1, "type": "maintenance",
            "action": "排定機構檢修(螺桿潤滑/異物/棒體卡滯)",
            "basis": "平台電流上升 = 阻力負載,唯一治本",
            "urgency": symptoms.get("severity", "medium")})
        acts.append({"tier": 2, "type": "compensation",
            "action": "啟用/調整擾動前饋補償(如 MR-J5 lost motion comp "
                      "PE44/PE48 或 DOB)以維持精度至維修窗口",
            "basis": "模擬驗證:追隨誤差可回復至健康值;不減少馬達負荷",
            "caveat": "補償為過渡措施,發熱與磨損持續"})
        acts.append({"tier": 3, "type": "derating",
            "action": "降低加減速(P1-42)或工作週期,峰值電流與熱應力下降",
            "basis": "健康感知控制:以節拍換取 RUL 延長(模擬:峰值 -22%)"})
        acts.append({"tier": 4, "type": "monitoring",
            "action": "以估測擾動扭矩作為健康指標持續追蹤",
            "basis": "US7719221 / MR-J5 摩擦估測:補償器兼作劣化感測器"})
        acts.append({"tier": 0, "type": "not_recommended",
            "action": "提高位置/速度環增益",
            "basis": "模擬驗證對阻力型劣化無效(誤差 -4%),且增加應力"})
    if symptoms.get("oscillation_up") and not symptoms.get("plateau_current_up"):
        acts.append({"tier": 1, "type": "tuning",
            "action": "增益/濾波整定(one-touch tuning 適用域):共振抑制、"
                      "阻尼調整",
            "basis": "動態型症狀(震盪/過衝)方為增益調整之正當場合"})
    for a in acts:
        a["disclaimer"] = "建議僅供審核,非閉環控制"
    return acts
