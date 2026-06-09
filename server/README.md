# FoodPrint 私有 DB（自架 server）

FoodPrint 的庫存在**你自己網域內的一台機器**：PostgreSQL（店家 metadata + 標籤）
＋一個 FastAPI 服務（收店家、選配照片、提供查詢 / 附近）。原始資料留在你手上。

## 架站（一次）

需求：一台能跑 Docker 的機器（NAS / 迷你主機 / Proxmox LXC 皆可）。

```bash
cd server
cp .env.example .env
# 編輯 .env：改掉 POSTGRES_PASSWORD，並產一組讀寫 token：
#   python3 -c "import secrets; print(secrets.token_hex(32))"
# 填進 FOODPRINT_TOKEN（建議再產一組唯讀 FOODPRINT_READ_TOKEN）

docker compose up -d --build
```

起來後測一下（在同一台或區網內）：
```bash
curl http://localhost:8000/health
# {"ok":true,"service":"foodprint-private-db"}
```

> ⚠️ 乾淨環境**第一次** `up` 偶爾會因 Postgres 首次初始化的重啟而短暫失敗，
> api 會自動重試並自我修復；若 api 一直 restart，`docker compose logs api` 看訊息，
> 多數情況再 `docker compose up -d` 一次即正常。

## 連到 Claude 端

在 FoodPrint repo 根（Claude Code 所在的機器）把 backend 指過去：
```bash
python3 scripts/backend.py set \
    --base-url http://<這台機器的內網IP>:8000 --token <你的讀寫 token>
python3 scripts/backend.py status   # 應印出連線 OK 與庫狀態
```

## 權限模型

| 端點 | 需要 | 對外開放 |
|---|---|---|
| `GET /health` | 免 token | 是 |
| `GET /stats` `/search` `/nearby` `/thumbs/{name}` | 讀取 token（讀寫或唯讀皆可） | 可（給未來地圖前端） |
| `GET /images/{name}`（原圖） | 讀寫 token | 否（防原圖被整批搬走） |
| `POST /places`、`PUT /places/{id}/tags`、`DELETE /places/{id}` | 讀寫 token **且僅限區網** | 否 |

- 兩個 token 都沒設 → 純內網信任、不檢查（只建議在完全隔離的內網這樣）。
- 寫入端點看**真實 TCP 對端 IP**（不信任可偽造的 `X-Forwarded-For`）。
  若把本服務反代在同一台後面對外，對端 IP 會變私有位址而誤判為區網——
  **別把寫入端點經由那個 proxy 對外轉發**，對外只開查詢端點。
- `/search` `/stats` `/nearby` 有速率限制（預設 60/分鐘，`FOODPRINT_SEARCH_RATE` 可調）。

## 資料持久化

- `pgdata` volume：PostgreSQL 資料。
- `media` volume（容器內 `/data`）：店家照片原圖（`images/`）+ 縮圖（`thumbs/`）。
- 備份：`docker compose exec db pg_dump -U foodprint foodprint > backup.sql`，
  以及打包 `media` volume。

## 端點速覽

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/health` | 健康檢查 |
| GET | `/stats` | places / tags 數、想去 vs 吃過 |
| POST | `/places` | 收一間店 + metadata（+選配照片），`place_key` 去重 |
| GET | `/places/{id}` | 取一間店摘要 |
| PUT | `/places/{id}/tags` | 改 tags（`tags` 整批替換 / `add` / `remove`） |
| DELETE | `/places/{id}` | 刪一間店（連帶清 tags + 磁碟照片） |
| GET | `/search?q=&tag=&status=&limit=&offset=` | 關鍵字 + 標籤粗篩 |
| GET | `/nearby?lat=&lng=&radius_km=&tag=&status=` | 座標附近，由近到遠，帶 `distance_km` |
| GET | `/thumbs/{name}` `/images/{name}` | 取縮圖 / 原圖 |
