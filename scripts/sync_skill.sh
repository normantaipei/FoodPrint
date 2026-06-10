#!/usr/bin/env bash
#
# 把專案內的 skill 檔案同步到 Claude 載入位置（~/.claude/skills/foodprint），
# 但「永遠保留」目的地的 data/config.json —— 那支裝著後端位址 + 讀寫 token，
# 覆蓋過去會斷線。改完 SKILL.md / scripts 後跑這支即可生效（重啟對話後重新載入）。
#
# 用法：
#   bash scripts/sync_skill.sh            # 同步（保留 config.json）
#   DST=/path/to/skills/foodprint bash scripts/sync_skill.sh   # 指定目的地
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="${DST:-$HOME/.claude/skills/foodprint}"

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()  { printf "\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# 與 build_skill_zip.sh 一致的「skill 正式檔案集」（不含 data/config.json）。
FILES=(
  "SKILL.md"
  "taxonomy.yaml"
  "scripts/backend.py"
  "scripts/fetch_place.py"
)

printf "🍜 同步 FoodPrint skill\n  來源：%s\n  目的：%s\n\n" "$ROOT" "$DST"

[ -d "$DST" ] || die "目的地不存在：$DST（請先安裝過 skill 一次）。"

# 安全檢查：目的地若是 symlink，代表已是「直接指向專案」，不需也不該再複製。
if [ -L "$DST" ]; then
  warn "目的地是 symlink（已直接連到專案），無需同步。target=$(readlink "$DST")"
  exit 0
fi

# 保護 config.json：同步前後皆不碰它。
CFG="$DST/data/config.json"
if [ -f "$CFG" ]; then
  CFG_BEFORE="$(shasum "$CFG" 2>/dev/null | awk '{print $1}' || true)"
  ok "偵測到既有連線設定 data/config.json（會原封保留）"
else
  warn "目的地沒有 data/config.json —— 同步後記得用 backend.py set 設定後端，否則無法連線。"
  CFG_BEFORE=""
fi

changed=0
for rel in "${FILES[@]}"; do
  src="$ROOT/$rel"
  dst="$DST/$rel"
  [ -f "$src" ] || die "找不到要同步的檔：$src"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && cmp -s "$src" "$dst"; then
    printf "    = %s（無變化）\n" "$rel"
  else
    cp "$src" "$dst"
    ok "更新 $rel"
    changed=$((changed + 1))
  fi
done

# 驗證 config.json 未被動到。
if [ -n "$CFG_BEFORE" ]; then
  CFG_AFTER="$(shasum "$CFG" 2>/dev/null | awk '{print $1}' || true)"
  [ "$CFG_BEFORE" = "$CFG_AFTER" ] || die "data/config.json 內容被改動了（不該發生）！請檢查。"
  ok "data/config.json 已確認保持不變"
fi

echo
if [ "$changed" -gt 0 ]; then
  ok "同步完成：更新 $changed 個檔。重啟對話後 Claude 會載入新版 SKILL.md。"
else
  ok "已是最新，無需更新。"
fi
