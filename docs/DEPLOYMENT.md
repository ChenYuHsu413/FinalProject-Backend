# DEPLOYMENT — GCP Compute Engine 單 VM

從零開機到服務上線的 runbook。對應 PROMPT §6。

> **這份文件的誠實聲明**
>
> 文件裡的 `gcloud` / `docker` 指令都是可直接執行的真實指令，不是虛擬碼。
> 但 PROMPT §6 要求的 `deploy/` 目錄**目前不存在**，所以本文不引用任何
> `deploy.sh` / `gcp-setup.sh`——那些腳本要做的事，這裡用原始指令寫開。
> 尚未完成的交付物集中列在 [§9 未完成事項](#9-未完成事項)，請在正式上線前補齊。

---

## 0. 先讀：目前與目標拓撲的落差

PROMPT §6 的目標是一台 VM 跑 **5 個** compose 服務：

```
caddy (80/443 對外)  →  flask (前端)
                         └→ api (FastAPI, 僅內網)
                            worker / postgres / redis (僅內網)
```

**倉庫現況 `docker-compose.yml` 只有 4 個服務**：`api` / `worker` / `postgres` / `redis`，
沒有 `caddy`、沒有 `flask`，而且 `api` 直接 publish 到 `127.0.0.1:8000`。

也就是說：**照現在的 compose 部署上去，等於沒有反向代理、沒有 HTTPS、前端不在機器上。**
上線前必須先補 §9 的項目。本文其餘章節假設你已補上，並在該用到的地方標註。

### ⚠ 資料根本不在資料碟上

PROMPT §6 要求「額外 Persistent Disk 20GB 掛 `/srv/data`，Postgres volume 與
`ENGINE_DATA_DIR` 都放這裡」。**現況沒有做到。** 現在的 compose 用的是 docker
**具名 volume**：

```yaml
volumes:
  - engine_data:/srv/data/engine       # 具名 volume，不是 bind mount
  - pg_data:/var/lib/postgresql/data   # 同上
```

具名 volume 實際存放在 `/var/lib/docker/volumes/`，位於**開機碟**。後果：

| 你以為 | 實際 |
|---|---|
| 資料碟快照 = 資料備份 | 快照的是一顆**空盤**，救不回任何東西 |
| 打包 `/srv/data` 就能搬家 | 打包到的是空目錄 |
| VM 重建、資料碟不動 = 資料還在 | **資料隨開機碟一起消失** |

**在 §9 第 8 項修好之前，唯一有效的備份是 §7.1 的 `pg_dump` 加 §7.4 的 volume 匯出。**
不要依賴磁碟快照。

---

## 1. 前置需求

| 項目 | 說明 |
|---|---|
| `gcloud` CLI | 已 `gcloud auth login`、`gcloud config set project <PROJECT_ID>` |
| GCP 專案 | 已啟用計費；本文用試用額度即可 |
| IAM 權限 | Compute Admin + Service Account User |
| 網域 | **選用**。沒有網域就先用 IP + HTTP（見 §4.2） |

本文所有指令假設先設好這些變數（依你的環境調整）：

```bash
export PROJECT_ID=<your-project-id>
export ZONE=asia-east1-b          # 台灣，離使用者最近
export REGION=asia-east1
export VM=ai-servo-vm
export DATA_DISK=ai-servo-data
```

---

## 2. 一次性基礎建設

### 2.1 啟用 API

```bash
gcloud services enable compute.googleapis.com iap.googleapis.com
```

### 2.2 建立資料磁碟（與 VM 分離，是災難復原的關鍵）

Postgres 資料與 `ENGINE_DATA_DIR` 都放這顆盤。**VM 可以砍掉重建，這顆盤不動。**

```bash
gcloud compute disks create $DATA_DISK \
  --size=20GB --type=pd-balanced --zone=$ZONE
```

### 2.3 建立 VM

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

### 2.4 防火牆：只開 80/443，SSH 走 IAP

```bash
# 對外只開 HTTP/HTTPS，且只對帶 web tag 的機器
gcloud compute firewall-rules create allow-web \
  --allow=tcp:80,tcp:443 --target-tags=web \
  --source-ranges=0.0.0.0/0 --description="public web"

# SSH 只允許 IAP 的來源網段，不開放公網 22
gcloud compute firewall-rules create allow-ssh-iap \
  --allow=tcp:22 --source-ranges=35.235.240.0/20 \
  --target-tags=web --description="SSH via IAP only"
```

**檢查有沒有殘留的舊規則**（PROMPT §6 特別要求移除 5000 對外）：

```bash
gcloud compute firewall-rules list --format="table(name,allowed[].map().firewall_rule().list(),sourceRanges.list())"
# 若看到 5000 或 0.0.0.0/0 的 22，刪掉：
# gcloud compute firewall-rules delete <rule-name>
```

Postgres / Redis / FastAPI **不建任何對外規則**——它們只在 docker 內網。

### 2.5 每日快照排程

```bash
gcloud compute resource-policies create snapshot-schedule daily-backup \
  --region=$REGION --daily-schedule --start-time=18:00 --max-retention-days=14

gcloud compute disks add-resource-policies $DATA_DISK \
  --resource-policies=daily-backup --zone=$ZONE
```

> 只綁**資料磁碟**。開機碟可以重建，資料碟不行。

### 2.6 SSH 進機器

```bash
gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap
```

---

## 3. 主機初始化（在 VM 上執行）

### 3.1 掛載資料磁碟

```bash
# 第一次才需要格式化 —— 重建 VM 時務必跳過這步，否則資料全毀
sudo mkfs.ext4 -m 0 -E lazy_itable_init=0,lazy_journal_init=0,discard \
  /dev/disk/by-id/google-data

sudo mkdir -p /srv/data
sudo mount -o discard,defaults /dev/disk/by-id/google-data /srv/data

# 開機自動掛載
echo "/dev/disk/by-id/google-data /srv/data ext4 discard,defaults,nofail 0 2" \
  | sudo tee -a /etc/fstab
```

> **確認是不是新盤**：`sudo blkid /dev/disk/by-id/google-data`
> 有輸出代表盤上已有檔案系統 → **不要格式化**，直接 mount。

### 3.2 安裝 Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker    # 或重新登入
```

### 3.3 建立資料目錄

```bash
sudo mkdir -p /srv/data/engine /srv/data/backups
sudo chown -R $USER:$USER /srv/data
```

---

## 4. 應用部署

### 4.1 取得程式碼

```bash
git clone https://github.com/ChenYuHsu413/FinalProject-Backend.git ~/backend
cd ~/backend
```

前端是**另一個 repo**，需一併 clone（見 §9 第 9 項——它目前沒有 Dockerfile）：

```bash
git clone https://github.com/yuwen628/AI-Servo-Command-Center.git ~/frontend
```

### 4.2 產生 `.env`

`.env` **只存在 VM 上，永遠不進 repo**。

```bash
cp .env.example .env
chmod 600 .env

# 產生密鑰
SERVICE_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 32)

sed -i "s|^SERVICE_TOKEN=.*|SERVICE_TOKEN=$SERVICE_TOKEN|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://aiservo:$POSTGRES_PASSWORD@postgres:5432/aiservo|" .env
sed -i "s|^APP_ENV=.*|APP_ENV=prod|" .env
```

**前端的 `SERVICE_TOKEN` 必須與這裡一致**，否則所有 API 請求都會 403。

正式環境要確認的其他變數：

| 變數 | 正式值 | 說明 |
|---|---|---|
| `APP_ENV` | `prod` | |
| `ENGINE_DATA_DIR` | `/srv/data/engine` | 必須在資料碟上 |
| `MOCK_MODE` | 依實際情況 | **誠實原則**：仍是模擬資料就維持 `true`，`/api/v1/system/integrations` 會照實標示 |
| `MODEL_SOURCE` | `mock` 或 `http` | batch 8。設 `http` 才會呼叫外部模型服務 |
| `MODEL_SERVICE_URL` | 模型服務網址 | `MODEL_SOURCE=http` 時必填 |
| `MODEL_SERVICE_TIMEOUT_S` | `3` | 外部服務逾時；逾時會靜默降級，不會 5xx |
| `MODEL_CACHE_TTL_S` | `5` | |

### 4.3 啟動

```bash
docker compose build
docker compose up -d postgres redis
# 等 healthcheck 轉為 healthy
docker compose ps

# 套用 migration（首次與每次更新都要）
docker compose run --rm api alembic upgrade head

docker compose up -d
```

### 4.4 反向代理

`Caddyfile` **尚不存在**（§9 第 1 項）。補上後，無網域先用：

```
:80 {
    reverse_proxy flask:5000
}
```

有網域後改成網域名，Caddy 會自動申請 TLS：

```
your-domain.example {
    reverse_proxy flask:5000
}
```

---

## 5. 驗證清單

**每次部署後都要跑完**。任何一項不過就不算上線。

### 5.1 容器健康

```bash
docker compose ps          # 全部 Up 且 healthy
docker compose logs --tail=50 api
```

### 5.2 API healthcheck

```bash
docker compose exec api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/health').read())"
```

### 5.3 稽核鏈驗證（治理的信任根，必驗）

```bash
TOKEN=$(grep ^SERVICE_TOKEN= .env | cut -d= -f2)
docker compose exec api curl -s \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-User-ID: admin-1" -H "X-User-Role: admin" \
  -H "X-Correlation-ID: deploy-verify" \
  http://localhost:8000/api/v1/audit/chain/verify
```

期望 `"verified": true`。**若為 false，立刻停止上線並保留現場**——那代表稽核鏈斷裂。

> 注意：所有 `/api/v1/*` 的 GET 也必須帶 `X-User-ID` / `X-User-Role` / `X-Correlation-ID`
> （DECISIONS D3.6），缺了會拿到 **400** 而不是 200。

### 5.4 誠實旗標

```bash
# 同上的 header，打 /api/v1/system/integrations
```

確認 `mock_mode` 與實際情況相符，且 `services` 裡 redis / postgres 都是 connected。

### 5.5 Flask 登入走通

從瀏覽器打開 `https://<網域或 IP>`，完成一次登入，然後確認：

- Dashboard 顯示資料（不是錯誤頁）
- 回到 `/audit/events` 查得到這次登入的稽核紀錄

### 5.6 對外埠稽核

```bash
gcloud compute firewall-rules list --filter="direction=INGRESS AND sourceRanges:0.0.0.0/0"
```

**只應該看到 80/443。** 出現 5432 / 6379 / 8000 / 5000 就是設定錯了，立即刪除。

---

## 6. 更新與回滾

### 6.1 更新

```bash
cd ~/backend
git pull
docker compose build
docker compose run --rm api alembic upgrade head
docker compose up -d
# 接著跑完整個 §5 驗證清單
```

### 6.2 回滾

```bash
git log --oneline -10          # 找上一個好的 commit
git checkout <commit>
docker compose build && docker compose up -d
```

> **migration 不會自動回滾。** 若該版本含 migration，需先確認
> `alembic downgrade -1` 是否安全。`audit_events` 有 append-only 觸發器
> （REVOKE + BEFORE UPDATE/DELETE/TRUNCATE），**降級可能被資料庫擋下**——
> 這是刻意的設計。遇到就走 §7 的資料還原，不要硬拆觸發器。

---

## 7. 備份與災難復原

### 7.1 每日 `pg_dump`

尚未排程（§9 第 5 項）。手動指令：

```bash
docker compose exec -T postgres pg_dump -U aiservo aiservo \
  | gzip > /srv/data/backups/aiservo-$(date +%F).sql.gz

# 保留 7 份
ls -1t /srv/data/backups/aiservo-*.sql.gz | tail -n +8 | xargs -r rm
```

### 7.2 從快照重建（VM 整台掛掉）

> **先讀 §0 的警告。** 在 volume 改成 bind mount 之前，資料碟快照裡**沒有資料**，
> 這節只能重建機器，資料要靠 §7.1 / §7.4 的備份還原。

```bash
# 1. 從快照建新盤
gcloud compute snapshots list
gcloud compute disks create ${DATA_DISK}-restored \
  --source-snapshot=<snapshot-name> --zone=$ZONE

# 2. 重新建 VM（§2.3），--disk 換成 ${DATA_DISK}-restored

# 3. 主機初始化（§3）—— 【務必跳過 mkfs】

# 4. 重新部署（§4）—— .env 需重建，SERVICE_TOKEN 要與前端同步更新

# 5. 還原資料：§7.3 的 pg_dump 還原 + §7.4 的 engine volume 還原

# 6. 跑完 §5 驗證清單，特別是 5.3 稽核鏈
```

**復原後稽核鏈仍須 `verified: true`。** 若否，代表還原的資料不完整。

### 7.3 還原資料庫

```bash
gunzip -c /srv/data/backups/aiservo-<date>.sql.gz \
  | docker compose exec -T postgres psql -U aiservo aiservo
```

### 7.4 備份／還原 engine volume

具名 volume 無法用檔案系統備份取得，得用容器導出：

```bash
# 備份
docker run --rm -v backend_engine_data:/data -v /srv/data/backups:/backup \
  alpine tar czf /backup/engine-$(date +%F).tar.gz -C /data .

# 還原
docker run --rm -v backend_engine_data:/data -v /srv/data/backups:/backup \
  alpine sh -c "rm -rf /data/* && tar xzf /backup/engine-<date>.tar.gz -C /data"
```

> volume 名稱前綴是 compose 專案名（預設為目錄名，此處為 `backend`）。
> 用 `docker volume ls` 確認實際名稱。

---

## 8. 90 天試用期滿的遷移

整套設計就是為了可以整包搬走——compose + 一顆資料磁碟。

1. **停服務**：`docker compose down`（**不要加 `-v`**，那會連 volume 一起刪掉，資料全毀）
2. **備份三樣東西**（缺一不可）：
   ```bash
   # a. 資料庫
   docker compose exec -T postgres pg_dump -U aiservo aiservo \
     | gzip > /srv/data/backups/aiservo-final.sql.gz
   # b. engine volume（§7.4）
   docker run --rm -v backend_engine_data:/data -v /srv/data/backups:/backup \
     alpine tar czf /backup/engine-final.tar.gz -C /data .
   # c. .env（含 SERVICE_TOKEN / DB 密碼，不在 repo 裡）
   cp .env /srv/data/backups/env.bak
   # 打包
   sudo tar czf ~/migration-$(date +%F).tar.gz -C /srv/data backups
   ```
3. **取出**：`gcloud compute scp --tunnel-through-iap $VM:~/migration-*.tar.gz . --zone=$ZONE`
4. **落地新環境**：新主機裝 Docker → clone repo → 還原 `.env`
   → `docker compose up -d postgres redis` → `alembic upgrade head`
   → 還原資料庫（§7.3）與 engine volume（§7.4）→ `docker compose up -d`
5. **驗證**：完整跑一次 §5
6. **拆除**：確認新環境無誤後才刪 GCP 資源
   ```bash
   gcloud compute instances delete $VM --zone=$ZONE
   gcloud compute disks delete $DATA_DISK --zone=$ZONE
   ```

> **最容易漏的一點**：資料庫與 engine 檔案都在 docker 具名 volume 裡（§0），
> 打包 `/srv/data` 抓不到它們。遷移前務必確認 `aiservo-final.sql.gz` 與
> `engine-final.tar.gz` 都存在、大小合理、能解開——在刪掉舊 VM 之前。

---

## 9. 未完成事項

PROMPT §6 要求的 7 項交付物，目前狀態：

| # | 交付物 | 狀態 |
|---|---|---|
| 1 | `deploy/Caddyfile` | **缺** — 無反向代理即無 HTTPS |
| 2 | `deploy/docker-compose.prod.yml` | **缺** — 需加 caddy + flask、關 debug、prod healthcheck |
| 3 | `deploy/gcp-setup.sh`（冪等） | **缺** — §2 的指令目前需手動執行 |
| 4 | `deploy/deploy.sh`（含 rollback） | **缺** — §6 的步驟目前需手動執行 |
| 5 | 每日 `pg_dump` 排程（worker） | **缺** — §7.1 目前是手動 |
| 6 | Secrets 產生 | 部分 — §4.2 有指令，未腳本化 |
| 7 | `docs/DEPLOYMENT.md` | **本文件** |

另外三個阻擋上線的問題：

| # | 問題 | 影響 |
|---|---|---|
| 8 | **volume 沒放在資料碟上**（§0） | 磁碟快照備份完全無效，災難復原會失敗 |
| 9 | **前端沒有 Dockerfile** | [AI-Servo-Command-Center](https://github.com/yuwen628/AI-Servo-Command-Center) 目前只有 `run.py`（Flask，預設埠 5000／`AI_SERVO_PORT`），無法進 compose |
| 10 | **api 仍 publish 到 `127.0.0.1:8000`** | 正式環境應只有 caddy publish 80/443，api 不 publish（PROMPT §6 明確要求） |

第 8 項的修法是把 compose 的具名 volume 改成 bind mount：

```yaml
  postgres:
    volumes:
      - /srv/data/postgres:/var/lib/postgresql/data
  api:
    volumes:
      - /srv/data/engine:/srv/data/engine
```

改完後資料才真的落在快照涵蓋的磁碟上，§7.2 的復原流程也才成立。
**這個改動要在有正式資料之前做**，否則需要一次資料搬遷。
