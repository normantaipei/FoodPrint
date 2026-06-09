"""FoodPrint 自架 server — Postgres 連線層 + 入庫核心。

沿用 PosePlanner 的 tag 解析 / upsert 設計，把實體換成 place（店家），
拿掉向量與創作者，改用 place_key 去重並支援地理欄位（lat / lng）。
連線用 psycopg3 連線池；啟動時冪等套 schema、seed taxonomy。
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = Path(os.environ.get("SCHEMA_PATH", HERE / "schema.sql"))
TAXONOMY_PATH = Path(os.environ.get("TAXONOMY_PATH", "/app/taxonomy.yaml"))

_pool: ConnectionPool | None = None


def dsn(dbname: str | None = None) -> str:
    """從環境變數組 Postgres DSN。docker-compose 會帶這些進來。
    dbname 可覆寫目標資料庫（補建 DB 時要連到一定存在的維護 DB postgres）。"""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    host = os.environ.get("PGHOST", "db")
    port = os.environ.get("PGPORT", "5432")
    user = os.environ.get("POSTGRES_USER", "foodprint")
    pwd = os.environ.get("POSTGRES_PASSWORD", "foodprint")
    name = dbname or os.environ.get("POSTGRES_DB", "foodprint")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}"


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(dsn(), min_size=1, max_size=10, kwargs={"autocommit": True})
    return _pool


def _ensure_database(connect_timeout: int = 3) -> None:
    """目標資料庫不存在就補建，讓系統自我修復（舊 / 半初始化 volume 的常見坑）。
    DATABASE_URL 模式（多半是受管 PG）不處理，交給該服務自己管理。"""
    if os.environ.get("DATABASE_URL"):
        return
    name = os.environ.get("POSTGRES_DB", "foodprint")
    with psycopg.connect(dsn("postgres"), connect_timeout=connect_timeout, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            # 識別字不能參數化綁定，name 取自我們自己的環境變數，非外部輸入。
            conn.execute(f'CREATE DATABASE "{name}"')


def _probe(connect_timeout: int = 3) -> None:
    """用短逾時的直接連線確認 Postgres 真的可連、可認證（含密碼）。"""
    with psycopg.connect(dsn(), connect_timeout=connect_timeout, autocommit=True) as conn:
        conn.execute("SELECT 1")


def wait_and_init(retries: int = 60, delay: float = 2.0) -> None:
    """等 Postgres 起來 → 套 schema → seed taxonomy。啟動時呼叫，冪等可重跑。
    整段包在重試裡：Postgres 首次初始化會先起暫時 server 再重啟，期間連線可能斷掉。"""
    last_err: Exception | None = None
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    for _ in range(retries):
        try:
            _ensure_database()
            _probe()
            with pool().connection() as conn:
                conn.execute(schema_sql)
            seed_taxonomy()
            return
        except Exception as e:  # DB 還沒好 / 首次初始化重啟中 / 短暫斷線 / 密碼還沒套上
            last_err = e
            time.sleep(delay)
    raise RuntimeError(f"連不上或初始化 Postgres 失敗（重試 {retries} 次）：{last_err}")


# ── place_key 去重鍵 ─────────────────────────────────────────────────
def make_place_key(name: str, address: str | None, lat=None, lng=None) -> str:
    """同一間店不重複入庫的鍵：優先用正規化(店名+地址)；地址缺時退回店名+四捨五入座標。

    - 全形/半形、大小寫、空白統一，避免「鼎泰豐 」與「鼎泰豐」被當兩間。
    - 座標四捨五入到小數第 4 位（約 ±11 公尺），同店些微定位誤差仍視為同一鍵。
    """
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s or "").strip().lower()
        return re.sub(r"\s+", "", s)

    base = norm(name)
    addr = norm(address or "")
    if addr:
        return f"{base}@{addr}"
    if lat is not None and lng is not None:
        try:
            return f"{base}@{round(float(lat), 4)},{round(float(lng), 4)}"
        except (TypeError, ValueError):
            pass
    return base


# ── taxonomy ────────────────────────────────────────────────────────
def load_taxonomy() -> dict:
    """讀 taxonomy.yaml。優先 PyYAML，沒裝就用極簡解析器。"""
    if not TAXONOMY_PATH.exists():
        return {}
    text = TAXONOMY_PATH.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text).get("dimensions", {}) or {}
    except ModuleNotFoundError:
        return _mini_yaml_dimensions(text)


def _mini_yaml_dimensions(text: str) -> dict:
    dims: dict = {}
    cur = None
    in_dims = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^dimensions:\s*$", line):
            in_dims = True
            continue
        if not in_dims:
            continue
        m = re.match(r"^  (\w+):\s*$", line)
        if m:
            cur = m.group(1)
            dims[cur] = {"seed": []}
            continue
        if cur is None:
            continue
        m = re.match(r"^    seed:\s*\[(.*)\]\s*$", line)
        if m:
            dims[cur]["seed"] = [v.strip() for v in m.group(1).split(",") if v.strip()]
    return dims


def known_categories() -> set[str]:
    return set(load_taxonomy().keys())


def seed_taxonomy() -> None:
    dims = load_taxonomy()
    if not dims:
        return
    with pool().connection() as conn:
        for category, spec in dims.items():
            for name in spec.get("seed", []) or []:
                conn.execute(
                    "INSERT INTO tags(name, category, status, usage_count) "
                    "VALUES (%s,%s,'active',0) ON CONFLICT (category, name) DO NOTHING",
                    (name, category),
                )


# ── tag 解析：既有維度→active，新維度→proposed ──────────────────────
def resolve_tag(conn: psycopg.Connection, category: str, name: str, dims: set[str]) -> tuple[int, bool]:
    category = (category or "").strip()
    name = (name or "").strip()
    if not category or not name:
        raise ValueError(f"tag 不可有空欄位：category={category!r} name={name!r}")

    row = conn.execute(
        "SELECT canonical_tag_id FROM tag_aliases WHERE category=%s AND alias=%s",
        (category, name),
    ).fetchone()
    if row:
        return row[0], False

    row = conn.execute(
        "SELECT id FROM tags WHERE category=%s AND name=%s", (category, name)
    ).fetchone()
    if row:
        return row[0], False

    status = "active" if category in dims else "proposed"
    row = conn.execute(
        "INSERT INTO tags(name, category, status, usage_count) VALUES (%s,%s,%s,0) RETURNING id",
        (name, category, status),
    ).fetchone()
    return row[0], True


# ── place 入庫（place_key 去重）──────────────────────────────────────
def upsert_place(conn: psycopg.Connection, entry: dict, dims: set[str]) -> tuple[int | None, str]:
    """寫入一筆 place（place_key 去重）。回傳 (place_id|None, 狀態)：
    'added' / 'dup' / 'error'。"""
    name = (entry.get("name") or "").strip()
    if not name:
        return None, "error"
    place_key = (entry.get("place_key") or "").strip() or make_place_key(
        name, entry.get("address"), entry.get("lat"), entry.get("lng")
    )

    row = conn.execute("SELECT id FROM places WHERE place_key=%s", (place_key,)).fetchone()
    if row:
        return row[0], "dup"

    status = entry.get("status") or "want"
    if status not in ("want", "visited"):
        status = "want"

    row = conn.execute(
        "INSERT INTO places(name, address, lat, lng, description, place_key, status, "
        "price_level, rating, favorite, source, image_path, thumbnail_path) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            name,
            entry.get("address"),
            entry.get("lat"),
            entry.get("lng"),
            (entry.get("description") or "").strip() or None,
            place_key,
            status,
            entry.get("price_level"),
            entry.get("rating"),
            bool(entry.get("favorite")),
            entry.get("source"),
            entry.get("image_path"),
            entry.get("thumbnail_path"),
        ),
    ).fetchone()
    place_id = row[0]

    for t in entry.get("tags") or []:
        category = (t.get("category") or "").strip()
        tname = (t.get("name") or "").strip()
        if not category or not tname:
            continue
        tag_id, _is_new = resolve_tag(conn, category, tname, dims)
        conn.execute(
            "INSERT INTO place_tags(place_id, tag_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (place_id, tag_id),
        )
        conn.execute("UPDATE tags SET usage_count = usage_count + 1 WHERE id=%s", (tag_id,))

    return place_id, "added"


def _link_tag(conn: psycopg.Connection, place_id: int, tag_id: int) -> bool:
    cur = conn.execute(
        "INSERT INTO place_tags(place_id, tag_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (place_id, tag_id),
    )
    if cur.rowcount:
        conn.execute("UPDATE tags SET usage_count = usage_count + 1 WHERE id=%s", (tag_id,))
        return True
    return False


def _unlink_tag(conn: psycopg.Connection, place_id: int, tag_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM place_tags WHERE place_id=%s AND tag_id=%s", (place_id, tag_id)
    )
    if cur.rowcount:
        conn.execute(
            "UPDATE tags SET usage_count = GREATEST(usage_count - 1, 0) WHERE id=%s", (tag_id,)
        )
        return True
    return False


def _resolve_pairs(conn: psycopg.Connection, tags: list, dims: set[str]) -> set[int]:
    ids: set[int] = set()
    for t in tags or []:
        category = (t.get("category") or "").strip()
        name = (t.get("name") or "").strip()
        if not category or not name:
            continue
        tag_id, _is_new = resolve_tag(conn, category, name, dims)
        ids.add(tag_id)
    return ids


def update_place_tags(
    conn: psycopg.Connection,
    place_id: int,
    dims: set[str],
    *,
    replace: list | None = None,
    add: list | None = None,
    remove: list | None = None,
) -> dict | None:
    """改一間 place 的 tags。place 不存在回 None。
    replace 有給 → 整批替換（add/remove 視為附加微調）；否則只套用 add / remove。"""
    if conn.execute("SELECT 1 FROM places WHERE id=%s", (place_id,)).fetchone() is None:
        return None

    current = {
        r[0]
        for r in conn.execute(
            "SELECT tag_id FROM place_tags WHERE place_id=%s", (place_id,)
        ).fetchall()
    }
    add_ids = _resolve_pairs(conn, add, dims)
    remove_ids = _resolve_pairs(conn, remove, dims)

    if replace is not None:
        target = _resolve_pairs(conn, replace, dims) | add_ids
        target -= remove_ids
        to_add = target - current
        to_remove = current - target
    else:
        to_add = add_ids - remove_ids
        to_remove = remove_ids - add_ids

    added = sum(_link_tag(conn, place_id, tid) for tid in to_add)
    removed = sum(_unlink_tag(conn, place_id, tid) for tid in to_remove)

    return {"added": added, "removed": removed, "tags": place_tag_list(conn, place_id)}


def place_tag_list(conn: psycopg.Connection, place_id: int) -> list[dict]:
    return [
        {"category": cat, "name": name}
        for cat, name in conn.execute(
            "SELECT t.category, t.name FROM place_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.place_id=%s ORDER BY t.category, t.name",
            (place_id,),
        ).fetchall()
    ]


def get_place(conn: psycopg.Connection, place_id: int) -> dict | None:
    """取一間 place 的完整摘要（給刪除前 dry-run / 單筆查詢用）。不存在回 None。"""
    row = conn.execute(
        "SELECT id, name, address, lat, lng, description, place_key, status, price_level, "
        "rating, favorite, source, image_path, thumbnail_path FROM places WHERE id=%s",
        (place_id,),
    ).fetchone()
    if not row:
        return None
    cols = ["id", "name", "address", "lat", "lng", "description", "place_key", "status",
            "price_level", "rating", "favorite", "source", "image_path", "thumbnail_path"]
    place = dict(zip(cols, row))
    place["favorite"] = bool(place["favorite"])
    place["tags"] = place_tag_list(conn, place_id)
    return place


def delete_place(conn: psycopg.Connection, place_id: int) -> dict | None:
    """刪一間 place。place_tags 靠 FK ON DELETE CASCADE 連帶清掉；
    tags.usage_count 在刪 place_tags 前先補扣。回傳 {id, image_path, thumbnail_path}。"""
    row = conn.execute(
        "SELECT id, name, image_path, thumbnail_path FROM places WHERE id=%s",
        (place_id,),
    ).fetchone()
    if not row:
        return None
    pid, name, image_path, thumb = row
    conn.execute(
        "UPDATE tags SET usage_count = GREATEST(usage_count - 1, 0) WHERE id IN "
        "(SELECT tag_id FROM place_tags WHERE place_id=%s)", (place_id,)
    )
    conn.execute("DELETE FROM places WHERE id=%s", (place_id,))
    return {"id": pid, "name": name, "image_path": image_path, "thumbnail_path": thumb}
