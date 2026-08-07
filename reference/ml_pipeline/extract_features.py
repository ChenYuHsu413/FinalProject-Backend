"""
FMCRD Servomotor Dataset - per-transition feature extraction
串流讀取 zip 內 8 個大型 CSV(不解壓),以 (run, transition) 為單位切割暫態,
只對運動片段抽取特徵,輸出 train_features.csv / test_features.csv。

用法:  python extract_features.py
需求:  pandas, numpy  (pip install pandas numpy)
"""

import os
import sys
import time as _time
import zipfile

import numpy as np
import pandas as pd

# ========= 設定 =========
ZIP_PATH = os.environ.get("FMCRD_ZIP", r"C:\Users\alung\Downloads\FMCRD_Data.zip")
OUT_DIR = os.environ.get("FMCRD_OUT", r"C:\Users\alung\Downloads")
CHUNK_ROWS = 2_000_000  # 每次讀取的列數(約 250MB 記憶體)
DT = 2e-5  # 取樣間隔 20 µs (50 kHz)
# ========================

SIG_COLS = [
    "DV",
    "rod_demand_pos",
    "rod_actual_pos",
    "torque",
    "rotor_speed",
    "i_3p_a",
    "i_3p_b",
    "i_3p_c",
    "direct",
    "quadrature",
    "del_pos",
]
DTYPES = {c: np.float64 for c in SIG_COLS}
DTYPES.update({"run_index": np.int32, "transitions": np.int64, "ylabel": "category"})
USECOLS = SIG_COLS + ["run_index", "transitions", "ylabel"]


def rms(x):
    return float(np.sqrt(np.mean(x * x))) if len(x) else np.nan


def stats_block(prefix, x, out):
    """一組訊號的基本統計(在指定片段上)"""
    if len(x) == 0:
        for s in ("mean", "median", "rms", "std", "maxabs", "p2p"):
            out[f"{prefix}_{s}"] = np.nan
        return
    out[f"{prefix}_mean"] = float(np.mean(x))
    out[f"{prefix}_median"] = float(np.median(x))
    out[f"{prefix}_rms"] = rms(x)
    out[f"{prefix}_std"] = float(np.std(x))
    out[f"{prefix}_maxabs"] = float(np.max(np.abs(x)))
    out[f"{prefix}_p2p"] = float(np.max(x) - np.min(x))


def transition_features(g):
    """對單一 transition 的資料切運動片段並抽特徵。g: DataFrame(已按時間排序)"""
    out = {}
    n = len(g)
    spd = g["rotor_speed"].to_numpy()
    trq = g["torque"].to_numpy()
    iq = g["quadrature"].to_numpy()
    idc = g["direct"].to_numpy()
    apos = g["rod_actual_pos"].to_numpy()
    dpos = g["rod_demand_pos"].to_numpy()

    out["n_samples"] = n
    out["seg_seconds"] = n * DT
    out["del_pos"] = float(g["del_pos"].iloc[0])
    out["abs_disp"] = abs(out["del_pos"])
    out["direction"] = float(np.sign(out["del_pos"]))

    # ---- 運動片段偵測:以轉速幅度為準 ----
    aspd = np.abs(spd)
    smax = aspd.max() if n else 0.0
    if smax <= 1e-9:
        out["has_motion"] = 0
        active = np.zeros(n, dtype=bool)
    else:
        out["has_motion"] = 1
        thr = 0.02 * smax
        idx = np.flatnonzero(aspd > thr)
        active = np.zeros(n, dtype=bool)
        active[idx[0] : idx[-1] + 1] = True  # 首次超過門檻到最後一次之間視為運動窗

    na = int(active.sum())
    out["motion_seconds"] = na * DT
    out["motion_frac"] = na / n if n else np.nan

    # ---- 主力特徵:運動窗內的扭矩 / q 軸電流 / 轉速 ----
    stats_block("torque_act", trq[active], out)
    stats_block("iq_act", iq[active], out)
    stats_block("speed_act", np.abs(spd[active]), out)
    stats_block("id_act", idc[active], out)

    # 等速段(轉速 > 70% 峰值)的扭矩/電流平台值 ≈ 阻力負載的直接估計
    if out["has_motion"]:
        plateau = aspd > 0.7 * smax
        out["torque_plateau_med"] = (
            float(np.median(np.abs(trq[plateau]))) if plateau.any() else np.nan
        )
        out["iq_plateau_med"] = float(np.median(np.abs(iq[plateau]))) if plateau.any() else np.nan
        out["speed_peak"] = float(smax)
    else:
        out["torque_plateau_med"] = np.nan
        out["iq_plateau_med"] = np.nan
        out["speed_peak"] = 0.0

    # ---- 論文 baseline 特徵:運動窗三等分的中位數(轉速、q 軸電流)----
    if na >= 3:
        segs = np.array_split(np.flatnonzero(active), 3)
        for k, sidx in enumerate(segs, 1):
            out[f"speed_med_t{k}"] = float(np.median(spd[sidx]))
            out[f"iq_med_t{k}"] = float(np.median(iq[sidx]))
    else:
        for k in (1, 2, 3):
            out[f"speed_med_t{k}"] = np.nan
            out[f"iq_med_t{k}"] = np.nan

    # ---- 控制性能:追蹤誤差 / 過衝 / 整定 / 穩態誤差 ----
    target = dpos[-1]
    err = apos - target
    track = apos - dpos
    out["track_err_rms"] = rms(track)
    out["track_err_maxabs"] = float(np.max(np.abs(track))) if n else np.nan
    tail = err[int(n * 0.9) :]
    out["steady_err_mean"] = float(np.mean(np.abs(tail))) if len(tail) else np.nan
    if out["abs_disp"] > 1e-9 and out["has_motion"]:
        d = out["direction"]
        over = np.max(d * err) if n else np.nan  # 沿運動方向越過目標的量
        out["overshoot_ratio"] = float(max(over, 0.0) / out["abs_disp"])
        tol = 0.02 * out["abs_disp"]
        bad = np.flatnonzero(np.abs(err) > tol)
        out["settle_seconds"] = float((bad[-1] + 1) * DT) if len(bad) else 0.0
    else:
        out["overshoot_ratio"] = np.nan
        out["settle_seconds"] = np.nan

    # ---- 雜訊/漣波:一階差分 RMS(高通),與電氣特徵 ----
    if n >= 2:
        out["iq_diff_rms"] = rms(np.diff(iq))
        out["torque_diff_rms"] = rms(np.diff(trq))
        out["speed_diff_rms"] = rms(np.diff(spd))
    else:
        out["iq_diff_rms"] = out["torque_diff_rms"] = out["speed_diff_rms"] = np.nan
    ia, ib, ic = (g["i_3p_a"].to_numpy(), g["i_3p_b"].to_numpy(), g["i_3p_c"].to_numpy())
    ra, rb, rc = rms(ia[active]), rms(ib[active]), rms(ic[active])
    out["i3p_rms_mean"] = float(np.nanmean([ra, rb, rc]))
    m = out["i3p_rms_mean"]
    out["i3p_imbalance"] = (
        float((max(ra, rb, rc) - min(ra, rb, rc)) / m) if m and m > 1e-12 else np.nan
    )
    return out


def process_run(df_run, meta, writer):
    """df_run: 單一 run 的完整資料;依 transitions 欄位分組抽特徵並寫出。"""
    run_id = int(df_run["run_index"].iloc[0])
    dv = float(df_run["DV"].iloc[0])
    ylab = str(df_run["ylabel"].iloc[0])
    rows = []
    for tr_id, g in df_run.groupby("transitions", sort=True, observed=True):
        feat = transition_features(g)
        feat.update(
            {
                "split": meta["split"],
                "file_level": meta["level"],
                "run_index": run_id,
                "transition_id": int(tr_id),
                "DV": dv,
                "ylabel": ylab,
            }
        )
        rows.append(feat)
    writer.write_rows(rows)


class CsvWriter:
    def __init__(self, path):
        self.path = path
        self.header_written = os.path.exists(path) and os.path.getsize(path) > 0
        self.n = 0

    def write_rows(self, rows):
        if not rows:
            return
        df = pd.DataFrame(rows)
        meta_cols = ["split", "file_level", "run_index", "transition_id", "DV", "ylabel"]
        df = df[meta_cols + [c for c in df.columns if c not in meta_cols]]
        df.to_csv(self.path, mode="a", header=not self.header_written, index=False)
        self.header_written = True
        self.n += len(df)


def stream_file(zf, name, writer):
    level = (
        "LN" if "load0" in name else ("HI" if "HI" in name else ("LO" if "LO" in name else "MED"))
    )
    split = "train" if os.path.basename(name).startswith("train") else "test"
    meta = {"split": split, "level": level}
    t0 = _time.time()
    buf = None
    done_runs = 0
    with zf.open(name) as raw:
        reader = pd.read_csv(raw, usecols=USECOLS, dtype=DTYPES, chunksize=CHUNK_ROWS)
        for chunk in reader:
            buf = chunk if buf is None else pd.concat([buf, chunk], ignore_index=True)
            last_run = buf["run_index"].iloc[-1]
            complete = buf[buf["run_index"] != last_run]
            if len(complete):
                for _, df_run in complete.groupby("run_index", sort=True):
                    process_run(df_run, meta, writer)
                    done_runs += 1
                buf = buf[buf["run_index"] == last_run].reset_index(drop=True)
            print(f"  {name}: {done_runs} runs 完成, {_time.time() - t0:.0f}s", end="\r")
    if buf is not None and len(buf):
        process_run(buf, meta, writer)
        done_runs += 1
    print(f"  {name}: {done_runs} runs 完成, {_time.time() - t0:.0f}s")


def main():
    zip_path = sys.argv[1] if len(sys.argv) > 1 else ZIP_PATH
    writers = {
        "train": CsvWriter(os.path.join(OUT_DIR, "train_features.csv")),
        "test": CsvWriter(os.path.join(OUT_DIR, "test_features.csv")),
    }
    for w in writers.values():  # 重跑時清除舊檔
        if os.path.exists(w.path):
            os.remove(w.path)
            w.header_written = False
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        print(f"共 {len(names)} 個檔案")
        for name in names:
            split = "train" if os.path.basename(name).startswith("train") else "test"
            print(f"處理 {name} ...")
            stream_file(zf, name, writers[split])
    for k, w in writers.items():
        print(f"{k}: {w.n} 筆特徵 -> {w.path}")
    # 打包方便上傳
    out_zip = os.path.join(OUT_DIR, "FMCRD_features.zip")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zo:
        for w in writers.values():
            if os.path.exists(w.path):
                zo.write(w.path, os.path.basename(w.path))
    print(f"完成: {out_zip}")


if __name__ == "__main__":
    main()
