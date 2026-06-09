#!/usr/bin/env python3
"""FoodPrint — 從 Google Maps / 社群連結擷取店家資訊（免 API key）。

把一個網址丟進來，盡力抽出 {name, address, lat, lng, raw_title, raw_description}，
輸出 JSON 給 Claude 進一步整理成入庫 manifest。只用標準庫，免任何 API key。

支援
────
  • Google Maps 完整連結：.../maps/place/<店名>/@<lat>,<lng>,<zoom>...
       店名取自 /place/ 路徑、座標取自 @ 或 !3d<lat>!4d<lng>。
  • Google Maps 短連結：https://maps.app.goo.gl/xxxx 或 https://goo.gl/maps/xxxx
       會自動跟隨轉址還原成完整連結再解析。
  • 一般網頁（IG / 部落格 / 餐廳官網）：抓 <title> 與 og:title / og:description / og:image。

⚠ 誠實的限制：Google 不提供免登入的結構化地址；地址多半要由 Claude 從 raw_title /
   og:description 推斷，或請使用者補。座標若 URL 內沒有就會是 null。

用法
────
  python3 scripts/fetch_place.py "https://maps.app.goo.gl/xxxx"
  python3 scripts/fetch_place.py "<url>" --pretty
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _fetch(url: str, timeout: int = 20) -> tuple[str, str]:
    """GET 一個 URL，回 (最終網址, HTML 文字)。跟隨轉址（短連結還原）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh,en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
            return final_url, body
    except urllib.error.HTTPError as e:
        # 有些頁面回 4xx 仍帶可用 HTML
        try:
            return url, e.read().decode("utf-8", errors="replace")
        except Exception:
            raise SystemExit(f"抓取失敗（{e.code}）：{url}")
    except urllib.error.URLError as e:
        raise SystemExit(f"連不到（{e.reason}）：{url}")


def _resolve_redirect(url: str) -> str:
    """短連結（maps.app.goo.gl / goo.gl）→ 跟隨轉址取得完整 Maps 連結。"""
    if not re.search(r"(maps\.app\.goo\.gl|goo\.gl/maps)", url):
        return url
    final_url, _ = _fetch(url)
    return final_url


def _coords_from_url(url: str) -> tuple[float | None, float | None]:
    """從 Maps URL 抽座標：先試 !3d<lat>!4d<lng>（地點本身），再退回 @<lat>,<lng>（地圖中心）。"""
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def _name_from_maps_url(url: str) -> str | None:
    m = re.search(r"/maps/place/([^/@]+)", url)
    if not m:
        return None
    name = urllib.parse.unquote(m.group(1)).replace("+", " ").strip()
    return name or None


def _meta(html_text: str, prop: str) -> str | None:
    """抓 <meta property/name="prop" content="...">（順序不拘）。"""
    for pat in (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(prop)}["\']',
    ):
        m = re.search(pat, html_text, re.IGNORECASE)
        if m:
            return html.unescape(m.group(1)).strip() or None
    return None


def _title(html_text: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    return html.unescape(m.group(1)).strip() if m else None


def extract(url: str) -> dict:
    url = _resolve_redirect(url.strip())
    is_maps = "google." in url and "/maps" in url
    out: dict = {"source": url, "name": None, "address": None,
                 "lat": None, "lng": None, "raw_title": None, "raw_description": None}

    if is_maps:
        out["name"] = _name_from_maps_url(url)
        out["lat"], out["lng"] = _coords_from_url(url)

    try:
        final_url, body = _fetch(url)
    except SystemExit:
        body = ""
        final_url = url
    if body:
        out["raw_title"] = _meta(body, "og:title") or _title(body)
        out["raw_description"] = _meta(body, "og:description")
        out["image"] = _meta(body, "og:image")
        if out["name"] is None and out["raw_title"]:
            # 非 Maps 頁（IG / 部落格）：店名先用 og:title，交給 Claude 收斂。
            out["name"] = out["raw_title"]
        if (out["lat"] is None or out["lng"] is None):
            lat, lng = _coords_from_url(final_url)
            if lat is not None:
                out["lat"], out["lng"] = lat, lng

    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="從 Google Maps / 社群連結擷取店家（免 API key）")
    ap.add_argument("url", help="Google Maps / IG / 部落格 / 官網 連結")
    ap.add_argument("--pretty", action="store_true", help="縮排輸出")
    args = ap.parse_args()
    data = extract(args.url)
    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None))
    if not data.get("name"):
        print("⚠ 抽不到店名——請 Claude 依 raw_title / 使用者描述補上。", file=sys.stderr)


if __name__ == "__main__":
    main()
