#!/usr/bin/env bash
# deploy/gcp-setup.sh — 自動化 DEPLOYMENT.md 步驟 1–2：
#   資料碟 → VM（掛資料碟）→ 防火牆（只開 80/443，SSH 走 IAP）→ 每日快照
#
# 在「你自己的電腦」上執行（不是 VM 裡），需要已登入的 gcloud。
#
# 用法：
#   PROJECT_ID=my-project ./deploy/gcp-setup.sh
# 可覆寫：ZONE REGION VM DATA_DISK
#
# 冪等：已存在的資源會跳過，不會重建、不會動到資料。

set -euo pipefail

: "${PROJECT_ID:?請設定 PROJECT_ID，例如 PROJECT_ID=my-project $0}"
ZONE="${ZONE:-asia-east1-b}"
REGION="${REGION:-asia-east1}"
VM="${VM:-ai-servo-vm}"
DATA_DISK="${DATA_DISK:-ai-servo-data}"
# 允許連進 80/443 的來源。預設只放行前端那台 VM。
# 要自己從瀏覽器直接測後端的話，把你的 IP 也加進來（逗號分隔），
# 或暫時 ALLOW_SOURCES=0.0.0.0/0（API 有 token 保護，但能鎖就鎖）。
ALLOW_SOURCES="${ALLOW_SOURCES:-35.194.234.205/32}"

gcloud config set project "$PROJECT_ID" >/dev/null

say() { printf '\n==> %s\n' "$*"; }

exists() { gcloud compute "$1" describe "$2" "${@:3}" >/dev/null 2>&1; }

say "開啟需要的 API"
gcloud services enable compute.googleapis.com iap.googleapis.com

say "資料碟 $DATA_DISK"
if exists disks "$DATA_DISK" --zone="$ZONE"; then
  echo "已存在，跳過（不會動到裡面的資料）"
else
  gcloud compute disks create "$DATA_DISK" \
    --size=20GB --type=pd-balanced --zone="$ZONE"
fi

say "VM $VM"
if exists instances "$VM" --zone="$ZONE"; then
  echo "已存在，跳過"
else
  gcloud compute instances create "$VM" \
    --zone="$ZONE" \
    --machine-type=e2-medium \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=30GB \
    --boot-disk-type=pd-balanced \
    --disk=name="$DATA_DISK",device-name=data,mode=rw,boot=no \
    --tags=web \
    --metadata=enable-oslogin=TRUE
fi

say "防火牆：只開 80/443，來源限 $ALLOW_SOURCES"
if gcloud compute firewall-rules describe allow-web >/dev/null 2>&1; then
  echo "allow-web 已存在，更新來源清單"
  gcloud compute firewall-rules update allow-web --source-ranges="$ALLOW_SOURCES"
else
  gcloud compute firewall-rules create allow-web \
    --allow=tcp:80,tcp:443 --target-tags=web \
    --source-ranges="$ALLOW_SOURCES" --description="web (frontend VM only)"
fi

say "防火牆：SSH 只走 IAP"
if gcloud compute firewall-rules describe allow-ssh-iap >/dev/null 2>&1; then
  echo "allow-ssh-iap 已存在，跳過"
else
  gcloud compute firewall-rules create allow-ssh-iap \
    --allow=tcp:22 --source-ranges=35.235.240.0/20 \
    --target-tags=web --description="SSH via IAP only"
fi

say "每日快照排程（只綁資料碟，保留 14 天）"
if gcloud compute resource-policies describe daily-backup --region="$REGION" >/dev/null 2>&1; then
  echo "daily-backup 已存在，跳過"
else
  gcloud compute resource-policies create snapshot-schedule daily-backup \
    --region="$REGION" --daily-schedule --start-time=18:00 --max-retention-days=14
fi
if gcloud compute disks describe "$DATA_DISK" --zone="$ZONE" \
     --format="value(resourcePolicies)" | grep -q daily-backup; then
  echo "資料碟已綁定快照排程，跳過"
else
  gcloud compute disks add-resource-policies "$DATA_DISK" \
    --resource-policies=daily-backup --zone="$ZONE"
fi

say "檢查有沒有可疑的對外規則（5432 / 6379 / 8000 / 5000 / 22 對全世界）"
BAD=$(gcloud compute firewall-rules list \
  --format="value(name,sourceRanges.list(),allowed[].map().firewall_rule().list())" \
  | grep '0.0.0.0/0' | grep -E 'tcp:(22|5432|6379|8000|5000)' || true)
if [ -n "$BAD" ]; then
  echo "⚠️  發現下列規則把內部服務或 SSH 開給全世界，請手動確認並刪除："
  echo "$BAD"
else
  echo "OK，沒有多開的埠"
fi

say "完成。下一步："
cat <<EOF
  1. 連進機器：
       gcloud compute ssh $VM --zone=$ZONE --tunnel-through-iap
  2. 在機器裡照 DEPLOYMENT.md 步驟 3 掛資料碟、裝 Docker
     （⚠️ 只有「全新」資料碟才能格式化；接舊碟一定要跳過 mkfs 那行）
  3. 之後的部署都用 deploy/deploy.sh
EOF
