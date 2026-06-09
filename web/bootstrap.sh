#!/usr/bin/env bash
# FoodPrint 美食地圖前端（Nuxt 3）— 一鍵架設（給全新的空 Linux VM，可獨立於後端部署）
#
# 一般情況用 repo 根的 install.sh 即可（會連同後端一起把前端 compose 起來）。
# 這支腳本是「把前端單獨架在另一台機器」時用：後端的 domain 與『讀取』token 用後綴帶上。
#
#   # 位置參數（建議；像後綴一樣接在後面）
#   curl -fsSL https://raw.githubusercontent.com/normantaipei/FoodPrint/main/web/bootstrap.sh \
#     | bash -s -- http://192.168.1.50:8000 <read_token>
#
#   # 或用環境變數
#   curl -fsSL https://raw.githubusercontent.com/normantaipei/FoodPrint/main/web/bootstrap.sh \
#     | BASE_URL=http://192.168.1.50:8000 TOKEN=<read_token> bash
#
# 這支腳本會（缺什麼裝什麼，可重複執行）：
#   1. 沒有 Docker 就用官方 get.docker.com 裝好 Docker
#   2. 沒有 git 就裝起來，然後把 repo clone 到 ~/FoodPrint（已存在就 git pull）
#   3. docker build 出 Nuxt 前端映像（多階段，產出精簡 Node runtime）
#   4. 收掉自己的舊前端容器後挑一個沒被占用的對外埠（從 WEB_PORT 起自動往上避讓）
#   5. docker run 起容器：後端 domain + 讀取 token 以「環境變數」帶入（不烤進映像、
#      也不會送到瀏覽器）；--restart unless-stopped 開機自動起
#   6. 印出前端網址
#
# 帶後端連線資訊的方式（domain 必填；token 視後端有沒有設）：
#   $1 / BASE_URL   後端 domain，如 http://192.168.1.50:8000
#   $2 / TOKEN      後端的『讀取』token（FOODPRINT_READ_TOKEN；後端沒設就留空 ""）
#
# 可用環境變數覆寫：
#   FOODPRINT_DIR   安裝目錄（預設 $HOME/FoodPrint）
#   FOODPRINT_REPO  repo 網址（預設官方 GitHub）
#   WEB_PORT        前端對外埠（預設 8080，被占用會自動往上避讓）
set -euo pipefail

REPO="${FOODPRINT_REPO:-https://github.com/normantaipei/FoodPrint.git}"
DIR="${FOODPRINT_DIR:-$HOME/FoodPrint}"
WEB_PORT="${WEB_PORT:-8080}"
CONTAINER="foodprint-web"
IMAGE="foodprint-web"

# 後端連線資訊：位置參數優先，否則退回環境變數
BASE_URL="${1:-${BASE_URL:-}}"
TOKEN="${2:-${TOKEN:-}}"

# root 直接跑；非 root 就在需要時加 sudo
if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
DOCKER_SUDO="$SUDO"

log() { printf '\n\033[1;36m▸ %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

port_in_use() {  # 這個 host 上有沒有人在 listen 這個埠（含其他 docker 容器發布的）
  local p="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${p}\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1  # 偵測不了就當沒占用，交給 docker 自己報錯
  fi
}

find_free_port() {  # 從 $1 起往上找第一個沒被占用的埠
  local start="$1" cand i
  for i in $(seq 0 50); do
    cand=$((start + i))
    if ! port_in_use "$cand"; then echo "$cand"; return 0; fi
  done
  echo "$start"  # 找不到就回原值，讓 docker 自己報錯
}

# 在受限環境（如 Proxmox 非特權 LXC）下，docker-default 的 AppArmor profile 會擋掉
# esbuild service 行程的 socket（nuxt build 報 read ENOTCONN），而 docker build 不吃
# --security-opt。改在吃得下該參數的 docker run 容器內編譯（apparmor=unconfined），
# 把 .output 倒進暫存目錄，再用極簡 Dockerfile 組 runtime 映像（runtime 不需要 esbuild）。
build_web_in_container() {
  local stage; stage="$(mktemp -d)"
  log "改用『容器內編譯 + apparmor=unconfined』避開 AppArmor 對 esbuild 的限制…"
  $DOCKER_SUDO docker run --rm \
    --security-opt apparmor=unconfined \
    -v "$DIR/web":/src:ro \
    -v "$stage":/out \
    node:22-slim bash -lc '
      set -e
      mkdir -p /build && cp -a /src/. /build/ && cd /build
      # 丟掉 host 帶來的 lockfile / node_modules：lockfile 多半是 macOS 產的、缺 linux
      # optional 原生套件（@oxc-parser/binding-linux-*、esbuild 等），照它裝會觸發
      # npm/cli#4828。刪掉後從 package.json 全新解析，linux 原生 binding 才會齊。
      rm -rf node_modules package-lock.json npm-shrinkwrap.json
      npm install --ignore-scripts --no-audit --no-fund
      find node_modules -path "*@esbuild/*/bin/esbuild" -exec chmod +x {} +
      npm run build
      cp -a /build/.output /out/.output
    ' || { rm -rf "$stage" 2>/dev/null || $SUDO rm -rf "$stage"; die "容器內編譯前端失敗。"; }
  cat > "$stage/Dockerfile" <<'DOCKERFILE'
FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production NITRO_HOST=0.0.0.0 NITRO_PORT=3000 PORT=3000
COPY .output ./.output
EXPOSE 3000
CMD ["node", ".output/server/index.mjs"]
DOCKERFILE
  $DOCKER_SUDO docker build -t "$IMAGE" "$stage"
  rm -rf "$stage" 2>/dev/null || $SUDO rm -rf "$stage" 2>/dev/null || true
}

# ── 0. 檢查必填的後端 domain ─────────────────────────────────────────
if [ -z "$BASE_URL" ]; then
  die "沒帶後端 domain。用法：curl -fsSL …/web/bootstrap.sh | bash -s -- http://<後端IP>:<埠> <read_token>"
fi

# ── 1. Docker ───────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  log "安裝 Docker（官方 get.docker.com）…"
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO systemctl enable --now docker 2>/dev/null || true
else
  log "Docker 已安裝，略過。"
fi

# ── 2. git + 取得程式碼 ─────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  log "安裝 git…"
  $SUDO apt-get update -y && $SUDO apt-get install -y git
fi

if [ -d "$DIR/.git" ]; then
  log "repo 已在 $DIR，git pull 更新…"
  git -C "$DIR" pull --ff-only || true
else
  log "clone $REPO → $DIR …"
  git clone --depth 1 "$REPO" "$DIR"
fi

cd "$DIR/web"

# ── 3. 建構前端映像 ─────────────────────────────────────────────────
# 先試正常的多階段 docker build（一般主機走這條、有 layer cache）；若失敗（多半是
# Proxmox 非特權 LXC 的 AppArmor 擋 esbuild service），自動退到容器內編譯的 fallback。
log "docker build 前端映像（首次會拉 node 映像 + npm install，請稍候）…"
if ! $DOCKER_SUDO docker build -t "$IMAGE" .; then
  log "docker build 失敗，啟用 fallback…"
  build_web_in_container
fi

# ── 4. 決定對外埠（自動避讓）─────────────────────────────────────────
log "收掉本專案舊前端容器以釋放埠…"
$DOCKER_SUDO docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

PORT="$(find_free_port "$WEB_PORT")"
if [ "$PORT" != "$WEB_PORT" ]; then
  log "埠 ${WEB_PORT} 已被其他服務占用，自動改用 ${PORT}。"
fi

# ── 5. 起容器：domain + token 以環境變數帶入（不烤進映像、不外洩給瀏覽器）─
log "啟動 Nuxt 前端容器，接上後端 ${BASE_URL} …"
$DOCKER_SUDO docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "${PORT}:3000" \
  -e NUXT_FOODPRINT_BASE_URL="$BASE_URL" \
  -e NUXT_FOODPRINT_TOKEN="$TOKEN" \
  "$IMAGE" >/dev/null

# ── 6. 連線資訊 ─────────────────────────────────────────────────────
# 對外 IP：抓第一個非 loopback 的 IPv4 當提示（抓不到就用 localhost）
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "$HOST_IP" ] && HOST_IP="localhost"

TOKEN_NOTE="$([ -n "$TOKEN" ] && echo '(已帶入容器環境變數，不外洩給瀏覽器)' || echo '(空 — 後端純內網信任模式)')"
printf '\n\033[1;32m✅ 前端已上線\033[0m\n'
cat <<EOF

  本機：     http://localhost:${PORT}/
  區網/外網： http://${HOST_IP}:${PORT}/   （雲端 VM 記得到防火牆/安全群組放行 TCP ${PORT}）

  後端 domain： ${BASE_URL}
  讀取 token：  ${TOKEN_NOTE}

換後端 / 換 token：重跑這支腳本並帶上新的參數即可（會重建映像、重啟容器）。
停止前端：  ${DOCKER_SUDO:+sudo }docker rm -f ${CONTAINER}
看日誌：    ${DOCKER_SUDO:+sudo }docker logs -f ${CONTAINER}
EOF
