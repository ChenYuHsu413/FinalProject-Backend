#!/usr/bin/env bash
# deploy/deploy.sh — 在 VM 上執行的部署／更新腳本
#
# 做的事 = DEPLOYMENT.md「步驟 4.3 + 更新程式 + 步驟 5 驗證」全自動化：
#   1. 前置檢查（.env、DATA_ROOT）
#   2. git pull（--no-pull 可跳過）
#   3. build → 起 postgres/redis → alembic upgrade → 全部起來
#   4. 驗證：容器健康、/health、稽核鏈 verify、integrations、經 caddy 打 API、埠檢查
#
# 前端在另一台 VM（35.194.234.205），不歸這個腳本管。對接前端只需要：
#   把 .env 的 SERVICE_TOKEN 交給前端那邊，並請對方設
#   BACKEND_BASE_URL=http://<這台對外IP>/api/v1
#
# 用法（在 ~/backend）：
#   ./deploy/deploy.sh            # 完整部署＋驗證
#   ./deploy/deploy.sh --no-pull  # 不 git pull（例如剛手動 checkout 舊版）
#   ./deploy/deploy.sh --verify   # 只跑驗證（步驟 5）

set -euo pipefail

cd "$(dirname "$0")/.."          # 進到後端 repo 根目錄
COMPOSE=(docker compose -f deploy/docker-compose.prod.yml --env-file .env)

PULL=1; VERIFY_ONLY=0
for a in "$@"; do
  case "$a" in
    --no-pull) PULL=0 ;;
    --verify)  VERIFY_ONLY=1 ;;
    *) echo "未知參數：$a"; exit 1 ;;
  esac
done

say()  { printf '\n==> %s\n' "$*"; }
fail() { printf '\n❌ %s\n' "$*"; exit 1; }

# ---------- 前置檢查 ----------
[ -f .env ] || fail "找不到 .env（cp .env.example .env 之後照 DEPLOYMENT.md 4.2 填好）"

DATA_ROOT=$(grep -E '^DATA_ROOT=' .env | cut -d= -f2- || true)
[ "$DATA_ROOT" = "/srv/data" ] \
  || fail ".env 的 DATA_ROOT 是「${DATA_ROOT:-（沒設）}」，正式環境必須是 /srv/data，否則資料會落在沒備份的開機碟上"
mountpoint -q /srv/data \
  || fail "/srv/data 不是掛載點 —— 資料碟還沒掛上來（DEPLOYMENT.md 步驟 3.1）"

if [ "$VERIFY_ONLY" -eq 0 ]; then
  # ---------- 拉 code ----------
  if [ "$PULL" -eq 1 ]; then
    say "更新後端"
    git pull --ff-only
  fi

  # ---------- 建置與啟動 ----------
  say "建置映像"
  "${COMPOSE[@]}" build

  say "先起 postgres / redis"
  "${COMPOSE[@]}" up -d postgres redis
  say "等資料庫 healthy"
  for i in $(seq 1 30); do
    st=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' | awk '$1=="postgres"{print $2}')
    [ "$st" = "healthy" ] && break
    sleep 2
  done
  [ "$st" = "healthy" ] || fail "postgres 一直沒 healthy，看 log：${COMPOSE[*]} logs postgres"

  say "資料庫遷移（alembic upgrade head）"
  "${COMPOSE[@]}" run --rm api alembic upgrade head

  say "全部起來"
  "${COMPOSE[@]}" up -d
fi

# ---------- 驗證（= DEPLOYMENT.md 步驟 5）----------
say "驗證 5.1：容器狀態"
sleep 5
"${COMPOSE[@]}" ps
UNHEALTHY=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.State}} {{.Health}}' \
  | awk '$2!="running" || ($3!="" && $3!="healthy")' || true)
[ -z "$UNHEALTHY" ] || fail "有容器不健康：
$UNHEALTHY"

say "驗證 5.2：後端 /health"
"${COMPOSE[@]}" exec -T api python -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/health').read().decode())" \
  || fail "後端 /health 沒回應"

say "驗證 5.3：稽核鏈"
TOKEN=$(grep '^SERVICE_TOKEN=' .env | cut -d= -f2-)
CHAIN=$("${COMPOSE[@]}" exec -T api python -c "
import urllib.request
req = urllib.request.Request('http://localhost:8000/api/v1/audit/chain/verify', headers={
    'Authorization': 'Bearer $TOKEN',
    'X-User-ID': 'admin-1', 'X-User-Role': 'admin',
    'X-Correlation-ID': 'deploy-verify'})
print(urllib.request.urlopen(req).read().decode())")
echo "$CHAIN"
echo "$CHAIN" | grep -q '"verified":\s*true' || fail "稽核鏈 verified 不是 true —— 立刻停止上線、保留現場，不要重啟或清資料（見 DEPLOYMENT.md 5.3）"

say "驗證 5.4：誠實標示 /system/integrations"
"${COMPOSE[@]}" exec -T api python -c "
import urllib.request
req = urllib.request.Request('http://localhost:8000/api/v1/system/integrations', headers={
    'Authorization': 'Bearer $TOKEN',
    'X-User-ID': 'admin-1', 'X-User-Role': 'admin',
    'X-Correlation-ID': 'deploy-verify'})
print(urllib.request.urlopen(req).read().decode())"

say "驗證 5.5：從門口打 API（caddy → api，模擬前端的路徑）"
curl -fsS -o /dev/null -w 'caddy 健康 HTTP %{http_code}\n' http://localhost/caddy-health \
  || fail "caddy 沒回應"
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-User-ID: admin-1" -H "X-User-Role: admin" \
  -H "X-Correlation-ID: deploy-verify" \
  http://localhost/api/v1/health)
[ "$CODE" = "200" ] || fail "經 caddy 打 /api/v1/health 得到 HTTP $CODE（前端會遇到同樣的問題）"
echo "經 caddy 打 API：HTTP $CODE"

say "驗證 5.6：本機有沒有多開埠（應該只有 80/443）"
LISTEN=$(ss -tlnp 2>/dev/null | awk 'NR>1{print $4}' | grep -E ':(5432|6379|8000|5000)$' || true)
[ -z "$LISTEN" ] || fail "這些內部服務的埠不該對外監聽：
$LISTEN"
echo "OK"

cat <<'EOF'

✅ 部署完成、驗證全數通過。
剩下要「人」做的事（腳本代替不了）：
  1. 把 SERVICE_TOKEN 交給前端負責人（grep ^SERVICE_TOKEN= .env），
     請對方在 35.194.234.205 那台設定：
       SERVICE_TOKEN=<同一個值>
       BACKEND_BASE_URL=http://<這台的對外IP>/api/v1
     然後重啟前端。
  2. 用瀏覽器開前端網站登入一次，確認畫面有真實資料、
     且後端稽核紀錄查得到這次登入（DEPLOYMENT.md 5.5）
  3. 在自己電腦確認防火牆：
       gcloud compute firewall-rules list --filter="direction=INGRESS"
     這台應該只有 80/443（且來源最好限縮成前端 IP，見 gcp-setup.sh）
EOF
