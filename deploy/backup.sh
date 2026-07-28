#!/usr/bin/env bash
# deploy/backup.sh — 每日備份：pg_dump + engine 打包，各留 7 份
#
# 這是 DEPLOYMENT.md「平常的維護」那兩段手動指令的腳本版。
# 快照救「機器爆炸」，這個救「手滑刪表」—— 兩種都要有。
#
# 安裝成每天 03:00 自動跑（在 ~/backend 執行一次即可）：
#   ( crontab -l 2>/dev/null; echo "0 3 * * * $HOME/backend/deploy/backup.sh >> /srv/data/backups/backup.log 2>&1" ) | crontab -
#
# 確認裝好了：crontab -l

set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file .env)
DEST=/srv/data/backups
KEEP=7
TODAY=$(date +%F)

mkdir -p "$DEST"

echo "[$(date -Is)] 開始備份"

# 資料庫（-T：cron 沒有 tty）
"${COMPOSE[@]}" exec -T postgres pg_dump -U aiservo aiservo \
  | gzip > "$DEST/aiservo-$TODAY.sql.gz.tmp"
# 先寫 tmp 再改名，避免備份到一半失敗留下壞檔被誤認成好備份
mv "$DEST/aiservo-$TODAY.sql.gz.tmp" "$DEST/aiservo-$TODAY.sql.gz"

# engine 檔案
tar czf "$DEST/engine-$TODAY.tar.gz.tmp" -C /srv/data engine
mv "$DEST/engine-$TODAY.tar.gz.tmp" "$DEST/engine-$TODAY.tar.gz"

# 各只留最近 KEEP 份
ls -1t "$DEST"/aiservo-*.sql.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm
ls -1t "$DEST"/engine-*.tar.gz  2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm

# 簡單健檢：今天的 dump 不能是空的
SIZE=$(stat -c %s "$DEST/aiservo-$TODAY.sql.gz")
if [ "$SIZE" -lt 200 ]; then
  echo "[$(date -Is)] ⚠️ 今天的 pg_dump 只有 ${SIZE} bytes，八成是空的，請檢查！"
  exit 1
fi

echo "[$(date -Is)] 完成：aiservo-$TODAY.sql.gz (${SIZE} bytes)"
