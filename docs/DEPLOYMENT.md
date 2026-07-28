# 部署到 GCP

把這個後端架到一台 Google Cloud 的虛擬機上。對應 PROMPT §6。

---

## 這份文件怎麼用

由上往下照做就可以。每一段都是**可以直接複製貼上執行的指令**，不是示意用的假指令。

大部分步驟都已經包成 `deploy/` 底下的腳本，會標「**建議用腳本**」；想理解細節或手動做，每段都附了等效的手動指令。各檔案的用途看「開始之前」第三點。

---

## 開始之前，先知道三件事

### 一、整套東西長什麼樣

**這台機器只跑後端**，4 個容器：

```
網際網路
   ↓  (只有 80/443 對外開，而且來源限縮成前端那台)
 caddy         ← 門口，負責 HTTPS，只把 /api/* 轉進去
   ↓
 api           ← 這個後端
   ↓
 postgres / redis / worker    ← 全部只在內部，外面連不到
```

**重點：只有 caddy 對外。** 資料庫、Redis、後端 API 都關在裡面，從網際網路直接連不到。這是刻意的。

**前端不在這台。** 它在另一個人管的另一台 GCP VM（固定 IP `35.194.234.205`），透過 `http(s)://<這台的IP或網域>/api/v1` 呼叫這個後端。所以：

- 這台的 caddy **不服務任何網頁**，只轉 `/api/*` 給後端，其餘一律 404。
- 兩台之間靠 `SERVICE_TOKEN` 認證，這個值**前後端必須一模一樣**。
- 這台的防火牆（80/443）**來源限縮成前端那台的 IP**，不是對全世界開。

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

### 三、部署檔案都在 `deploy/` 裡

正式環境要用的東西都寫好了，放在 `deploy/`：

| 檔案 | 做什麼 |
|---|---|
| `deploy/gcp-setup.sh` | 一鍵建好機器、資料碟、防火牆、每日快照（步驟 1–2） |
| `deploy/gen-secrets.sh` | 第一次部署時產生密碼、初始化 `.env`（步驟 4.2） |
| `deploy/docker-compose.prod.yml` | 正式環境的容器編排（含 caddy，api 不開埠） |
| `deploy/Caddyfile` | 門口設定，只轉 `/api/*`，有網域就自動申請 HTTPS |
| `deploy/deploy.sh` | 部署／更新＋跑完整驗證（步驟 4.3 + 5） |
| `deploy/backup.sh` | 每日 `pg_dump` + engine 備份，可掛 cron |

**跟開發用的 `docker-compose.yml` 差在哪：** 正式版多了 caddy，api **完全不對外開埠**（開發版會開 `127.0.0.1:8000` 方便本機測試）。

還有一件事要「人」去做，腳本代替不了：**把 `SERVICE_TOKEN` 交給前端負責人**，兩邊設成一樣、並填好 `BACKEND_BASE_URL`。細節在步驟 4 和最後一章。

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

> **懶人版：這一整章可以用 `deploy/gcp-setup.sh` 一鍵跑完。**
> 在你自己的電腦上（不是 VM 裡）：
> ```bash
> PROJECT_ID=<你的專案ID> ./deploy/gcp-setup.sh
> ```
> 它會建資料碟、建機器、設好防火牆（只放行前端 IP）、綁每日快照，且**冪等**——已存在的資源會跳過，不會動到資料。跑完照它印出的下一步做即可。
>
> 下面是它背後實際做的事，想手動做或想理解細節再往下看。

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

只開網頁要用的兩個埠，而且**來源限縮成前端那台 VM**（不對全世界開）：

```bash
gcloud compute firewall-rules create allow-web \
  --allow=tcp:80,tcp:443 --target-tags=web \
  --source-ranges=35.194.234.205/32 --description="web (frontend VM only)"
```

> 要自己從瀏覽器直接測後端的話，把你的 IP 也加進來（逗號分隔），
> 或暫時用 `0.0.0.0/0`（API 有 token 保護，但能鎖就鎖）。
> `gcp-setup.sh` 用 `ALLOW_SOURCES` 這個變數控制這件事。

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

**前端不用在這台抓。** 它是另一個人、在另一台 VM 上跑的專案（固定 IP `35.194.234.205`），跟這台只透過 API 溝通。你要跟前端負責人對接的只有兩個值：`SERVICE_TOKEN` 和後端對外網址（見 4.2、4.4）。

### 4.2 設定 `.env`

這個檔案裝著密碼和金鑰，**只放在機器上，絕對不要進 git**。

**用腳本做（建議）：**

```bash
# 沒網域
./deploy/gen-secrets.sh
# 有網域（caddy 會自動申請 HTTPS）
DOMAIN=example.com ./deploy/gen-secrets.sh
```

它會 `cp .env.example .env`、`chmod 600`、產生 `SERVICE_TOKEN` 和 `POSTGRES_PASSWORD`、把 `APP_ENV=prod`、`DATA_ROOT=/srv/data`、`ENGINE_DATA_DIR=/srv/data/engine` 都設好，最後把 `SERVICE_TOKEN` 印出來給你交給前端。**`.env` 已存在時它不會覆蓋**（避免把正在用的資料庫密碼換掉導致連不上）。

**想手動做的話**，等同以下步驟：

```bash
cp .env.example .env
chmod 600 .env

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

> **前端那台 VM 要設兩個值：**
> - `SERVICE_TOKEN`：跟這裡**一模一樣**。不一樣的話，前端每一個請求都會被擋掉（403），畫面整個空白。
> - `BACKEND_BASE_URL`：`http://<這台的對外IP>/api/v1`（有網域就用 `https://網域/api/v1`）。
>
> 這兩個值要交給前端負責人設定、然後重啟前端。

### 4.3 啟動

**用腳本做（建議）：** 一行搞定建置、遷移、啟動，並自動跑完步驟 5 的驗證。

```bash
./deploy/deploy.sh
```

它會先檢查 `.env` 的 `DATA_ROOT=/srv/data` 且 `/srv/data` 真的是掛載點（沒掛就擋下來），再 build → 起 postgres/redis → `alembic upgrade head` → 全部起來 → 驗證。

**想手動做的話**，正式環境要指定 prod compose 檔：

```bash
C='docker compose -f deploy/docker-compose.prod.yml --env-file .env'

# 先只開資料庫和 Redis，等它們 healthy
$C up -d postgres redis
$C ps

# 建立資料表（第一次、以及每次更新程式後都要跑）
$C run --rm api alembic upgrade head

# 全部起來（含 caddy）
$C up -d
```

### 4.4 門口（caddy）

設定檔就是 `deploy/Caddyfile`，已經寫好，`docker-compose.prod.yml` 會自動掛給 caddy 容器。**這台只轉 API、不服務網頁**，所以它長這樣：

```
{$DOMAIN::80} {
    handle /api/* { reverse_proxy api:8000 }   # 只轉 API 給後端
    handle /caddy-health { respond "ok" 200 }  # 不用 token 的健康檢查
    handle { respond 404 }                     # 其餘一律 404（這台沒網頁）
}
```

- **沒設 `DOMAIN`** → 監聽 `:80`，前端用 `http://<這台IP>/api/v1` 呼叫。
- **設了 `DOMAIN`** → caddy 自動去申請 Let's Encrypt 憑證，前端改用 `https://網域/api/v1`。憑證這件事完全不用手動處理。

`DOMAIN` 由 `.env` 帶進去（`gen-secrets.sh` 會寫入，或自己加一行 `DOMAIN=你的網域.com`）。

---

## 步驟 5：確認有沒有成功

**每次部署完都要全部跑一遍。有任何一項不過，就當作沒部署成功。**

> **`deploy/deploy.sh` 已經把 5.1–5.6 全自動跑過一遍了**（包含從 caddy 打 API 這條路徑）。
> 只想重跑驗證、不重新部署：`./deploy/deploy.sh --verify`。
> 下面是每一項在做什麼、以及想手動確認時的指令——記得正式環境要加
> `-f deploy/docker-compose.prod.yml`（例：`docker compose -f deploy/docker-compose.prod.yml exec api ...`）。

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

### 5.5 從門口打一次 API

先確認 caddy 這條路走得通（前端就是走這條）：

```bash
curl -s http://localhost/caddy-health          # 應該回 ok
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-User-ID: admin-1" -H "X-User-Role: admin" \
  -H "X-Correlation-ID: deploy-verify" \
  http://localhost/api/v1/health               # 應該回 200
```

### 5.5b 前端真的用一次（跨機器）

前端在另一台 VM，這一步要**跟前端負責人一起做**：

- 確認前端的 `SERVICE_TOKEN` 跟這台一樣、`BACKEND_BASE_URL` 指到這台
- 瀏覽器打開前端網站，登入一次，主畫面有資料、不是錯誤頁
- 回到這台查稽核紀錄，找得到剛才那次登入

### 5.6 檢查有沒有開錯埠

```bash
gcloud compute firewall-rules list --filter="direction=INGRESS AND sourceRanges:0.0.0.0/0"
```

**應該只有 80 和 443。** 出現 5432、6379、8000、5000 就是設錯了，立刻刪掉——那等於把資料庫直接暴露在網路上。

---

## 平常的維護

> 下面幾段若直接打 `docker compose`，正式環境請一律加上 prod 檔：
> `docker compose -f deploy/docker-compose.prod.yml --env-file .env <指令>`。
> `deploy.sh` / `backup.sh` 內部已經帶好，用腳本就不用管這件事。

### 更新程式

```bash
cd ~/backend
./deploy/deploy.sh
```

`deploy.sh` 會 `git pull` → build → 遷移 → 重啟 → **自動跑完步驟 5 驗證**。剛手動 checkout 過舊版、不想被 pull 蓋掉，加 `--no-pull`。

想手動做的話：

```bash
cd ~/backend
git pull
C='docker compose -f deploy/docker-compose.prod.yml --env-file .env'
$C build
$C run --rm api alembic upgrade head
$C up -d
# 然後把步驟 5 整個跑一遍
```

### 退回上一版

```bash
git log --oneline -10     # 找到上一個正常的版本
git checkout <版本編號>
./deploy/deploy.sh --no-pull   # 用當前 checkout 的版本重建，不要再 pull
```

> **資料表的變更不會自動退回。**
> 如果那一版有改資料表結構，退版前要先確認能不能安全降級。
>
> 另外，稽核表被設計成**不能修改也不能刪除**（資料庫層級擋住），所以降級指令
> 有可能直接被資料庫拒絕。這是故意的。遇到就走下面的「資料還原」，**不要去把
> 那個保護拆掉**。

### 每天備份資料庫 + engine

用 `deploy/backup.sh`，一次做 `pg_dump` 和 engine 打包，各留 7 份，寫檔用「先寫 tmp 再改名」避免留下半殘的壞備份，還會檢查當天 dump 不是空的。

先掛成每天 03:00 自動跑（在 `~/backend` 執行一次即可）：

```bash
( crontab -l 2>/dev/null; \
  echo "0 3 * * * $HOME/backend/deploy/backup.sh >> /srv/data/backups/backup.log 2>&1" ) \
  | crontab -
crontab -l          # 確認裝好了
```

想手動跑一次：`./deploy/backup.sh`。

> **這個備份跟每天的硬碟快照兩者都要有。**
> 快照救「機器爆炸」，`pg_dump` 救「手滑刪掉一張表」——隔天的快照會把錯誤狀態一起拍進去，救不回來。

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

## 交付狀態

PROMPT §6 要求 7 樣東西，目前都齊了：

| 要交付的東西 | 狀態 |
|---|---|
| `deploy/Caddyfile`（門口設定） | ✅ 只轉 `/api/*`，有網域就自動 HTTPS |
| `deploy/docker-compose.prod.yml`（正式環境設定） | ✅ 含 caddy，api 不對外開埠 |
| `deploy/gcp-setup.sh`（自動建置腳本） | ✅ 冪等，一鍵建機器＋防火牆＋快照 |
| `deploy/deploy.sh`（自動部署腳本） | ✅ 部署／更新＋自動驗證 |
| `deploy/backup.sh`（每天備份） | ✅ pg_dump + engine，可掛 cron |
| `deploy/gen-secrets.sh`（產生密碼） | ✅ 初始化 `.env`，已存在不覆蓋 |
| 這份部署文件 | ✅ 就是本文 |

正式環境的 api **不對外開埠**（只有 `docker-compose.prod.yml`；開發用的 `docker-compose.yml` 才會開 `127.0.0.1:8000` 方便本機測試）。

### 部署前最後確認這幾件事（腳本擋不住的）

1. **`.env` 的 `DATA_ROOT=/srv/data`，而且 `/srv/data` 真的掛了資料碟。**
   `deploy.sh` 會擋，但你要先確認資料碟掛好（步驟 3.1）。沒設好，資料會落在沒備份的開機碟上。
2. **`SERVICE_TOKEN` 前後端一致。** 前端在另一台 VM（`35.194.234.205`），
   要把這台產生的 `SERVICE_TOKEN` 交給前端負責人設定，兩邊不一致 = 前端每個請求都 403。
3. **前端那台要設 `BACKEND_BASE_URL`** 指到這台（`http://<這台IP>/api/v1`，有網域則 https）。
4. **防火牆 80/443 的來源** 最好限縮成前端那台 IP（`gcp-setup.sh` 預設已這樣做）。

> **關於前端：** 前端是另一個人、在另一台 GCP VM 上跑的獨立專案，
> **不會**跟這個後端放在同一台機器，也不需要在這台容器化。
> 兩台之間只透過 HTTP API + `SERVICE_TOKEN` 溝通。前端 VM 的 IP 是固定的。
