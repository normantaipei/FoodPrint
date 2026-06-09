#!/usr/bin/env bash
#
# FoodPrint 一鍵安裝 / 部署 —— 從零到「後端 + 地圖前端」全部跑起來。
#
# 在一台全新、連 repo 都還沒拉的 VM 上，一條命令搞定（會自動裝 git/Docker、clone、部署）：
#
#   curl -fsSL https://raw.githubusercontent.com/normantaipei/FoodPrint/main/install.sh | bash
#
# 已經在 repo 內：
#   bash install.sh            # 安裝 / 升級並啟動
#   bash install.sh --down     # 停止整個系統（資料保留在 volume）
#   bash install.sh --logs     # 看即時 log
#
# 做的事：
#   0. （bootstrap）不在 repo 內時：自動裝 git/Docker → clone 到 ~/FoodPrint → 重跑自己。
#   1. 檢查 / 安裝 Docker、偵測 docker compose（必要時用 sudo）。
#   2. 第一次跑：產 .env（隨機 Postgres 密碼 + 讀寫/唯讀 token），之後沿用不覆寫。
#   3. 自動避開「已被占用的埠」：API 預設 8000、地圖前端預設 8080，被占用就往後找空的，
#      並把實際使用的埠寫回 .env（重跑會沿用自己這套，不會亂跳）。
#   4. docker compose up -d --build（後端 + PostgreSQL + nginx 地圖前端）。
#   5. 等後端健康檢查通過，自動把 Claude 端 backend client 指向本機。
#   6. 印出地圖網址（含區網 IP）與連線狀態。
#
# 環境變數（選用）：
#   FOODPRINT_REPO   要 clone 的 repo（預設 https://github.com/normantaipei/FoodPrint.git）
#   FOODPRINT_DIR    clone 目的地（預設 $HOME/FoodPrint）
#
set -euo pipefail

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "  \033[2m%s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# root 就不需要 sudo；否則有 sudo 就用。
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

REPO_URL="${FOODPRINT_REPO:-https://github.com/normantaipei/FoodPrint.git}"
TARGET_DIR="${FOODPRINT_DIR:-$HOME/FoodPrint}"

pkg_install() {  # 用偵測到的套件管理器安裝 $@
  if   command -v apt-get >/dev/null 2>&1; then $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "$@";
  elif command -v dnf     >/dev/null 2>&1; then $SUDO dnf install -y -q "$@";
  elif command -v yum     >/dev/null 2>&1; then $SUDO yum install -y -q "$@";
  elif command -v apk     >/dev/null 2>&1; then $SUDO apk add --no-cache "$@";
  elif command -v pacman  >/dev/null 2>&1; then $SUDO pacman -Sy --noconfirm "$@";
  elif command -v zypper  >/dev/null 2>&1; then $SUDO zypper -n install "$@";
  elif command -v brew    >/dev/null 2>&1; then brew install "$@";
  else return 1; fi
}

ensure_git() {
  command -v git >/dev/null 2>&1 && return
  info "未偵測到 git，安裝中…"
  pkg_install git || die "無法自動安裝 git，請手動安裝後重跑。"
  ok "git 已安裝"
}

# ── bootstrap：不在 repo 內（含 curl | bash）就裝 git → clone → 重跑自己 ──
self="${BASH_SOURCE[0]:-$0}"
self_dir=""
[ -f "$self" ] && self_dir="$(cd "$(dirname "$self")" 2>/dev/null && pwd)"

if [ -n "$self_dir" ] && [ -f "$self_dir/server/docker-compose.yml" ]; then
  REPO_ROOT="$self_dir"                       # 已經在 repo 內
elif [ "${FOODPRINT_BOOTSTRAPPED:-}" != "1" ]; then
  bold "🍜 FoodPrint bootstrap（這台機器還沒有 repo）"
  ensure_git
  if [ -d "$TARGET_DIR/.git" ]; then
    info "已存在 $TARGET_DIR，更新中…"
    git -C "$TARGET_DIR" pull --ff-only || warn "git pull 失敗，沿用現有版本"
  else
    info "clone $REPO_URL → $TARGET_DIR"
    git clone --depth 1 "$REPO_URL" "$TARGET_DIR" || die "git clone 失敗（repo 是否為 public？）"
  fi
  ok "repo 就緒：$TARGET_DIR"
  echo
  exec env FOODPRINT_BOOTSTRAPPED=1 bash "$TARGET_DIR/install.sh" "$@"
else
  die "bootstrap 後仍找不到 repo 內容（$TARGET_DIR）。"
fi

SERVER_DIR="$REPO_ROOT/server"
ENV_FILE="$SERVER_DIR/.env"
ENV_EXAMPLE="$SERVER_DIR/.env.example"

# ── Docker：沒裝就裝（Linux 用官方腳本）；決定是否要 sudo 才能用 ──────────
DOCKER=(docker)
ensure_docker() {
  if command -v docker >/dev/null 2>&1; then : ; else
    case "$(uname -s)" in
      Linux)
        info "未偵測到 Docker，使用官方腳本安裝（get.docker.com，需要 root/sudo）…"
        curl -fsSL https://get.docker.com | $SUDO sh || die "Docker 安裝失敗，請手動安裝後重跑。"
        $SUDO systemctl enable --now docker 2>/dev/null || $SUDO service docker start 2>/dev/null || true
        ;;
      Darwin) die "請先安裝並啟動 Docker Desktop：https://www.docker.com/products/docker-desktop/ 後再重跑。" ;;
      *) die "未知作業系統，請先自行安裝 Docker 後重跑。" ;;
    esac
  fi
  # daemon 沒起來就試著起
  if ! docker info >/dev/null 2>&1; then
    $SUDO systemctl start docker 2>/dev/null || $SUDO service docker start 2>/dev/null || true
  fi
  # 仍連不上 → 試 sudo（剛裝完、使用者還沒加進 docker 群組時很常見）
  if ! docker info >/dev/null 2>&1; then
    if [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then DOCKER=($SUDO docker); else
      die "Docker 沒在跑或權限不足。請啟動 Docker（或把使用者加進 docker 群組重新登入）後重跑。"
    fi
  fi
}

# ── compose 包裝（在 server/ 目錄內執行；自動偵測 v2 / v1）──────────────
detect_compose() {
  if "${DOCKER[@]}" compose version >/dev/null 2>&1; then COMPOSE=("${DOCKER[@]}" compose);
  elif command -v docker-compose >/dev/null 2>&1; then COMPOSE=(${SUDO:+$SUDO} docker-compose);
  else die "找不到 docker compose。請安裝較新的 Docker（內含 compose v2）。"; fi
}
dc() { ( cd "$SERVER_DIR" && "${COMPOSE[@]}" "$@" ); }

# ── 子命令：down / logs ────────────────────────────────────────────────
case "${1:-}" in
  --down|down|stop)
    ensure_docker; detect_compose; bold "停止 FoodPrint…"; dc down; ok "已停止（資料保留在 volume）"; exit 0 ;;
  --logs|logs)
    ensure_docker; detect_compose; dc logs -f --tail=100; exit 0 ;;
  -h|--help|help)
    sed -n '3,30p' "$0"; exit 0 ;;
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
free_port() {  # $1 = 起始埠 → 印出第一個「真的沒人用」的埠
  python3 - "$1" <<'PY'
import socket, sys
start = int(sys.argv[1])

def in_use(port):
    # 1) 不用 SO_REUSEADDR：別人若已綁這埠（含只綁 127.0.0.1 且開了
    #    SO_REUSEADDR 的服務），這個 bind 會直接 EADDRINUSE，不會誤判成空。
    for host in ("0.0.0.0", "127.0.0.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            s.close(); return True
        s.close()
    # 2) 再保險：有沒有人正在 listen（擋掉 SO_REUSEPORT 之類的邊角情況）。
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.2)
    try:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return True
    finally:
        s.close()
    return False

for port in range(start, start + 300):
    if not in_use(port):
        print(port); break
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
command -v python3 >/dev/null 2>&1 || pkg_install python3 || die "需要 python3，請先安裝。"
ensure_docker
detect_compose
ok "Docker 與 docker compose 就緒${DOCKER[0]:+（${DOCKER[*]} compose）}"

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
SKILL_HOST="$([ "$IP" != "localhost" ] && echo "$IP" || echo localhost)"
echo
bold "✅ 完成！"
echo
printf "  \033[1m地圖前端\033[0m   http://localhost:%s\n" "$WEB_PORT"
[ "$IP" != "localhost" ] && printf "             區網其他裝置：http://%s:%s\n" "$IP" "$WEB_PORT"
printf "  \033[1m後端 API\033[0m   http://localhost:%s   （健康檢查 /health）\n" "$API_PORT"
printf "  \033[1m下載 skill\033[0m  http://%s:%s/skill   （限區網、免 token，連線設定即時烤入）\n" "$SKILL_HOST" "$API_PORT"
echo
info "skill 下載（區網內另一台機器 / 手機開這網址就會下載 foodprint-skill.zip）："
info "  讀寫版（自己入庫用）：http://${SKILL_HOST}:${API_PORT}/skill"
info "  唯讀版（只給人搜尋）：http://${SKILL_HOST}:${API_PORT}/skill?token=ro"
info "  → Claude Desktop → 設定 → Capabilities/Skills → Upload skill → 選這個 zip → 啟用（已連好這台後端）。"
info "  （也可在這台直接用：ln -s \"$REPO_ROOT\" ~/.claude/skills/foodprint）"
info "  注意：入庫 / 刪除是區網限定（require_lan），要在區網內的 Claude Code 跑；查詢不限。"
echo
info "專案位置：$REPO_ROOT"
info "讀寫 token（入庫 / 刪除用）：$RW_TOKEN"
info "唯讀 token（給只查詢的人）：$RO_TOKEN"
info "兩組 token 隨時再查：grep TOKEN $ENV_FILE  ｜ 秘密與埠都在 server/.env（不進版控）。停止：bash $REPO_ROOT/install.sh --down"
echo
bold "下一步：開上面的『下載 skill』網址裝上 skill，或在對話貼 Google Maps 連結 / 照片說「加進我的口袋名單」📍"
