# 部署到 GCP

把這個後端架到一台 Google Cloud 的虛擬機上。對應 PROMPT §6。

---

## 這份文件怎麼用

由上往下照做就可以。每一段都是**可以直接複製貼上執行的指令**，不是示意用的假指令。

有幾個地方會標「**還沒做**」，那是真的還沒寫的東西，不是我漏寫。全部集中在最後一章。

---

## 開始之前，先知道三件事

### 一、整套東西長什麼樣

目標是一台機器上跑 5 個容器：

```
網際網路
   ↓  (只有 80/443 這兩個門對外開)
 caddy         ← 門口，負責 HTTPS
   ↓
 flask         ← 前端網頁
   ↓
 api           ← 這個後端
   ↓
 postgres / redis / worker    ← 全部只在內部，外面連不到
```

**重點：只有 caddy 對外。** 資料庫、Redis、後端 API 都關在裡面，從網際網路直接連不到。這是刻意的。

### 二、資料放在哪裡（這件事最容易出事）

機器上有兩顆硬碟：

| 硬碟 | 大小 | 用途 | 機器砍掉時 |
|---|---|---|---|
| 開機碟 | 30GB | 作業系統、Docker 本身 | **一起消失** |
| 資料碟 | 20GB | 資料庫、engine 檔案 | **留著，而且每天自動備份** |

所以資料**一定要放在資料碟上**。要做到這件事，`.env` 裡必須設定：

```bash
DATA_ROOT=/srv/data
```

`/srv/data` 就是資料碟掛上來的位置。

**如果忘了設會怎樣？** 資料會跑到程式目錄底下的 `.data/`，那是在開機碟上。結果就是：每天的自動備份照樣在跑、看起來一切正常，但備份到的是一顆空硬碟。等到機器真的掛掉要救的時候，才發現什麼都沒有。

（本機開發不用設，預設會用 `./.data`，不影響。）

### 三、現在還缺什麼

**照目前的程式碼直接部署，是上不了線的。** 缺三樣：

1. **沒有 caddy 設定檔** → 沒有 HTTPS，網址只能用 IP 開 http
2. **前端還不能容器化** → 前端專案沒有 Dockerfile，放不進來
3. **api 現在會對外開 8000 埠** → 正式環境不該這樣

細節看最後一章。

---

## 步驟 1：準備 GCP

### 1.1 你需要有的東西

- 裝好 `gcloud` 指令工具，並且登入過
- 一個 GCP 專案，已開啟計費（試用額度就夠）
- 帳號權限要有 Compute Admin
- 網域**可有可無**，沒有就先用 IP

### 1.2 設定變數

後面所有指令都會用到這幾個變數，先在你的終端機設好：

```bash
export PROJECT_ID=<你的專案ID>
export ZONE=asia-east1-b
export REGION=asia-east1
export VM=ai-servo-vm
export DATA_DISK=ai-servo-data
```

`asia-east1` 是台灣機房，離使用者最近。

### 1.3 打開需要的服務

```bash
gcloud services enable compute.googleapis.com iap.googleapis.com
```

---

## 步驟 2：建立機器

### 2.1 先建資料碟

**先建這顆，而且它跟機器是分開的。** 機器可以砍掉重建，這顆硬碟不動，資料就還在。

```bash
gcloud compute disks create $DATA_DISK \
  --size=20GB --type=pd-balanced --zone=$ZONE
```

### 2.2 建立機器，並把資料碟接上去

```bash
gcloud compute instances create $VM \
  --zone=$ZONE \
  --machine-type=e2-medium \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --disk=name=$DATA_DISK,device-name=data,mode=rw,boot=no \
  --tags=web \
  --metadata=enable-oslogin=TRUE
```

`e2-medium` 是 2 核心 4GB 記憶體，跑這套夠用。

### 2.3 設定防火牆

只開網頁要用的兩個埠：

```bash
gcloud compute firewall-rules create allow-web \
  --allow=tcp:80,tcp:443 --target-tags=web \
  --source-ranges=0.0.0.0/0 --description="public web"
```

SSH 不對外開放，改走 Google 的跳板（IAP）。這樣就算有人掃到你的 IP，也連不上 22 埠：

```bash
gcloud compute firewall-rules create allow-ssh-iap \
  --allow=tcp:22 --source-ranges=35.235.240.0/20 \
  --target-tags=web --description="SSH via IAP only"
```

**接著檢查有沒有多餘的規則**：

```bash
gcloud compute firewall-rules list --format="table(name,allowed[].map().firewall_rule().list(),sourceRanges.list())"
```

看到 5000 埠對外、或 22 埠對 `0.0.0.0/0` 開放，就刪掉：

```bash
gcloud compute firewall-rules delete <規則名稱>
```

資料庫（5432）、Redis（6379）、後端（8000）**不要建任何對外規則**。它們只在機器內部互通。

### 2.4 設定每天自動備份

「快照」就是幫硬碟定時拍一張照片，出事可以還原回去。

```bash
gcloud compute resource-policies create snapshot-schedule daily-backup \
  --region=$REGION --daily-schedule --start-time=18:00 --max-retention-days=14

gcloud compute disks add-resource-policies $DATA_DISK \
  --resource-policies=daily-backup --zone=$ZONE
```

**只綁資料碟。** 開機碟壞了重灌就好，資料碟壞了才是真的麻煩。保留 14 天。

### 2.5 連進機器

```bash
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap
```

以下的指令都是在機器裡面執行。

---

## 步驟 3：設定機器

### 3.1 把資料碟掛起來

新硬碟要先格式化才能用：

```bash
sudo mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard \
  /dev/disk/by-id/google-data
```

> ⚠️ **格式化 = 清空。**
> 這行指令**只有全新的硬碟才能執行**。如果你是在重建機器、要接回舊資料碟，
> **跳過這一行**，執行了資料就全沒了。
>
> 不確定的話先檢查：
> ```bash
> sudo blkid /dev/disk/by-id/google-data
> ```
> **有任何輸出 = 這顆盤已經有資料了 = 不要格式化。**

掛起來，並設定成開機自動掛：

```bash
sudo mkdir -p /srv/data
sudo mount -o discard,defaults /dev/disk/by-id/google-data /srv/data

echo "/dev/disk/by-id/google-data /srv/data ext4 discard,defaults,nofail 0 2" \
  | sudo tee -a /etc/fstab
```

### 3.2 裝 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker
```

### 3.3 建好資料目錄

```bash
sudo mkdir -p /srv/data/engine /srv/data/backups
sudo chown -R $USER:$USER /srv/data
```

---

## 步驟 4：裝上程式

### 4.1 抓程式碼

```bash
git clone https://github.com/ChenYuHsu413/FinalProject-Backend.git ~/backend
cd ~/backend
```

前端是另一個專案，也要抓下來（但它現在還不能容器化，見最後一章）：

```bash
git clone https://github.com/yuwen628/AI-Servo-Command-Center.git ~/frontend
```

### 4.2 設定 `.env`

這個檔案裝著密碼和金鑰，**只放在機器上，絕對不要進 git**。

```bash
cp .env.example .env
chmod 600 .env
```

產生兩組隨機密碼並填進去：

```bash
SERVICE_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 32)

sed -i "s|^SERVICE_TOKEN=.*|SERVICE_TOKEN=$SERVICE_TOKEN|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://aiservo:$POSTGRES_PASSWORD@postgres:5432/aiservo|" .env
sed -i "s|^APP_ENV=.*|APP_ENV=prod|" .env
sed -i "s|^DATA_ROOT=.*|DATA_ROOT=/srv/data|" .env
```

**最後那行 `DATA_ROOT` 千萬別漏。** 原因看前面「開始之前」第二點。

還有幾個要自己確認的設定：

| 設定 | 要填什麼 | 說明 |
|---|---|---|
| `ENGINE_DATA_DIR` | `/srv/data/engine` | 要在資料碟上 |
| `MOCK_MODE` | 通常是 `true` | 資料還是模擬的就維持 `true`。系統會誠實對外標示，不要為了好看改成 `false` |
| `MODEL_SOURCE` | `mock` 或 `http` | 填 `http` 才會去呼叫外部模型服務 |
| `MODEL_SERVICE_URL` | 模型服務網址 | 上面填 `http` 時才需要 |

> **前端的 `SERVICE_TOKEN` 要跟這裡一模一樣。**
> 不一樣的話，前端每一個請求都會被擋掉（403），畫面整個空白。

### 4.3 啟動

```bash
# 先只開資料庫和 Redis
docker compose up -d postgres redis

# 確認它們狀態變成 healthy 再繼續
docker compose ps

# 建立資料表（第一次、以及每次更新程式後都要跑）
docker compose run --rm api alembic upgrade head

# 全部起來
docker compose up -d
```

### 4.4 設定門口（caddy）

**設定檔還沒寫**（見最後一章）。補上之後，還沒有網域的話這樣寫：

```
:80 {
    reverse_proxy flask:5000
}
```

之後有網域了改成這樣，HTTPS 憑證 caddy 會自己去申請，不用手動處理：

```
你的網域.com {
    reverse_proxy flask:5000
}
```

---

## 步驟 5：確認有沒有成功

**每次部署完都要全部跑一遍。有任何一項不過，就當作沒部署成功。**

### 5.1 容器有沒有活著

```bash
docker compose ps                  # 每個都要是 Up / healthy
docker compose logs --tail=50 api  # 看有沒有錯誤訊息
```

### 5.2 後端有沒有回應

```bash
docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/health').read())"
```

### 5.3 稽核紀錄有沒有被動過手腳

這套系統的稽核紀錄是一條「鏈」，任何一筆被偷改都會被驗出來。這是整個治理機制的信任基礎。

```bash
TOKEN=$(grep ^SERVICE_TOKEN= .env | cut -d= -f2)
docker compose exec api curl -s \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-User-ID: admin-1" -H "X-User-Role: admin" \
  -H "X-Correlation-ID: deploy-verify" \
  http://localhost:8000/api/v1/audit/chain/verify
```

要看到 `"verified": true`。

> **如果是 false，立刻停下來，不要繼續上線，也不要重啟或清資料。**
> 那代表稽核紀錄被竄改或損毀，現場要保留下來查。

> 順帶一提：這個系統**連查詢都要帶身分**。少了 `X-User-ID` / `X-User-Role` /
> `X-Correlation-ID` 這三個 header，會拿到 400 而不是資料。這是刻意的設計。

### 5.4 有沒有誠實標示

打 `/api/v1/system/integrations`（header 同上），確認：

- `mock_mode` 跟實際情況相符
- `services` 裡的 redis、postgres 都是 connected

### 5.5 真的用一次

瀏覽器打開網站，登入一次，然後確認：

- 主畫面有資料，不是錯誤頁
- 回頭查稽核紀錄，找得到剛才那次登入

### 5.6 檢查有沒有開錯埠

```bash
gcloud compute firewall-rules list --filter="direction=INGRESS AND sourceRanges:0.0.0.0/0"
```

**應該只有 80 和 443。** 出現 5432、6379、8000、5000 就是設錯了，立刻刪掉——那等於把資料庫直接暴露在網路上。

---

## 平常的維護

### 更新程式

```bash
cd ~/backend
git pull
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d
```

**然後把步驟 5 整個跑一遍。**

### 退回上一版

```bash
git log --oneline -10     # 找到上一個正常的版本
git checkout <版本編號>
docker compose build && docker compose up -d
```

> **資料表的變更不會自動退回。**
> 如果那一版有改資料表結構，退版前要先確認能不能安全降級。
>
> 另外，稽核表被設計成**不能修改也不能刪除**（資料庫層級擋住），所以降級指令
> 有可能直接被資料庫拒絕。這是故意的。遇到就走下面的「資料還原」，**不要去把
> 那個保護拆掉**。

### 每天備份資料庫

**還沒做成自動排程。** 目前要手動跑：

```bash
docker compose exec -T postgres pg_dump -U aiservo aiservo \
  | gzip > /srv/data/backups/aiservo-$(date +%F).sql.gz

# 只留最近 7 份
ls -1t /srv/data/backups/aiservo-*.sql.gz | tail -n +8 | xargs -r rm
```

### 備份 engine 檔案

```bash
sudo tar czf /srv/data/backups/engine-$(date +%F).tar.gz -C /srv/data engine
```

---

## 出事的時候

### 狀況一：整台機器掛了

資料碟每天都有快照，照著還原：

```bash
# 1. 看有哪些快照
gcloud compute snapshots list

# 2. 用快照建一顆新的資料碟
gcloud compute disks create ${DATA_DISK}-restored \
  --source-snapshot=<快照名稱> --zone=$ZONE
```

3. 重新建機器（步驟 2.2），`--disk` 改成 `${DATA_DISK}-restored`
4. 設定機器（步驟 3）——**格式化那行一定要跳過**，資料就在盤上
5. 裝上程式（步驟 4）——`.env` 要重建，記得 `DATA_ROOT=/srv/data`，
   而且 `SERVICE_TOKEN` 要跟前端同步改
6. `docker compose up -d`，資料庫會直接接上還原的資料，不用另外匯入
7. **跑完步驟 5，特別是 5.3 的稽核鏈**

還原完稽核鏈仍然要是 `verified: true`。如果不是，代表還原的資料不完整。

> **快照不能取代每天的 `pg_dump`。**
> 快照救得了「機器爆炸」，救不了「手滑刪掉一張表」——因為隔天的快照會把錯誤的
> 狀態一起拍進去。兩種備份要都有。

### 狀況二：只有資料庫壞了

```bash
gunzip -c /srv/data/backups/aiservo-<日期>.sql.gz \
  | docker compose exec -T postgres psql -U aiservo aiservo
```

---

## 試用期滿要搬家

整套設計就是為了可以整包搬走：程式碼 + 一顆資料碟。

**1. 停掉服務**

```bash
docker compose down
```

⚠️ **不要加 `-v`**，那會把資料一起刪掉。

**2. 備份**

```bash
# 先單獨起資料庫，做一份匯出檔
docker compose up -d postgres
docker compose exec -T postgres pg_dump -U aiservo aiservo \
  | gzip > /srv/data/backups/aiservo-final.sql.gz
docker compose down

# .env 也要留（裡面有金鑰和密碼，git 上沒有）
cp .env /srv/data/backups/env.bak

# 整包打包
sudo tar czf ~/srv-data-$(date +%F).tar.gz -C /srv data
```

> **打包前一定要先 `docker compose down`。**
> 直接複製正在運作的資料庫檔案，會拿到一份壞掉的資料。這也是為什麼上面還是
> 做了一份 `pg_dump`——那份不管怎樣都能還原。

**3. 傳出來**

```bash
gcloud compute scp --tunnel-through-iap $VM:~/srv-data-*.tar.gz . --zone=$ZONE
```

**4. 在新環境裝回去**

新機器裝好 Docker → 把打包檔解開成 `/srv/data` → 抓程式碼 → 放回 `.env`
（確認 `DATA_ROOT=/srv/data`）→ `docker compose up -d` → `alembic upgrade head`

**5. 驗證**：完整跑一次步驟 5

**6. 確認新環境完全正常之後，才刪掉 GCP 資源**

```bash
gcloud compute instances delete $VM --zone=$ZONE
gcloud compute disks delete $DATA_DISK --zone=$ZONE
```

---

## 還沒做完的東西

PROMPT §6 要求 7 樣東西，目前狀態：

| 要交付的東西 | 狀態 |
|---|---|
| `deploy/Caddyfile`（門口設定） | **還沒做** — 沒有它就沒有 HTTPS |
| `deploy/docker-compose.prod.yml`（正式環境設定） | **還沒做** — 要加 caddy 和 flask |
| `deploy/gcp-setup.sh`（自動建置腳本） | **還沒做** — 步驟 2 目前要手動打 |
| `deploy/deploy.sh`（自動部署腳本） | **還沒做** — 更新流程目前要手動 |
| 每天自動備份資料庫 | **還沒做** — 目前要手動跑 |
| 產生密碼的腳本 | 部分 — 步驟 4.2 有指令，還沒包成腳本 |
| 這份部署文件 | ✅ 就是本文 |

另外兩件會擋住上線的事：

**一、前端還不能容器化**
[AI-Servo-Command-Center](https://github.com/yuwen628/AI-Servo-Command-Center) 目前只有 `run.py`，沒有 Dockerfile，所以放不進 compose 裡。這個要前端那邊補。

**二、後端還會對外開 8000 埠**
`docker-compose.yml` 現在把 api 開在 `127.0.0.1:8000`。正式環境應該只有 caddy 對外，api 完全不開埠。

> 先前這裡還有一項「資料沒放在資料碟上」，**已經修好了**——compose 改成
> 用 `DATA_ROOT` 指定位置。但**還是要記得在 `.env` 設 `DATA_ROOT=/srv/data`**，
> 沒設的話資料一樣會跑到沒有備份的地方去。
