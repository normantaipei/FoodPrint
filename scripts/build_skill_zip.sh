#!/usr/bin/env bash
# 把 FoodPrint 打包成可上傳 / 下載的 skill .zip（SKILL.md 在 zip 根目錄）。
#
# 會把後端「位址 + 讀寫 token」烤進 data/config.json，讓拿到 zip 的人一裝就是
# 「私有 DB 已連線」狀態，不必再手動 set。
#
# 用法：
#   FOODPRINT_BASE_URL=http://192.168.1.50:8002 FOODPRINT_TOKEN=<讀寫token> \
#       bash scripts/build_skill_zip.sh
#
# 沒帶環境變數時：token 退而從 server/.env 讀；base_url 仍必須給（需含對外可達的 IP）。
# 產出：dist/foodprint-skill.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/foodprint-skill.zip"
mkdir -p "$ROOT/dist"
rm -f "$OUT"

# token 預設從 server/.env 取（install.sh 會直接用環境變數覆寫）
if [ -z "${FOODPRINT_TOKEN:-}" ] && [ -f "$ROOT/server/.env" ]; then
  FOODPRINT_TOKEN="$(grep -E '^FOODPRINT_TOKEN=' "$ROOT/server/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' \r' || true)"
fi
BASE_URL="${FOODPRINT_BASE_URL:-}"
[ -n "$BASE_URL" ] || { echo "✗ 缺 FOODPRINT_BASE_URL（需含對外可達的 IP，如 http://192.168.1.50:8002）" >&2; exit 1; }

# 用 python 的 zipfile 打包（不依賴外部 zip 指令；乾淨 VM 常沒裝）。
# 烤入的 config.json 放在 zip 內 data/config.json：backend.py 以 <skill根>/data/config.json 為設定檔。
BASE_URL="$BASE_URL" FOODPRINT_TOKEN="${FOODPRINT_TOKEN:-}" OUT="$OUT" ROOT="$ROOT" python3 - <<'PY'
import json, os, pathlib, zipfile, sys

root = pathlib.Path(os.environ["ROOT"])
out  = os.environ["OUT"]
cfg = {
    "backend": "selfhost",
    "selfhost": {
        "base_url": os.environ["BASE_URL"].rstrip("/"),
        "token": os.environ.get("FOODPRINT_TOKEN", ""),
    },
}
config_json = json.dumps(cfg, ensure_ascii=False, indent=2)

# zip 內路徑 -> 來源（None 表示 inline 內容）
members = {
    "SKILL.md":               root / "SKILL.md",
    "taxonomy.yaml":          root / "taxonomy.yaml",
    "scripts/backend.py":     root / "scripts" / "backend.py",
    "scripts/fetch_place.py": root / "scripts" / "fetch_place.py",
}
for arc, src in members.items():
    if not src.is_file():
        sys.exit(f"✗ 找不到要打包的檔：{src}")

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("data/config.json", config_json)   # 烤入連線設定
    for arc, src in members.items():
        z.write(src, arc)

print("🔐 已把後端位址 + 讀寫 token 烤入 data/config.json")
print("   zip 內容：data/config.json, " + ", ".join(members.keys()))
PY

echo "✅ 打包完成：${OUT#$ROOT/}"
