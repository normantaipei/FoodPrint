# FoodPrint 美食地圖前端（Nuxt 3）

把你的美食口袋名單釘在地圖上的 **Nuxt 3（SSR）** 應用：地圖用 **Leaflet + OpenStreetMap 圖磚（免 API key）**，
資料一律走 Nuxt 自己的 server route 代理你的私有 DB server——**token 留在 server 端、永不外洩給瀏覽器**。

## 設計重點

- **token 走 server proxy**：瀏覽器打的是同源的 `/api/*`（Nuxt server route），由 Nuxt 在
  server 端補上『讀取』token 再轉打後端。寫入端點（`POST/PUT/DELETE /places`、原圖 `/images`）
  **不經此代理**，仍只能在區網內帶讀寫 token 直接打 api。
- **同源、免 CORS**：前端打相對路徑 `/api/...`，沒有跨源問題。
- **domain / token 不烤進映像**：一律在 `docker run` / compose 時用環境變數帶入
  （`NUXT_FOODPRINT_BASE_URL` / `NUXT_FOODPRINT_TOKEN`），對應 `nuxt.config.ts` 的 `runtimeConfig`。

## 功能（與舊版相同）

- 依 `status`（想去 / 吃過）、`★ 最愛`、價位 `$`、關鍵字、以及各維度標籤（料理 / 地區 / 氛圍…）篩選；
  標籤面板由已載入的資料自動推導（維度間 AND、維度內 OR）。
- 點 pin → 卡片：縮圖、心得、評分 ★、價位 $、標籤、Google Maps / 來源連結。
- 「📍 找我附近」：用瀏覽器定位呼叫 `/api/nearby`，標出距離並重新置中。
- marker 自動分群（leaflet.markercluster），手機版側欄可收合。

## 怎麼跑

### 跟著後端一起（建議）

repo 根的一鍵安裝會把後端 + 這個前端一起用 docker compose 起來（自動避埠、串好 token）：

```bash
bash install.sh
```

之後開 `http://<這台機器>:<WEB_PORT>`（預設 8080，被占用會自動往後找；實際埠見 `server/.env`）。
compose 內前端容器以 `NUXT_FOODPRINT_BASE_URL=http://api:8000` 直接打後端服務名，
讀取 token 由 `FOODPRINT_PROXY_TOKEN` 注入成 `NUXT_FOODPRINT_TOKEN`。

### 把前端單獨架在另一台機器

```bash
curl -fsSL https://raw.githubusercontent.com/normantaipei/FoodPrint/main/web/bootstrap.sh \
  | bash -s -- http://<後端IP>:<埠> <read_token>
```

### 本機開發

```bash
cp .env.example .env     # 填 NUXT_FOODPRINT_BASE_URL / NUXT_FOODPRINT_TOKEN
npm install
npm run dev              # http://localhost:3000
```

## 檔案

| 路徑 | 作用 |
|---|---|
| `nuxt.config.ts` | Nuxt 設定（SSR、`runtimeConfig` 收 domain/token、head/meta） |
| `app.vue` / `pages/index.vue` | 版面 + 地圖 UI（頂列搜尋、側欄篩選/清單、Leaflet 地圖） |
| `composables/usePlaces.ts` | 撈資料、庫狀態、標籤面板、篩選邏輯（維度間 AND、維度內 OR） |
| `utils/place.ts` | 型別與純函式（`parseTag` / `mediaUrl` / 卡片 popup HTML） |
| `utils/leaflet.ts` | 瀏覽器端按需載入 Leaflet + markercluster（CDN） |
| `assets/css/main.css` | 樣式（含手機版、pin、popup 卡片） |
| `server/api/*.get.ts` | server proxy：`stats` / `search` / `nearby` / `media`（縮圖），補 token 後轉打後端 |
| `server/utils/backend.ts` | 代理共用工具（讀 runtimeConfig、轉發 client IP、組 query） |
| `Dockerfile` | 多階段建構，產出精簡 Node runtime 映像 |
| `bootstrap.sh` | 把前端單獨架在另一台機器的一鍵腳本 |
