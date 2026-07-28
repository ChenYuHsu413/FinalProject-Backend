#!/usr/bin/env bash
# deploy/gen-secrets.sh — 初始化 .env：產生密碼、設定正式環境參數
#
# 只在「第一次」部署時跑。已存在的 .env 不會被覆蓋（避免把正在用的密碼換掉
# 導致資料庫連不上）。
#
# 用法（在 ~/backend）：
#   ./deploy/gen-secrets.sh                 # 沒網域
#   DOMAIN=example.com ./deploy/gen-secrets.sh   # 有網域（caddy 會自動申請 HTTPS）
#
# 跑完後：
#   1. 把印出的 SERVICE_TOKEN 交給前端負責人（前端在另一台 VM，必須手動同步）
#   2. 確認 MOCK_MODE / MODEL_SOURCE 是否符合現況

set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then
  echo "❌ .env 已存在，不覆蓋。"
  echo "   真的要重來：先備份再刪（cp .env .env.bak && rm .env），但注意："
  echo "   換掉 POSTGRES_PASSWORD 之後，既有資料庫會連不上（密碼存在資料碟裡）。"
  exit 1
fi

cp .env.example .env
chmod 600 .env

SERVICE_TOKEN=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 32)

sed -i "s|^SERVICE_TOKEN=.*|SERVICE_TOKEN=$SERVICE_TOKEN|" .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$POSTGRES_PASSWORD|" .env
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://aiservo:$POSTGRES_PASSWORD@postgres:5432/aiservo|" .env
sed -i "s|^APP_ENV=.*|APP_ENV=prod|" .env
sed -i "s|^DATA_ROOT=.*|DATA_ROOT=/srv/data|" .env
grep -qE '^DATA_ROOT=' .env || echo "DATA_ROOT=/srv/data" >> .env
sed -i "s|^ENGINE_DATA_DIR=.*|ENGINE_DATA_DIR=/srv/data/engine|" .env

# 正式環境 compose 額外需要的值
echo "" >> .env
echo "# --- 由 gen-secrets.sh 加入（正式環境用） ---" >> .env
echo "DOMAIN=${DOMAIN:-}" >> .env

echo "✅ .env 已建立（chmod 600）"
echo ""
echo "   DATA_ROOT        = /srv/data"
echo "   APP_ENV          = prod"
echo "   DOMAIN           = ${DOMAIN:-（沒設，先用 IP 走 http）}"
echo ""
echo "   SERVICE_TOKEN    = $SERVICE_TOKEN"
echo "   ↑ 前端在另一台 VM（35.194.234.205），請把這個值交給前端負責人，"
echo "     連同 BACKEND_BASE_URL=http://<這台對外IP>/api/v1 一起設定並重啟前端。"
echo "     兩邊不一致 = 前端每個請求都 403。"
echo ""
echo "接著手動確認 .env 裡的："
echo "   MOCK_MODE（資料還是模擬的就維持 true，不要為了好看改 false）"
echo "   MODEL_SOURCE / MODEL_SERVICE_URL（要接外部模型服務才改）"
