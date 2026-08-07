# 引擎層資料之上游 ML 管線參考

本目錄收錄引擎層資料(`ENGINE_DATA_DIR`)的**上游 ML 管線參考實作**——
從原始資料集重現 R²=0.9973 的完整路徑,供外部讀者參考。此為參考實作,
不是平台執行期的一部分;引擎層只消費其產物的下游資料。

檔案:

- [`REPRODUCE_0997.md`](REPRODUCE_0997.md) — 逐步重現說明與預期輸出逐字核對
- [`extract_features.py`](extract_features.py) — 由原始 ZIP 串流抽取 per-transition 特徵
- [`train_eval_0997.py`](train_eval_0997.py) — 特徵表 → R²=0.9973 的正典訓練/評估管線

## 資料集出處與授權

- **資料集**:PHM Society 2023 Servomotor Dataset(FMCRD)
- **DOI**:[10.36001/phmconf.2023.v15i1.3580](https://doi.org/10.36001/phmconf.2023.v15i1.3580)
- **授權**:CC BY
- **下載**:<https://phm-datasets.s3.amazonaws.com/GE-UTK/FMCRD_Data.zip>(21.3 GB,**勿解壓**)

## 資料不隨 repo 發佈

原始資料檔與抽取後的特徵 CSV(`*.csv` / `*.parquet`)**不隨本 repo 發佈**,
且已於 `.gitignore` 排除。請依 [`REPRODUCE_0997.md`](REPRODUCE_0997.md) 自行下載
資料並產出特徵表。路徑以環境變數優先設定:

- `extract_features.py`:`FMCRD_ZIP`(輸入 ZIP)、`FMCRD_OUT`(輸出目錄)
- `train_eval_0997.py`:`FMCRD_TRAIN_CSV`、`FMCRD_TEST_CSV`(特徵表位置)

未設環境變數時沿用腳本內原始預設值。

## 成績脈絡

R²=0.9973 為 transition 對齊之重建管線成果;AIFinalProject 主線(per-run 21 維)
為 R²=0.944,兩者關係見 [`REPRODUCE_0997.md`](REPRODUCE_0997.md) 的評估協定聲明
——避免跨 repo 兩個數字造成混淆。
