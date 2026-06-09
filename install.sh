#!/usr/bin/env bash
#
# FoodPrint 一鍵安裝 / 部署 —— 從零到「後端 + 地圖前端」全部跑起來。
#
#   bash install.sh            # 安裝 / 升級並啟動
#   bash install.sh --down     # 停止整個系統（資料保留在 volume）
#   bash install.sh --logs     # 看即時 log
#
# 做的事：
#   1. 檢查 Docker / docker compose。
#   2. 第一次跑：產 .env（隨機 Postgres 密碼 + 讀寫/唯讀 token），之後沿用不覆寫。
#   3. 自動避開「已被占用的埠」：API 預設 8000、地圖前端預設 8080，被占用就往後找空的，
#      並把實際使用的埠寫回 .env（重跑會沿用自己這套，不會亂跳）。
#   4. docker compose up -d --build（後端 + PostgreSQL + nginx 地圖前端）。
#   5. 等後端健康檢查通過，自動把 Claude 端 backend client 指向本機。
#   6. 印出地圖網址（含區網 IP）與連線狀態。
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$REPO_ROOT/server"
ENV_FILE="$SERVER_DIR/.env"
ENV_EXAMPLE="$SERVER_DIR/.env.example"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  \033[2m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ── compose 包裝（在 server/ 目錄內執行；自動偵測 v2 / v1）──────────────
detect_compose() {
  if docker compose version >/dev/null 2>&1; then COMPOSE=(docker compose);
  elif command -v docker-compose >/dev/null 2>&1; then COMPOSE=(docker-compose);
  else die "找不到 docker compose。請先安裝 Docker Desktop / Docker Engine。"; fi
}
dc() { ( cd "$SERVER_DIR" && "${COMPOSE[@]}" "$@" ); }

# ── 子命令：down / logs ────────────────────────────────────────────────
case "${1:-}" in
  --down|down|stop)
    detect_compose; bold "停止 FoodPrint…"; dc down; ok "已停止（資料保留在 volume）"; exit 0 ;;
  --logs|logs)
    detect_compose; dc logs -f --tail=100; exit 0 ;;
  -h|--help|help)
    sed -n '3,20p' "$0"; exit 0 ;;
esac

# ── .env helpers（用 python3 做可攜的 upsert/讀取）──────────────────────
envset() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys, pathlib
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
out, done = [], False
for ln in lines:
    if ln.split("=", 1)[0].strip() == key and not ln.lstrip().startswith("#"):
        out.append(f"{key}={val}"); done = True
    else:
        out.append(ln)
if not done:
    out.append(f"{key}={val}")
p.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
}
envget() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys, pathlib
path, key = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
if p.exists():
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.lstrip()
        if s.startswith("#"):
            continue
        if "=" in ln and ln.split("=", 1)[0].strip() == key:
            print(ln.split("=", 1)[1].strip()); break
PY
}
gen_token() { python3 -c "import secrets; print(secrets.token_hex(32))"; }

# ── 找空埠：preferred 起跳，被占用就往後找；但若該埠已是我們自己的 compose
#    服務在用，就沿用、不換（重跑時不亂跳）。───────────────────────────
free_port() {  # $1 = 起始埠 → 印出第一個可綁定的埠
  python3 - "$1" <<'PY'
import socket, sys
start = int(sys.argv[1])
for port in range(start, start + 300):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port)); s.close(); print(port); break
    except OSError:
        s.close()
else:
    sys.exit("找不到可用的埠")
PY
}
our_port() {  # 服務 $1 容器埠 $2 → 若我們的 compose 正在發布該埠，印出 host 埠
  dc port "$1" "$2" 2>/dev/null | sed -n 's/.*:\([0-9][0-9]*\)$/\1/p' | head -n1
}
pick_port() {  # preferred=$1 service=$2 container_port=$3
  local mine; mine="$(our_port "$2" "$3" || true)"
  if [ -n "$mine" ]; then echo "$mine"; return; fi
  free_port "$1"
}

# ── 區網 IP（給地圖頁在手機 / 其他電腦上開）────────────────────────────
lan_ip() {
  local ip
  if command -v ipconfig >/dev/null 2>&1; then
    for i in en0 en1 en2; do ip="$(ipconfig getifaddr "$i" 2>/dev/null || true)"; [ -n "$ip" ] && { echo "$ip"; return; }; done
  fi
  if command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; [ -n "$ip" ] && { echo "$ip"; return; }
  fi
  echo "localhost"
}

# ════════════════════════════════════════════════════════════════════════
bold "🍜 FoodPrint 一鍵安裝"
detect_compose
docker info >/dev/null 2>&1 || die "Docker 沒在跑。請先啟動 Docker Desktop / dockerd。"
ok "Docker 與 docker compose 就緒"

# 1) .env：第一次產生，之後沿用不覆寫秘密 ----------------------------------
if [ ! -f "$ENV_FILE" ]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"; info "已從範本建立 server/.env"
fi
pw="$(envget POSTGRES_PASSWORD)"
if [ -z "$pw" ] || [ "$pw" = "change-me-please" ]; then envset POSTGRES_PASSWORD "$(gen_token)"; ok "已產生隨機 Postgres 密碼"; fi
[ -z "$(envget FOODPRINT_TOKEN)" ]      && { envset FOODPRINT_TOKEN "$(gen_token)";      ok "已產生『讀寫』token"; }
[ -z "$(envget FOODPRINT_READ_TOKEN)" ] && { envset FOODPRINT_READ_TOKEN "$(gen_token)"; ok "已產生『唯讀』token"; }

RW_TOKEN="$(envget FOODPRINT_TOKEN)"
RO_TOKEN="$(envget FOODPRINT_READ_TOKEN)"
# 前端 nginx 代理用的 token：優先唯讀，否則讀寫。
PROXY_TOKEN="${RO_TOKEN:-$RW_TOKEN}"
envset FOODPRINT_PROXY_TOKEN "$PROXY_TOKEN"

# 2) 自動避埠 -------------------------------------------------------------
api_pref="$(envget API_PORT)"; web_pref="$(envget WEB_PORT)"
API_PORT="$(pick_port "${api_pref:-8000}" api 8000)"
WEB_PORT="$(pick_port "${web_pref:-8080}" web 80)"
[ "$WEB_PORT" = "$API_PORT" ] && WEB_PORT="$(free_port "$((API_PORT + 1))")"
envset API_PORT "$API_PORT"
envset WEB_PORT "$WEB_PORT"
ok "後端埠 ${API_PORT}、地圖前端埠 ${WEB_PORT}（已避開占用中的埠）"

# 3) 啟動 ----------------------------------------------------------------
bold "🚀 部署中（docker compose up -d --build）…"
dc up -d --build
ok "容器已啟動"

# 4) 等後端健康檢查 -------------------------------------------------------
printf "  等後端就緒"
ready=""
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${API_PORT}/health" >/dev/null 2>&1; then ready=1; break; fi
  printf "."; sleep 2
done
printf "\n"
if [ -n "$ready" ]; then ok "後端健康檢查通過"; else
  warn "後端尚未回應（首次初始化 Postgres 可能較久）。稍後可看 log：bash install.sh --logs"
fi

# 5) 自動把 Claude 端 client 指向本機 -------------------------------------
if [ -n "$ready" ]; then
  python3 "$REPO_ROOT/scripts/backend.py" set --base-url "http://localhost:${API_PORT}" --token "$RW_TOKEN" >/dev/null 2>&1 \
    && ok "已把 backend client 指向 http://localhost:${API_PORT}" \
    || warn "backend client 自動設定失敗，可手動：python3 scripts/backend.py set --base-url http://localhost:${API_PORT} --token <讀寫 token>"
fi

# 6) 總結 ----------------------------------------------------------------
IP="$(lan_ip)"
echo
bold "✅ 完成！"
echo
printf "  \033[1m地圖前端\033[0m   http://localhost:%s\n" "$WEB_PORT"
[ "$IP" != "localhost" ] && printf "             區網其他裝置：http://%s:%s\n" "$IP" "$WEB_PORT"
printf "  \033[1m後端 API\033[0m   http://localhost:%s   （健康檢查 /health）\n" "$API_PORT"
echo
info "讀寫 token（入庫 / 刪除用）：$RW_TOKEN"
info "唯讀 token（給只查詢的人）：$RO_TOKEN"
info "秘密與埠都在 server/.env（不進版控）。停止：bash install.sh --down"
echo
bold "下一步：在 Claude 對話貼 Google Maps 連結 / 照片，說「加進我的口袋名單」，再回地圖頁看 pin 📍"
