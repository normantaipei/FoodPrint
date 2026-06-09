-- FoodPrint 私有 DB（自架）結構 — PostgreSQL 版
-- 由 api 服務在啟動時冪等套用（CREATE ... IF NOT EXISTS），不需手動灌。
--
-- 設計沿用 PosePlanner 的「內容實體 + 動態 tag 系統」，把實體從 pose（cos 圖）換成
-- place（店家），並加上地理欄位（lat / lng）支援「附近」查詢。
-- 「招牌 / 料理 / 氛圍 / 場合 / 特色 / 地區」走 tag 系統；
-- 「地址 / 經緯度 / 價位 / 想去 or 吃過 / 個人評分」是 places 的欄位。

-- ── 核心：每一間入庫的 place ─────────────────────────────
CREATE TABLE IF NOT EXISTS places (
  id              SERIAL PRIMARY KEY,
  name            TEXT NOT NULL,                     -- 店名
  address         TEXT,                              -- 地址（人類可讀）
  lat             DOUBLE PRECISION,                  -- 緯度（地圖 / 附近查詢）
  lng             DOUBLE PRECISION,                  -- 經度
  description     TEXT,                              -- 一句話心得 / 為什麼想去（Claude 產或使用者給）
  place_key       TEXT NOT NULL UNIQUE,              -- 去重鍵：正規化(店名+地址) 或 座標，避免同店重複入庫
  status          TEXT NOT NULL DEFAULT 'want',      -- want（想去）/ visited（吃過）
  price_level     INTEGER,                           -- 價位 1..4（$ $$ $$$ $$$$），可選
  rating          INTEGER,                           -- 個人評分 1..5，可選
  favorite        BOOLEAN NOT NULL DEFAULT FALSE,
  source          TEXT,                              -- 來源（Google Maps 連結 / IG / 備註）
  image_path      TEXT,                              -- 選配：店家照片相對路徑（images/<hash>.<ext>）
  thumbnail_path  TEXT,                              -- 選配：縮圖相對路徑（thumbs/<hash>.jpg）
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 「附近」查詢的常用排序欄位；座標範圍粗篩走這個索引。
CREATE INDEX IF NOT EXISTS idx_places_lat ON places(lat);
CREATE INDEX IF NOT EXISTS idx_places_lng ON places(lng);
CREATE INDEX IF NOT EXISTS idx_places_status ON places(status);

-- ── 標籤：動態、可延展（料理 / 招牌 / 氛圍 / 場合 / 特色 / 地區…）─────
CREATE TABLE IF NOT EXISTS tags (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL,                          -- 標籤值，如「火鍋」
  category    TEXT NOT NULL,                          -- 維度，如 cuisine / vibe / district
  usage_count INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'active',         -- active / proposed
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (category, name)
);

-- 同義詞收斂：把別名指向 canonical tag（如「咖啡店」→「咖啡廳」）
CREATE TABLE IF NOT EXISTS tag_aliases (
  alias            TEXT NOT NULL,
  category         TEXT NOT NULL,
  canonical_tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (category, alias)
);

CREATE TABLE IF NOT EXISTS place_tags (
  place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
  tag_id   INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  PRIMARY KEY (place_id, tag_id)
);

-- ── 入庫紀錄（給 stats / 稽核）─────────────────────────────
CREATE TABLE IF NOT EXISTS ingest_log (
  id         SERIAL PRIMARY KEY,
  n_added    INTEGER NOT NULL DEFAULT 0,
  n_dup      INTEGER NOT NULL DEFAULT 0,
  n_new_tags INTEGER NOT NULL DEFAULT 0,
  summary    TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
