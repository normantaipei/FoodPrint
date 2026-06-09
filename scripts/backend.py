#!/usr/bin/env python3
"""FoodPrint — 私有 DB（自架）客戶端。

FoodPrint 的庫存在你網域內自架的一台機器（server/docker-compose.yml：
PostgreSQL + 店家/照片接收服務）。這支腳本是 Claude 端的客戶端：把店家
metadata（+選配照片）POST 進去、搜尋、依座標找附近、刪除。

只用 Python 標準庫（urllib），不需要任何 pip 安裝。

設定
────
  python3 scripts/backend.py status                         # 看後端與連線狀態
  python3 scripts/backend.py set --base-url http://192.168.1.50:8000 --token <token>

入庫 / 查詢
──────────
  python3 scripts/backend.py ingest --manifest /tmp/foodprint/_ingest.json
      # 把 manifest 裡每間店（+選配照片）POST 到 /places（place_key 去重）。
  python3 scripts/backend.py place --name "鼎泰豐 信義店" --address "台北市信義區..." \
      --lat 25.033 --lng 121.564 --tag cuisine=中式 --tag dish=小籠包 \
      --status visited --rating 5 --description "小籠包必點"
  python3 scripts/backend.py search "火鍋 約會" --tag district=大安區 --table
  python3 scripts/backend.py nearby --lat 25.033 --lng 121.564 --radius 1.5 --table
  python3 scripts/backend.py delete --ids 12,15 --confirm
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FOODPRINT_DATA", str(ROOT / "data")))
CONFIG_PATH = Path(os.environ.get("FOODPRINT_CONFIG", str(DATA_DIR / "config.json")))


# ── 設定檔 ──────────────────────────────────────────────────────────
def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已寫入設定：{CONFIG_PATH}")


def require_selfhost(cfg: dict | None) -> dict:
    if not cfg or cfg.get("backend") != "selfhost":
        raise SystemExit(
            "尚未設定後端。先指向你的自架 server：\n"
            "  python3 scripts/backend.py set --base-url http://<內網IP>:8000 --token <token>"
        )
    sh = cfg.get("selfhost") or {}
    if not sh.get("base_url"):
        raise SystemExit("設定缺 selfhost.base_url，請重新 set。")
    return sh


# ── HTTP（標準庫；含 multipart 編碼）────────────────────────────────
def _auth_headers(sh: dict) -> dict:
    token = (sh.get("token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _multipart_body(fields: dict, files: list[tuple[str, str, bytes]]) -> tuple[bytes, str]:
    boundary = f"----FoodPrint{uuid.uuid4().hex}"
    crlf = b"\r\n"
    out = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        out += b"--" + boundary.encode() + crlf
        out += f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
        out += str(value).encode("utf-8") + crlf
    for name, filename, content in files:
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        out += b"--" + boundary.encode() + crlf
        out += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode() + crlf
        out += f"Content-Type: {ctype}".encode() + crlf + crlf
        out += content + crlf
    out += b"--" + boundary.encode() + b"--" + crlf
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _request(method: str, url: str, *, headers: dict, data: bytes | None = None,
             content_type: str | None = None, timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"連不到 server（{url}）：{e.reason}")


def _get_json(sh: dict, path: str, params: list[tuple[str, str]] | None = None) -> object:
    url = sh["base_url"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    status, body = _request("GET", url, headers=_auth_headers(sh))
    if status >= 400:
        raise SystemExit(f"GET {path} 失敗（{status}）：{body.decode(errors='ignore')[:300]}")
    return json.loads(body.decode("utf-8"))


def _post_multipart(sh: dict, path: str, fields: dict, files: list, timeout: int = 120) -> tuple[int, object]:
    url = sh["base_url"].rstrip("/") + path
    body, ctype = _multipart_body(fields, files)
    status, raw = _request("POST", url, headers=_auth_headers(sh), data=body,
                           content_type=ctype, timeout=timeout)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = raw.decode(errors="ignore")
    return status, parsed


# ── 子指令 ──────────────────────────────────────────────────────────
def cmd_status(args) -> None:
    cfg = load_config()
    if not cfg:
        print("尚未設定後端。先指向你的自架 server：")
        print("  python3 scripts/backend.py set --base-url http://<內網IP>:8000 --token <token>")
        print("（還沒架 server？見 server/README.md，docker compose up 即可。）")
        return
    sh = cfg.get("selfhost") or {}
    print(f"後端：selfhost（私有 DB）")
    print(f"  base_url：{sh.get('base_url')}")
    print(f"  token   ：{'已設' if sh.get('token') else '（無，純內網信任）'}")
    try:
        health = _get_json(sh, "/health")
        stats = _get_json(sh, "/stats")
        print(f"  連線    ：OK（{health}）")
        print(f"  庫狀態  ：places={stats.get('places')}（吃過 {stats.get('visited')} / "
              f"想去 {stats.get('want')}）tags={stats.get('tags')} proposed={stats.get('proposed_tags')}")
    except SystemExit as e:
        print(f"  連線    ：✗ {e}")


def cmd_set(args) -> None:
    if not args.base_url:
        raise SystemExit("需要 --base-url，例如 http://192.168.1.50:8000")
    cfg = {"backend": "selfhost",
           "selfhost": {"base_url": args.base_url.rstrip("/"), "token": args.token or ""}}
    save_config(cfg)
    try:
        health = _get_json(cfg["selfhost"], "/health")
        print(f"連線測試 OK：{health}")
    except SystemExit as e:
        print(f"⚠ 設定已存，但連線測試失敗：{e}")


def _parse_tags(kvs: list | None) -> list[dict]:
    tags = []
    for kv in kvs or []:
        if "=" not in kv:
            raise SystemExit(f"--tag 格式要 category=name，收到：{kv}")
        c, n = kv.split("=", 1)
        tags.append({"category": c.strip(), "name": n.strip()})
    return tags


def _place_fields(entry: dict) -> dict:
    """把一個 place dict 轉成 multipart 欄位（tags 轉 JSON 字串，數值轉 str）。"""
    fields = {"name": entry["name"], "tags": json.dumps(entry.get("tags") or [], ensure_ascii=False)}
    for k in ("address", "description", "source"):
        if entry.get(k):
            fields[k] = entry[k]
    for k in ("lat", "lng", "price_level", "rating"):
        if entry.get(k) is not None:
            fields[k] = str(entry[k])
    fields["status"] = entry.get("status") or "want"
    fields["favorite"] = str(bool(entry.get("favorite"))).lower()
    return fields


def _post_place(sh: dict, entry: dict, base: Path | None = None) -> tuple[int, object]:
    files = []
    img = entry.get("image")
    if img:
        src = Path(img).expanduser()
        if not src.is_absolute() and base is not None:
            src = base / src
        if src.exists():
            files.append(("file", src.name, src.read_bytes()))
        else:
            print(f"  ⚠ 找不到照片，略過照片只存 metadata：{img}")
    return _post_multipart(sh, "/places", _place_fields(entry), files)


def cmd_place(args) -> None:
    """單間店直接 POST（手動 / 快速）。"""
    sh = require_selfhost(load_config())
    entry = {
        "name": args.name, "address": args.address, "description": args.description,
        "lat": args.lat, "lng": args.lng, "status": args.status,
        "price_level": args.price, "rating": args.rating, "favorite": args.favorite,
        "source": args.source, "tags": _parse_tags(args.tag), "image": args.image,
    }
    if not entry["tags"]:
        raise SystemExit("至少要一個 --tag（如 cuisine=火鍋）。")
    status, parsed = _post_place(sh, entry)
    if status >= 400:
        raise SystemExit(f"入庫失敗（{status}）：{parsed}")
    state = parsed.get("status") if isinstance(parsed, dict) else None
    if state == "dup":
        print(f"↻ 已存在（place_key 去重）：{args.name} → #{parsed.get('id')}")
    else:
        print(f"＋ 已入庫：{args.name} → #{parsed.get('id')}")


def cmd_ingest(args) -> None:
    """批次：manifest 裡每間店 POST /places。manifest 是 JSON 陣列，每筆例：
       {"name","address","lat","lng","description","status","rating","favorite",
        "source","tags":[{"category","name"}],"image":"<相對 manifest 的照片路徑，選填>"}"""
    sh = require_selfhost(load_config())
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.exists():
        raise SystemExit(f"找不到 manifest：{manifest_path}")
    base = manifest_path.parent
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("places") or entries.get("items") or [entries]
    if not isinstance(entries, list):
        raise SystemExit("manifest 應是 JSON 陣列。")

    added = dup = errors = 0
    for e in entries:
        name = (e.get("name") or "").strip()
        tags = e.get("tags") or []
        if not name or not tags:
            print(f"  ✗ 略過（缺 name 或 tags）：{e.get('name')!r}"); errors += 1; continue
        status, parsed = _post_place(sh, e, base)
        if status >= 400:
            print(f"  ✗ {name} 失敗（{status}）：{parsed}"); errors += 1; continue
        state = parsed.get("status") if isinstance(parsed, dict) else None
        if state == "added":
            added += 1; print(f"  ＋ {name} → #{parsed.get('id')}")
        elif state == "dup":
            dup += 1; print(f"  ↻ 已存在：{name}")
        else:
            errors += 1; print(f"  ✗ {name}：{parsed}")
    print(f"\n入庫完成：+{added} 間，重複 {dup}，失敗 {errors}。→ {sh['base_url']}")


def _parse_ids(chunks: list | None) -> list[int]:
    ids: list[int] = []
    for chunk in chunks or []:
        for tok in chunk.replace("，", ",").split(","):
            tok = tok.strip().lstrip("#")
            if not tok:
                continue
            if not tok.isdigit():
                raise SystemExit(f"--ids 只能是數字 id（逗號分隔），收到：{tok}")
            ids.append(int(tok))
    return ids


def cmd_delete(args) -> None:
    """刪一或多間店。預設 dry-run（只列出），--confirm 才真的刪。"""
    sh = require_selfhost(load_config())
    ids = _parse_ids(args.ids)
    if not ids:
        raise SystemExit("請用 --ids 指定要刪哪幾間（搜尋表上的 #，逗號分隔）。")
    base = sh["base_url"].rstrip("/")
    targets = []
    for pid in ids:
        status, raw = _request("GET", f"{base}/places/{pid}", headers=_auth_headers(sh))
        if status == 404:
            print(f"  ⚠ 找不到 #{pid}，略過"); continue
        if status >= 400:
            raise SystemExit(f"查 #{pid} 失敗（{status}）：{raw.decode(errors='ignore')[:200]}")
        try:
            targets.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"  ⚠ #{pid} 回傳非預期，略過")
    if not targets:
        raise SystemExit("沒有可刪的目標。")
    print(f"\n將刪除 {len(targets)} 間（私有 DB：metadata + 照片永久移除）：")
    for t in targets:
        print(f"  • #{t['id']}  {t.get('name')}  {(t.get('address') or '')[:30]}")
    if not args.confirm:
        print("\n— 這是預覽（dry-run），還沒刪任何東西。確認後加 --confirm 重跑。")
        return
    deleted = errors = 0
    for t in targets:
        status, raw = _request("DELETE", f"{base}/places/{t['id']}", headers=_auth_headers(sh))
        if status >= 400:
            errors += 1; print(f"  ✗ 刪 #{t['id']} 失敗（{status}）")
        else:
            deleted += 1; print(f"  ✓ 已刪 #{t['id']}（{t.get('name')}）")
    print(f"\n刪除完成：成功 {deleted}，失敗 {errors}。")


def _md_cell(text) -> str:
    return (str(text) if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _price_str(level) -> str:
    return "$" * level if isinstance(level, int) and 1 <= level <= 4 else "—"


def _print_places(results: list, base: str, token: str, table: bool, show_dist: bool) -> None:
    def media(rel):
        if not rel:
            return None
        url = f"{base}/{rel}"
        return url + ("?t=" + urllib.parse.quote(token) if token else "")

    if not results:
        print("（沒有符合的店家）")
        return
    if table:
        print(f"找到 **{len(results)}** 間店家：\n")
        head = "| # | 店名 | 地址 | 標籤 | 價位 | 訊號 |"
        sep = "|---|---|---|---|---|---|"
        if show_dist:
            head = "| # | 店名 | 距離 | 地址 | 標籤 | 價位 | 訊號 |"
            sep = "|---|---|---|---|---|---|---|"
        print(head); print(sep)
        for r in results:
            tag_vals = "、".join(t.split(":", 1)[-1] for t in r.get("tags", []))
            signals = []
            if r.get("favorite"):
                signals.append("★")
            if r.get("rating"):
                signals.append(f"{r['rating']}/5")
            signals.append("吃過" if r.get("status") == "visited" else "想去")
            cells = [str(r["id"]), _md_cell(r.get("name")), _md_cell(r.get("address")) or "—",
                     _md_cell(tag_vals) or "—", _price_str(r.get("price_level")),
                     _md_cell(" ".join(signals))]
            if show_dist:
                cells.insert(2, f"{r.get('distance_km')}km")
            print("| " + " | ".join(cells) + " |")
        print("\n> 以上是粗篩候選；請依使用者描述讀心得做語意挑選。")
    else:
        for r in results:
            head = f"#{r['id']}  {r.get('name')}"
            if show_dist:
                head += f"  ({r.get('distance_km')}km)"
            if r.get("favorite"):
                head += "  ★"
            if r.get("rating"):
                head += f"  {r['rating']}/5"
            head += "  [吃過]" if r.get("status") == "visited" else "  [想去]"
            print(head)
            if r.get("address"):
                print(f"  地址: {r['address']}")
            if r.get("description"):
                print(f"  {r['description']}")
            print(f"  tags: {'、'.join(r.get('tags', [])) or '（無）'}")
            if r.get("source"):
                print(f"  來源: {r['source']}")
            print()


def cmd_search(args) -> None:
    sh = require_selfhost(load_config())
    params: list[tuple[str, str]] = []
    if args.query:
        params.append(("q", args.query))
    for kv in args.tag or []:
        params.append(("tag", kv))
    if args.status:
        params.append(("status", args.status))
    params.append(("limit", str(args.limit)))
    results = _get_json(sh, "/search", params)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2)); return
    _print_places(results, sh["base_url"].rstrip("/"), (sh.get("token") or "").strip(),
                  args.table, show_dist=False)


def cmd_nearby(args) -> None:
    sh = require_selfhost(load_config())
    params = [("lat", str(args.lat)), ("lng", str(args.lng)),
              ("radius_km", str(args.radius)), ("limit", str(args.limit))]
    for kv in args.tag or []:
        params.append(("tag", kv))
    if args.status:
        params.append(("status", args.status))
    results = _get_json(sh, "/nearby", params)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2)); return
    print(f"（{args.lat},{args.lng} 半徑 {args.radius}km 內）")
    _print_places(results, sh["base_url"].rstrip("/"), (sh.get("token") or "").strip(),
                  args.table, show_dist=True)


def main() -> None:
    p = argparse.ArgumentParser(description="FoodPrint 私有 DB 客戶端")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="看後端與連線狀態").set_defaults(func=cmd_status)

    ps = sub.add_parser("set", help="設定自架 server 位址與 token")
    ps.add_argument("--base-url", required=True, help="server 位址，如 http://192.168.1.50:8000")
    ps.add_argument("--token", help="讀寫 token（入庫用）；只搜尋的人用唯讀 token")
    ps.set_defaults(func=cmd_set)

    pp = sub.add_parser("place", help="單間店直接 POST /places")
    pp.add_argument("--name", required=True)
    pp.add_argument("--address")
    pp.add_argument("--lat", type=float)
    pp.add_argument("--lng", type=float)
    pp.add_argument("--description")
    pp.add_argument("--tag", action="append", help="category=name，可重複")
    pp.add_argument("--status", choices=["want", "visited"], default="want")
    pp.add_argument("--price", type=int, choices=[1, 2, 3, 4], help="價位 1..4（$ ~ $$$$）")
    pp.add_argument("--rating", type=int, help="個人評分 1..5")
    pp.add_argument("--favorite", action="store_true")
    pp.add_argument("--source")
    pp.add_argument("--image", help="選配店家照片路徑")
    pp.set_defaults(func=cmd_place)

    pin = sub.add_parser("ingest", help="批次：manifest 裡每間店 POST /places")
    pin.add_argument("--manifest", required=True)
    pin.set_defaults(func=cmd_ingest)

    psr = sub.add_parser("search", help="tag + 關鍵字粗篩候選")
    psr.add_argument("query", nargs="?", default="")
    psr.add_argument("--tag", action="append", help="category=name，可重複（AND）")
    psr.add_argument("--status", choices=["want", "visited"])
    psr.add_argument("--limit", type=int, default=20)
    psr.add_argument("--json", action="store_true")
    psr.add_argument("--table", action="store_true", help="印 Markdown 表")
    psr.set_defaults(func=cmd_search)

    pn = sub.add_parser("nearby", help="座標附近找店（由近到遠）")
    pn.add_argument("--lat", type=float, required=True)
    pn.add_argument("--lng", type=float, required=True)
    pn.add_argument("--radius", type=float, default=3.0, help="半徑 km（預設 3）")
    pn.add_argument("--tag", action="append", help="category=name，可重複（AND）")
    pn.add_argument("--status", choices=["want", "visited"])
    pn.add_argument("--limit", type=int, default=30)
    pn.add_argument("--json", action="store_true")
    pn.add_argument("--table", action="store_true")
    pn.set_defaults(func=cmd_nearby)

    pd = sub.add_parser("delete", help="刪一或多間店（預設 dry-run，--confirm 才刪）")
    pd.add_argument("--ids", action="append", required=True, help="店家 id，逗號分隔，可重複")
    pd.add_argument("--confirm", action="store_true")
    pd.set_defaults(func=cmd_delete)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
