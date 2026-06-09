# FoodPrint — 美食口袋地圖

**建構你自己的美食口袋地圖** —— 一個自動學習、持續延展的個人美食收藏庫。

把你「吃過很讚」或「想去」的店丟給 Claude——一個 Google Maps 連結、一則 IG/部落格貼文、
一張照片，或單純一句話——它會辨識店家、產出心得與結構化標籤（料理、招牌、氛圍、場合、地區…）、
連同地址與座標入庫。之後你想吃東西時，跟它說「找大安區的咖啡廳」或「我在信義區附近有什麼想吃的」，
它就從你的口袋名單幫你挑、幫你找附近的。

> 設計參考自姊妹專案 **PosePlanner**（cosplay 拍攝參考庫）的三層架構：
> Claude Skill ＋ Python 客戶端 ＋ 自架私有 DB。把「內容實體」從 cos 圖換成店家，
> 並加上地理欄位（經緯度）支援「附近」查詢。

---

## 系統架構

「入庫」與「找店」分離；庫存在**你自己網域內自架的一台機器**（PostgreSQL + FastAPI）。
架構原始檔在 [docs/architecture.mmd](docs/architecture.mmd)（Mermaid）。

三層：

1. **Claude Skill**（[SKILL.md](SKILL.md)）：看圖 / 讀連結 → 產心得與標籤 → 入庫 / 找店的流程腦。
2. **scripts/**（Python，零安裝、純標準庫）：
   - [scripts/backend.py](scripts/backend.py)：私有 DB 客戶端（`status` / `set` / `place` / `ingest` / `search` / `nearby` / `delete`）。
   - [scripts/fetch_place.py](scripts/fetch_place.py)：從 Google Maps / 社群連結擷取店名/地址/座標（免 API key）。
3. **server/**（自架）：[FastAPI + PostgreSQL](server/README.md)，`docker compose up` 即可。
4. **web/**（地圖前端）：[靜態 SPA + Leaflet](web/README.md)（OSM 圖磚，免 API key），
   由 nginx 反向代理唯讀端點並注入讀取 token——瀏覽器端免持 token、免設 CORS。

---

## 一鍵安裝整套系統

一條命令把**後端（FastAPI + PostgreSQL）＋ 地圖前端**全部建起來並部署。
腳本會自動產生隨機密碼與 token、**自動避開已被占用的埠**、啟動容器、等健康檢查通過，
再把 Claude 端的 backend client 指向本機。

**全新、連 repo 都還沒拉的機器**（會自動裝 git/Docker、clone 到 `~/FoodPrint`、部署）：

```bash
curl -fsSL https://raw.githubusercontent.com/normantaipei/FoodPrint/main/install.sh | bash
```

**已經 clone 好 repo**：

```bash
bash install.sh
```

跑完會印出：地圖網址（含區網 IP）、API 位址、兩組 token，以及一個 **skill 下載網址**。
後端有個 **`GET /skill`** 端點（**僅限區網、免 token**）：區網內任一台機器 / 手機開
`http://<後端IP>:<API_PORT>/skill` 就會即時打包並下載 `foodprint-skill.zip`，且把「這台後端的
位址 + 讀寫 token」即時烤進去。下載後上傳到 Claude Desktop（設定 → Skills → Upload skill）
即「已連線」，免再手動 set；或在本機 `ln -s` 給 Claude Code 用。

- 想給只搜尋、不入庫的人：`http://<後端IP>:<API_PORT>/skill?token=ro`（烤唯讀 token）。
- `/skill` 看真實對端 IP 限區網；要放行 VPN 等網段用 `FOODPRINT_SKILL_ALLOW_CIDRS`。

> 入庫 / 刪除是區網限定（`require_lan`），要在區網內的 Claude Code 跑；查詢（找店 / 附近）不限。

重跑 = 升級（沿用既有秘密與埠，不會亂跳）。其他：

```bash
bash install.sh --logs    # 看即時 log
bash install.sh --down    # 停止整套（資料保留在 volume）
```

> 需求：一台跑得動 Docker 的機器。秘密與實際使用的埠都寫在 `server/.env`（不進版控）。

---

## 安裝（Claude Code）

把這個資料夾放進 Claude Code 的 skills 目錄（或在 repo 內直接用 `/foodprint`）：
```bash
ln -s "$(pwd)" ~/.claude/skills/foodprint     # 或直接複製整個資料夾
```

> 入庫/刪除要連到你區網內自架的 server，需在本地端開沙箱——請用 **Claude Code**。

---

## 快速開始

1. **架後端**（一次）：見 [server/README.md](server/README.md)。
   ```bash
   cd server && cp .env.example .env   # 改密碼、填 token
   docker compose up -d --build
   ```
2. **連上後端**：
   ```bash
   python3 scripts/backend.py set --base-url http://192.168.x.x:8000 --token <讀寫 token>
   python3 scripts/backend.py status
   ```
3. **入庫**：在 Claude 對話裡貼 Google Maps 連結 / 照片 / 描述，說「加進我的口袋名單」，
   或直接 `/foodprint`。也可手動：
   ```bash
   python3 scripts/backend.py place --name "鼎泰豐 信義店" --lat 25.0401 --lng 121.5637 \
       --status visited --rating 5 --tag cuisine=中式 --tag dish=小籠包 --tag district=信義區
   ```
4. **找店**：
   ```bash
   python3 scripts/backend.py search "火鍋 約會" --tag district=大安區 --table
   python3 scripts/backend.py search --tag occasion=約會 --price-min 3 --table   # 價位區間（1..4，$~$$$$）
   python3 scripts/backend.py nearby --lat 25.033 --lng 121.564 --radius 1.5 --price-max 2 --table
   ```

---

## 資料模型

- **places**：店名、地址、`lat`/`lng`、心得、`status`（想去 / 吃過）、價位、評分、最愛、來源、選配照片。
  `place_key`（正規化店名+地址 / 座標）去重，同店不重複入庫。
- **tags**：動態、可延展的標籤系統，維度固定（`cuisine` / `dish` / `meal_type` / `vibe` /
  `occasion` / `feature` / `district`），值開放成長。見 [taxonomy.yaml](taxonomy.yaml)。
  既有維度的新值自動 `active`；全新維度標 `proposed`。

---

## 路線圖

- [x] Skill ＋ scripts ＋ 自架 Postgres 後端（含 `/nearby` 地理查詢）
- [x] **web 地圖前端**：Leaflet（OSM）靜態 SPA，把口袋名單釘在地圖上、可依狀態 / 標籤 / 價位篩選、
  點 pin 看卡片（縮圖 + 心得 + 標籤 + Google Maps）、「找我附近」。一鍵 `install.sh` 部署
- [ ] 想去 → 吃過的一鍵轉換與到訪紀錄
- [ ] 口味畫像：從累積的店家學你的偏好，主動推薦

---

## 使用聲明

本工具供**個人**收藏與規劃，請只收藏你有權記錄的資訊，尊重店家與原作者權益，勿用於商業或侵權用途。
