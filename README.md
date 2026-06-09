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
   python3 scripts/backend.py nearby --lat 25.033 --lng 121.564 --radius 1.5 --table
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
- [ ] **web 地圖前端**：Nuxt + Leaflet/MapLibre，把口袋名單釘在地圖上、可篩選、點 pin 看卡片
- [ ] 想去 → 吃過的一鍵轉換與到訪紀錄
- [ ] 口味畫像：從累積的店家學你的偏好，主動推薦

---

## 使用聲明

本工具供**個人**收藏與規劃，請只收藏你有權記錄的資訊，尊重店家與原作者權益，勿用於商業或侵權用途。
