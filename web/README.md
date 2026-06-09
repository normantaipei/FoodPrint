# FoodPrint 地圖前端

把你的美食口袋名單釘在地圖上的純前端 SPA：**Leaflet + OpenStreetMap 圖磚（免 API key）**，
無建置步驟（不需 Node），由一個 nginx 容器同時負責「送靜態檔」與「反向代理唯讀 API」。

## 設計重點

- **零建置**：`public/` 就是成品（`index.html` / `app.js` / `style.css`），直接掛進 nginx。
- **瀏覽器端不持 token**：nginx 只把 `GET /api/{stats,search,nearby,thumbs/...}` 代理到後端 api，
  並在 server 端注入讀取 token。寫入端點（`POST/PUT/DELETE /places`、原圖 `/images`）一律
  **不經此代理**，仍只能在區網內帶讀寫 token 直接打 api。
- **同源**：前端打的是相對路徑 `/api/...`，所以沒有 CORS 問題。

## 功能

- 依 `status`（想去 / 吃過）、`★ 最愛`、關鍵字、以及各維度標籤（料理 / 地區 / 氛圍…）篩選；
  標籤面板由已載入的資料自動推導（維度間 AND、維度內 OR）。
- 點 pin → 卡片：縮圖、心得、評分 ★、價位 \$、標籤、Google Maps / 來源連結。
- 「📍 找我附近」：用瀏覽器定位呼叫 `/api/nearby`，標出距離並重新置中。
- marker 自動分群（leaflet.markercluster），手機版側欄可收合。

## 怎麼跑

跟著 repo 根的一鍵安裝即可（會連同後端一起部署、自動避埠）：

```bash
bash install.sh
```

之後開 `http://<這台機器>:<WEB_PORT>`（預設 8080，被占用會自動往後找；實際埠見 `server/.env`）。

### 進階：指向別處的 API

預設打同源的 `/api`。若要讓前端直接打另一個位址（會需要該後端設好 CORS 與 token），
在載入 `app.js` 前設：

```html
<script>window.FOODPRINT_API_BASE = "http://192.168.1.50:8000";</script>
```

## 檔案

| 檔案 | 作用 |
|---|---|
| `public/index.html` | 版面 + 載入 Leaflet（CDN）與本地 JS/CSS |
| `public/app.js` | 撈資料、篩選、地圖 marker、卡片、找附近 |
| `public/style.css` | 樣式（含手機版） |
| `nginx.conf.template` | 靜態服務 + 唯讀 API 反向代理（envsubst 注入 `FOODPRINT_PROXY_TOKEN`） |
