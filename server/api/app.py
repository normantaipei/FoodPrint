"""FoodPrint 自架 server — 店家接收 / 查詢服務（FastAPI）。

私有 DB 模式的後端：店家 metadata + 選配照片存在你網域內的硬碟 + PostgreSQL。

端點
────
  GET  /health                       健康檢查（免 token）
  GET  /stats                        庫狀態（places / tags 數）
  POST /places     (multipart)       收「一間店家 + metadata（+選配照片）」入庫（place_key 去重）
  PUT  /places/{id}/tags  (json)     改一間店的 tags（整批替換 / 只新增 / 只移除）
  GET  /places/{id}                  取一間店摘要（刪除前 dry-run 確認用）
  DELETE /places/{id}                刪一間店（連帶清 tags + 磁碟照片；需讀寫 token）
  GET  /search?q=&tag=&status=&limit=  tag + 關鍵字粗篩候選（語意排序交給 Claude）
  GET  /nearby?lat=&lng=&radius_km=    地理查詢：回傳座標附近的店（含距離），由近到遠
  GET  /thumbs/{name}                取店家照片縮圖（讀取 token 即可）
  GET  /images/{name}                取原圖（需讀寫 token）

讀取端點（/stats /search /nearby /thumbs）需『讀取』token（讀寫或唯讀皆可）。
所有「寫入 DB」端點（POST /places、PUT、DELETE）需讀寫 token 且僅限區網。
token 未設定則不檢查，僅建議在純內網時這樣。/search /stats /nearby 另有速率限制。
"""
from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import db

DATA_DIR = Path(os.environ.get("FOODPRINT_DATA", "/data"))
IMAGES_DIR = DATA_DIR / "images"
THUMBS_DIR = DATA_DIR / "thumbs"
THUMB_MAX = 1280
RW_TOKEN = os.environ.get("FOODPRINT_TOKEN", "").strip()
RO_TOKEN = os.environ.get("FOODPRINT_READ_TOKEN", "").strip()
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".tiff"}

app = FastAPI(title="FoodPrint 私有 DB", version="1.0")

# ── CORS ────────────────────────────────────────────────────────────
_cors_env = os.environ.get("FOODPRINT_CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_env in ("", "*") else [o.strip() for o in _cors_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET"],          # 前端（未來的地圖頁）只讀
    allow_headers=["*"],
)

# ── 速率限制（防有人寫腳本快速列舉整庫）─────────────────────────────
SEARCH_RATE = os.environ.get("FOODPRINT_SEARCH_RATE", "60/minute").strip() or "60/minute"


def _client_key(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    return xff.split(",")[0].strip() if xff else get_remote_address(request)


limiter = Limiter(key_func=_client_key)
app.state.limiter = limiter


def _ratelimit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "請求太頻繁，請稍後再試"})


app.add_exception_handler(RateLimitExceeded, _ratelimit_handler)


def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "不合法的請求"})


app.add_exception_handler(RequestValidationError, _validation_handler)


@app.on_event("startup")
def _startup() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    db.wait_and_init()


# ── 認證 ────────────────────────────────────────────────────────────
def _provided_token(authorization: str | None, t: str | None) -> str:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return (t or "").strip()


def require_write(authorization: str | None = Header(default=None), t: str | None = None) -> None:
    if not RW_TOKEN:
        return
    if _provided_token(authorization, t) != RW_TOKEN:
        raise HTTPException(status_code=401, detail="需要『讀寫』token")


def require_read(authorization: str | None = Header(default=None), t: str | None = None) -> None:
    if not RW_TOKEN and not RO_TOKEN:
        return
    tok = _provided_token(authorization, t)
    if tok and tok in {x for x in (RW_TOKEN, RO_TOKEN) if x}:
        return
    raise HTTPException(status_code=401, detail="需要『讀取』token（讀寫或唯讀皆可）")


# ── 區網限定（寫入端點用）────────────────────────────────────────────
def _peer_ip(request: Request):
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return getattr(ip, "ipv4_mapped", None) or ip


def require_lan(request: Request) -> None:
    ip = _peer_ip(request)
    if ip is None:
        raise HTTPException(status_code=403, detail="無法判定來源位址")
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return
    raise HTTPException(status_code=403, detail="這個端點僅限區網內存取")


def require_write_lan(
    request: Request,
    authorization: str | None = Header(default=None),
    t: str | None = None,
) -> None:
    """所有『寫入 DB』端點：必須來自區網（真實 TCP 對端 IP）且帶讀寫 token。"""
    require_lan(request)
    require_write(authorization, t)


# ── 縮圖 ────────────────────────────────────────────────────────────
def make_thumbnail(img_bytes: bytes, dst: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(img_bytes)) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            im.save(dst, "JPEG", quality=85)
        return True
    except Exception:
        return False


def _media_url(rel: str | None) -> str | None:
    return rel  # 前端 / 客戶端自己接 base_url；server 只回相對路徑


# ── 端點 ────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "foodprint-private-db"}


@app.get("/stats")
@limiter.limit(SEARCH_RATE)
def stats(request: Request, _: None = Depends(require_read)) -> dict:
    with db.pool().connection() as conn:
        n_places = conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        n_visited = conn.execute("SELECT COUNT(*) FROM places WHERE status='visited'").fetchone()[0]
        n_want = conn.execute("SELECT COUNT(*) FROM places WHERE status='want'").fetchone()[0]
        n_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        n_prop = conn.execute("SELECT COUNT(*) FROM tags WHERE status='proposed'").fetchone()[0]
    return {"places": n_places, "visited": n_visited, "want": n_want,
            "tags": n_tags, "proposed_tags": n_prop}


@app.post("/places")
async def upload_place(
    name: str = Form(...),
    description: str | None = Form(default=None),
    tags: str = Form(...),                        # JSON 陣列：[{"category","name"}, ...]
    address: str | None = Form(default=None),
    lat: float | None = Form(default=None),
    lng: float | None = Form(default=None),
    status: str = Form(default="want"),           # want / visited
    price_level: int | None = Form(default=None), # 1..4
    rating: int | None = Form(default=None),      # 1..5
    favorite: bool = Form(default=False),
    source: str | None = Form(default=None),
    file: UploadFile | None = File(default=None), # 選配店家照片
    _: None = Depends(require_write_lan),
) -> JSONResponse:
    """收一間店家 + metadata（+選配照片）入庫。place_key 去重、產縮圖、存原圖。"""
    if not name.strip():
        raise HTTPException(status_code=400, detail="name 不可空")
    try:
        tag_list = json.loads(tags)
        assert isinstance(tag_list, list)
    except Exception:
        raise HTTPException(status_code=400, detail="tags 必須是 JSON 陣列")
    if not tag_list:
        raise HTTPException(status_code=400, detail="至少要一個 tag")

    image_rel = thumb_rel = None
    if file is not None:
        raw = await file.read()
        if raw:
            content_hash = hashlib.sha256(raw).hexdigest()
            ext = Path(file.filename or "").suffix.lower()
            if ext not in IMAGE_EXTS:
                ext = ".jpg"
            img_dst = IMAGES_DIR / f"{content_hash}{ext}"
            if not img_dst.exists():
                img_dst.write_bytes(raw)
            image_rel = f"images/{content_hash}{ext}"
            thumb_dst = THUMBS_DIR / f"{content_hash}.jpg"
            if thumb_dst.exists() or make_thumbnail(raw, thumb_dst):
                thumb_rel = f"thumbs/{content_hash}.jpg"

    entry = {
        "name": name,
        "address": address,
        "lat": lat,
        "lng": lng,
        "description": description,
        "status": status,
        "price_level": price_level,
        "rating": rating,
        "favorite": favorite,
        "source": source,
        "tags": tag_list,
        "image_path": image_rel,
        "thumbnail_path": thumb_rel,
    }
    dims = db.known_categories()
    with db.pool().connection() as conn:
        place_id, state = db.upsert_place(conn, entry, dims)
        if state == "added":
            conn.execute(
                "INSERT INTO ingest_log(n_added, n_dup, summary) VALUES (%s,%s,%s)",
                (1, 0, f"place｜{name}"),
            )
    if state == "error":
        raise HTTPException(status_code=400, detail="入庫失敗：缺 name 或 tags")
    return JSONResponse({"id": place_id, "status": state, "name": name})


@app.put("/places/{place_id}/tags")
async def update_place_tags(
    place_id: int,
    body: dict = Body(...),
    _: None = Depends(require_write_lan),
) -> JSONResponse:
    """改一間店的 tags。body 為 JSON 物件：{"tags":[...]} 整批替換 /
    {"add":[...]} 只新增 / {"remove":[...]} 只移除（可混用）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body 必須是 JSON 物件")
    replace, add, remove = body.get("tags"), body.get("add"), body.get("remove")
    for key, val in (("tags", replace), ("add", add), ("remove", remove)):
        if val is not None and not isinstance(val, list):
            raise HTTPException(status_code=400, detail=f"{key} 必須是 JSON 陣列")
    if replace is None and add is None and remove is None:
        raise HTTPException(status_code=400, detail="需至少給 tags / add / remove 其一")
    dims = db.known_categories()
    with db.pool().connection() as conn:
        result = db.update_place_tags(conn, place_id, dims, replace=replace, add=add, remove=remove)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到這間店")
    return JSONResponse({"id": place_id, **result})


@app.get("/places/{place_id}")
def get_place(place_id: int, _: None = Depends(require_read)) -> JSONResponse:
    with db.pool().connection() as conn:
        place = db.get_place(conn, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="找不到這間店")
    return JSONResponse(place)


@app.delete("/places/{place_id}")
def delete_place(place_id: int, _: None = Depends(require_write_lan)) -> JSONResponse:
    with db.pool().connection() as conn:
        info = db.delete_place(conn, place_id)
    if info is None:
        raise HTTPException(status_code=404, detail="找不到這間店")
    for rel in (info.get("image_path"), info.get("thumbnail_path")):
        if not rel:
            continue
        try:
            (DATA_DIR / rel).unlink(missing_ok=True)
        except OSError:
            pass
    return JSONResponse({"id": info["id"], "name": info["name"], "deleted": True})


def _row_to_place(conn, row) -> dict:
    pid, name, address, lat, lng, desc, st, price, rating, fav, src, img, thumb = row
    return {
        "id": pid, "name": name, "address": address, "lat": lat, "lng": lng,
        "description": desc, "status": st, "price_level": price, "rating": rating,
        "favorite": bool(fav), "source": src, "image_path": img, "thumbnail_path": thumb,
        "tags": [f"{c}:{n}" for c, n in conn.execute(
            "SELECT t.category, t.name FROM place_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.place_id=%s ORDER BY t.category", (pid,)).fetchall()],
    }


_PLACE_COLS = ("p.id, p.name, p.address, p.lat, p.lng, p.description, p.status, "
               "p.price_level, p.rating, p.favorite, p.source, p.image_path, p.thumbnail_path")


@app.get("/search")
@limiter.limit(SEARCH_RATE)
def search(request: Request, q: str = "", tag: list[str] | None = None,
           status: str | None = None, limit: int = 20, offset: int = 0,
           _: None = Depends(require_read)) -> JSONResponse:
    """tag（category=name，可多個 AND）+ 關鍵字（對 店名/地址/描述/tag AND LIKE）粗篩候選。
    status 可選 want / visited 篩選。語意排序由 Claude 讀描述完成。"""
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    tag = tag or []
    tag_pairs = [(c.strip(), n.strip()) for kv in tag if "=" in kv for c, n in [kv.split("=", 1)]]

    clauses: list[str] = []
    params: list = []
    if status in ("want", "visited"):
        clauses.append("p.status = %s")
        params.append(status)
    if tag_pairs:
        ors = " OR ".join(["(t.category=%s AND t.name=%s)"] * len(tag_pairs))
        sub_params = [x for pair in tag_pairs for x in pair]
        clauses.append(
            f"p.id IN (SELECT p2.id FROM places p2 "
            f"JOIN place_tags pt ON pt.place_id=p2.id JOIN tags t ON t.id=pt.tag_id "
            f"WHERE {ors} GROUP BY p2.id "
            f"HAVING COUNT(DISTINCT t.category||':'||t.name) = %s)"
        )
        params += sub_params + [len(tag_pairs)]
    for kw in q.split():
        like = f"%{kw}%"
        clauses.append(
            "(p.name ILIKE %s OR p.address ILIKE %s OR p.description ILIKE %s "
            "OR p.id IN (SELECT pt.place_id FROM place_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE t.name ILIKE %s OR t.category ILIKE %s))"
        )
        params += [like, like, like, like, like]

    sql = f"SELECT {_PLACE_COLS} FROM places p"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += (" ORDER BY p.favorite DESC, p.rating IS NULL, p.rating DESC, p.created_at DESC "
            "LIMIT %s OFFSET %s")
    params += [limit, offset]

    with db.pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        out = [_row_to_place(conn, r) for r in rows]
    return JSONResponse(out)


@app.get("/nearby")
@limiter.limit(SEARCH_RATE)
def nearby(request: Request, lat: float, lng: float, radius_km: float = 3.0,
           tag: list[str] | None = None, status: str | None = None, limit: int = 30,
           _: None = Depends(require_read)) -> JSONResponse:
    """地理查詢：回傳 (lat,lng) 半徑 radius_km 內的店，由近到遠，每筆帶 distance_km。
    先用經緯度 bounding box 粗篩（吃 lat/lng 索引），再用 haversine 精算距離 + 排序。"""
    limit = max(1, min(limit, 100))
    radius_km = max(0.05, min(radius_km, 50.0))
    tag = tag or []
    tag_pairs = [(c.strip(), n.strip()) for kv in tag if "=" in kv for c, n in [kv.split("=", 1)]]

    # bounding box：緯度 1 度 ≈ 111km；經度隨緯度收斂（乘 cos）。
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 1e-6))

    clauses = ["p.lat IS NOT NULL AND p.lng IS NOT NULL",
               "p.lat BETWEEN %s AND %s", "p.lng BETWEEN %s AND %s"]
    params: list = [lat - dlat, lat + dlat, lng - dlng, lng + dlng]
    if status in ("want", "visited"):
        clauses.append("p.status = %s")
        params.append(status)
    if tag_pairs:
        ors = " OR ".join(["(t.category=%s AND t.name=%s)"] * len(tag_pairs))
        sub_params = [x for pair in tag_pairs for x in pair]
        clauses.append(
            f"p.id IN (SELECT p2.id FROM places p2 "
            f"JOIN place_tags pt ON pt.place_id=p2.id JOIN tags t ON t.id=pt.tag_id "
            f"WHERE {ors} GROUP BY p2.id "
            f"HAVING COUNT(DISTINCT t.category||':'||t.name) = %s)"
        )
        params += sub_params + [len(tag_pairs)]

    sql = f"SELECT {_PLACE_COLS} FROM places p WHERE " + " AND ".join(clauses)
    with db.pool().connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            place = _row_to_place(conn, r)
            d = _haversine_km(lat, lng, place["lat"], place["lng"])
            if d <= radius_km:
                place["distance_km"] = round(d, 3)
                out.append(place)
    out.sort(key=lambda p: p["distance_km"])
    return JSONResponse(out[:limit])


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@app.get("/thumbs/{name}")
def get_thumb(name: str, _: None = Depends(require_read)):
    path = (THUMBS_DIR / name).resolve()
    if THUMBS_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="找不到縮圖")
    return FileResponse(path)


@app.get("/images/{name}")
def get_image(name: str, _: None = Depends(require_write)):
    path = (IMAGES_DIR / name).resolve()
    if IMAGES_DIR.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail="找不到原圖")
    return FileResponse(path)
