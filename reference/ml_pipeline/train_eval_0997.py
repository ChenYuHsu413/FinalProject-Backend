"""train_eval_0997.py — 由特徵表到 R²0.9973 的正典管線(單檔可重現)。

前置:先以 extract_features.py 從 FMCRD_Data.zip 產出
      train_features.csv / test_features.csv(57 欄 = 6 中繼 + 51 特徵)。

管線(與報告《FMCRD_誤差與演算法總表》一致):
  [1] 50 維入模(剔除 6 中繼欄與零變異之 has_motion)
  [2] 冠軍組合 = (ExtraTrees×500 + 單調約束 HistGB×600) / 2,seed=0
  [3] run 聚合:同 run 5 個 transition 預測取中位數
  [4] OOF 等張校正:GroupKFold(5, 以 run 分組) 之 out-of-fold 預測
      於 run 層級擬合 IsotonicRegression(規則:先聚合、再校正)
  [5] 貝氏分級:校正 DV 對四個已知高斯 LN(0,2)/LO(400,200)/
      MED(1600,300)/HI(3200,500) 最大似然,變異數加計 OOF 殘差 std
  [6] 指標:run 層級 R²/MAE/RMSE + 各級分解 + 分類混淆矩陣

用法:
  python train_eval_0997.py                 # 主結果
  python train_eval_0997.py --checks        # 附三項防偽對照(較耗時)

評估協定聲明:策略選擇依訓練集 CV;測試集單次評估。
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
)
from sklearn.model_selection import GroupKFold

TRAIN_CSV = os.environ.get("FMCRD_TRAIN_CSV", "feat/train_features.csv")
TEST_CSV = os.environ.get("FMCRD_TEST_CSV", "feat/test_features.csv")
META = ["split", "file_level", "run_index", "transition_id", "DV", "ylabel"]
SEED = 0
MONO_FEATS = [  # 物理單調(阻力↑ → 該量↑)之特徵,施加於 HistGB
    "torque_act_std",
    "iq_act_std",
    "torque_act_rms",
    "iq_act_rms",
    "i3p_rms_mean",
    "torque_act_p2p",
    "iq_act_p2p",
    "torque_act_maxabs",
    "iq_act_maxabs",
    "torque_plateau_med",
    "iq_plateau_med",
    "steady_err_mean",
]
LEVELS = {"LN": (0, 2), "LO": (400, 200), "MED": (1600, 300), "HI": (3200, 500)}
ORDER = ["LN", "LO", "MED", "HI"]


def rmse(a, b):
    return float(np.sqrt(mean_squared_error(a, b)))


def run_median(df, pred):
    d = df[["run_index"]].copy()
    d["p"] = pred
    return d.groupby("run_index")["p"].median()


def main(checks: bool) -> None:
    tr = pd.read_csv(TRAIN_CSV)
    te = pd.read_csv(TEST_CSV)
    feats = [c for c in tr.columns if c not in META and c != "has_motion"]
    assert len(feats) == 50, f"預期 50 維入模,得到 {len(feats)}"
    Xtr, ytr, grp = tr[feats].values, tr["DV"].values, tr["run_index"].values
    Xte = te[feats].values
    te_run = te.groupby("run_index").agg(DV=("DV", "first"), ylabel=("ylabel", "first"))
    print(
        f"train {len(tr)} transitions / {tr.run_index.nunique()} runs | "
        f"test {len(te)} / {len(te_run)}"
    )

    # [2] 冠軍組合
    et = ExtraTreesRegressor(n_estimators=500, n_jobs=-1, random_state=SEED).fit(Xtr, ytr)
    mono = np.array([1 if f in MONO_FEATS else 0 for f in feats])
    hg = HistGradientBoostingRegressor(max_iter=600, random_state=SEED, monotonic_cst=mono).fit(
        Xtr, ytr
    )
    pred_t = (et.predict(Xte) + np.clip(hg.predict(Xte), 0, None)) / 2

    # [3] run 聚合
    run_raw = run_median(te, pred_t).reindex(te_run.index)

    # [4] OOF 等張校正(run 層級擬合)
    oof = np.zeros_like(ytr)
    for a, b in GroupKFold(5).split(Xtr, ytr, grp):
        oof[b] = (
            ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=SEED)
            .fit(Xtr[a], ytr[a])
            .predict(Xtr[b])
        )
    d0 = tr[["run_index", "DV"]].copy()
    d0["p"] = oof
    r0 = d0.groupby("run_index").agg(DV=("DV", "first"), p=("p", "median"))
    iso = IsotonicRegression(out_of_bounds="clip").fit(r0.p, r0.DV)
    resid_std = float(np.std(iso.predict(r0.p) - r0.DV))
    run_cal = pd.Series(iso.predict(run_raw), index=run_raw.index)

    # [6] 主指標
    print(f"\n=== 主結果(run 層級,校正後,n={len(te_run)})===")
    print(f"R²   = {r2_score(te_run.DV, run_cal):.4f}")
    print(f"MAE  = {mean_absolute_error(te_run.DV, run_cal):.1f}")
    print(f"RMSE = {rmse(te_run.DV, run_cal):.1f}")
    ae = (run_cal - te_run.DV).abs()
    print(f"中位AE={ae.median():.1f}  P95={ae.quantile(0.95):.1f}  最大={ae.max():.1f}")

    print("\n各級分解:")
    tmp = te_run.copy()
    tmp["p"] = run_cal
    for lv, g in tmp.groupby("ylabel"):
        e = g.p - g.DV
        w = r2_score(g.DV, g.p) if lv != "LN" else float("nan")
        print(
            f"  {lv:3s} MAE={e.abs().mean():5.1f} 偏差={e.mean():+6.1f} "
            f"級內R²={w:6.3f}" + ("(LN 回歸值不可用,以分類代之)" if lv == "LN" else "")
        )

    # [5] 貝氏分級
    ll = np.column_stack(
        [norm.logpdf(run_cal, mu, np.sqrt(s**2 + resid_std**2)) for mu, s in LEVELS.values()]
    )
    pred_lvl = np.array(ORDER)[ll.argmax(1)]
    print(
        f"\n分類 accuracy={accuracy_score(te_run.ylabel, pred_lvl):.3f}  "
        f"macro-F1={f1_score(te_run.ylabel, pred_lvl, average='macro'):.3f}  "
        f"(resid_std={resid_std:.1f})"
    )
    cm = confusion_matrix(te_run.ylabel, pred_lvl, labels=ORDER)
    for i, row in enumerate(cm):
        print(f"  {ORDER[i]:3s} {row}  召回={row[i] / row.sum():.3f}")

    if not checks:
        return

    # ── 防偽對照三件套 ──
    print("\n=== 防偽對照 ===")
    # (a) 等級平均神諭:R² 虛高成分
    lvl_mean = tr.groupby("ylabel")["DV"].mean()
    oracle = te_run.ylabel.map(lvl_mean)
    print(
        f"(a) 完美分級+猜級平均: R²={r2_score(te_run.DV, oracle):.4f} "
        f"MAE={mean_absolute_error(te_run.DV, oracle):.1f}  ← R² 的免費地板"
    )
    # (b) 標籤打亂:洩漏檢查
    rng = np.random.default_rng(SEED)
    y_shuf = ytr.copy()
    rng.shuffle(y_shuf)
    m2 = ExtraTreesRegressor(n_estimators=300, n_jobs=-1, random_state=SEED).fit(Xtr, y_shuf)
    p2 = run_median(te, m2.predict(Xte)).reindex(te_run.index)
    print(f"(b) 標籤打亂重訓:     R²={r2_score(te_run.DV, p2):.3f}  ← 應≈0,證無洩漏")
    # (c) transition 層級(同粒度對照論文)
    print(
        f"(c) transition 未校正: R²={r2_score(te.DV, pred_t):.4f} "
        f"MAE={mean_absolute_error(te.DV, pred_t):.1f}  ← 與論文同粒度比較用"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", action="store_true", help="附三項防偽對照")
    main(ap.parse_args().checks)
