// FoodPrint 美食地圖前端 — Nuxt 3 設定。
// SSR 開啟：server routes（/server/api）當代理，把『讀取』token 留在後端環境變數，
// 瀏覽器永遠看不到 token，也不直接打你的私有 DB server（同源、免 CORS）。
export default defineNuxtConfig({
  compatibilityDate: '2025-06-01',
  ssr: true,
  devtools: { enabled: true },

  css: ['~/assets/css/main.css'],

  app: {
    head: {
      title: 'FoodPrint — 美食口袋地圖',
      htmlAttrs: { lang: 'zh-Hant' },
      meta: [
        { charset: 'utf-8' },
        {
          name: 'viewport',
          content:
            'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover',
        },
        { name: 'robots', content: 'noindex, nofollow' }, // 私有地圖頁，不要被搜尋引擎收錄
      ],
    },
  },

  runtimeConfig: {
    // ── 私有（只在 server 端可讀，不會打包進前端）──────────────
    // 用環境變數覆蓋：NUXT_FOODPRINT_BASE_URL / NUXT_FOODPRINT_TOKEN
    foodprintBaseUrl: '', // 你的私有 DB server，如 http://192.168.2.183:8000（compose 內為 http://api:8000）
    foodprintToken: '', // server 的『讀取』token（FOODPRINT_READ_TOKEN）；沒設就留空
    // 前面有「你自己控制的」反代（Caddy / Cloudflare 等）終止 TLS 時設 'true'，
    // 才採信其轉送的 X-Forwarded-For；否則一律用真實 TCP 對端位址，避免公開
    // 訪客自帶假 XFF 讓後端的 per-IP 限流形同虛設。NUXT_TRUST_PROXY
    trustProxy: '',
    // 選用：給整個地圖頁加一道 HTTP Basic Auth 閘（格式 user:pass）。留空＝不設防、
    // 任何有網址的人都能瀏覽全庫。要公開分享就留空；只給自己/少數人看才填。NUXT_BASIC_AUTH
    basicAuth: '',
    public: {
      // 這裡放可以公開的設定即可——token 一律不放這。
    },
  },
})
