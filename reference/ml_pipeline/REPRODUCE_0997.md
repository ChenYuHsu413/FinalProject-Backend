# 0.9973 重現套件 — 步驟說明

日期:2026-07-29|對應文件:《FMCRD_誤差與演算法總表》《FMCRD_特徵字典》
套件內容:`extract_features.py`(特徵提取)、`train_eval_0997.py`(訓練+評估,單檔正典)

---

## 步驟 0:取得資料(一次)

官方來源(PHM Society 2023,CC BY):
`https://phm-datasets.s3.amazonaws.com/GE-UTK/FMCRD_Data.zip`(21.3 GB,**勿解壓**)

已知缺陷(對話中之原創發現):`train_noisy_LO` 於官方源頭即截斷於 run 265,
僅 65/200 runs——這是 LO 級較弱(級內 R² 0.831)的根因,屬資料條件非方法問題。

## 步驟 1:特徵提取(一次,約 30–60 分鐘視磁碟)

```
# 編輯 extract_features.py 開頭的 ZIP_PATH / OUT_DIR 後:
python extract_features.py
```

- 串流讀 zip 內 8 個 CSV(chunk 2M 列,不解壓)
- 以 `transitions` 欄切割,每 run 5 個 transition;轉速幅度 >2% 峰值切運動窗
- 產出 `train_features.csv`(3,322 列)/ `test_features.csv`(4,000 列)
- 欄位:**6 中繼 + 51 特徵 = 57 欄**(定義見特徵字典;`has_motion` 恆 1)

## 步驟 2:訓練與評估(約 3–5 分鐘)

```
# 特徵表放 feat/ 子目錄(或改腳本頂端路徑)
python train_eval_0997.py            # 主結果
python train_eval_0997.py --checks   # 附防偽對照(建議至少跑一次)
```

管線六段(腳本內註解逐段對應):
1. 50 維入模(剔 6 中繼 + has_motion)
2. 冠軍組合 =(ExtraTrees×500 + 單調 HistGB×600)/2,seed=0
   (12 個扭矩/電流特徵施加單調遞增約束——物理:阻力↑ 該量↑)
3. run 聚合:同 run 5 預測取**中位數**
4. OOF 等張校正:GroupKFold(5, groups=run) 的 out-of-fold 預測
   於 **run 層級**擬合 Isotonic ——**鐵律:先聚合、再校正**
   (校正曲線套 transition 層級會變差:RMSE 100.3→113.8)
5. 貝氏分級:校正 DV 對 LN(0,2)/LO(400,200)/MED(1600,300)/HI(3200,500)
   最大似然,變異數加計 OOF 殘差 std(≈40)
6. 指標輸出

## 預期輸出(逐字核對)

```
R²   = 0.9973
MAE  = 44.1
RMSE = 66.7
中位AE=29.0  P95=140.1  最大=475.8
各級:HI 45.1/0.980  LN 39.2(分類代之)  LO 58.5/0.831  MED 33.7/0.978
分類 accuracy=0.906  macro-F1=0.905
混淆矩陣:LN[183,17,0,0] LO[43,153,4,0] MED[0,0,196,4] HI[0,0,7,193]
```

`--checks` 三項防偽:
(a) 等級平均神諭 R²=0.944(R² 的免費地板——虛高成分的量化)
(b) 標籤打亂重訓 R²≈−0.01(無洩漏)
(c) transition 未校正 R²=0.9940 / MAE 73.0(與論文同粒度之公平比較)

## 環境與變異

- Python 3.10+,pandas/numpy/scipy/scikit-learn(版本差異致 ±1% 級波動屬正常;
  上列數字於 sklearn 1.x / seed=0 產出)
- 單核機器將 n_jobs=-1 保留即可(僅變慢)

## 評估協定聲明(引用時請帶上)

策略選擇依訓練集 GroupKFold CV;測試集單次評估。開發期含多次測試集評估
(適應性選擇風險已於報告揭露);本套件即為單發重現路徑。R² 於此資料天生
虛高(神諭=0.944),對外引用建議並列 MAE 44.1(0.88% 全量程)與級內 R²。
