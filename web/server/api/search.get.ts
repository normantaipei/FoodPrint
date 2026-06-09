// GET /api/search?q=&tag=&status=&price_min=&price_max=&limit=&offset=
// 代理到私有 DB server 的 /search，回傳店家陣列。token 在 server 端補上。
const FIELDS = ['q', 'tag', 'status', 'price_min', 'price_max', 'limit', 'offset'] as const

export default defineEventHandler(async (event) => {
  const { baseUrl, token } = backendConfig(event)
  const params = pickParams(getQuery(event), FIELDS, token)

  try {
    // 轉發真實 client IP，後端限流才能 per-user，而非全站共用一個額度。
    return await $fetch(`${baseUrl}/search`, {
      params,
      headers: { 'x-forwarded-for': clientIp(event) },
    })
  } catch (err: any) {
    // 後端錯誤只記在 server，不把原文（含內網 base_url）回給瀏覽器。
    console.error('[api/search] 後端連線異常：', err?.message || err)
    throw createError({
      statusCode: err?.statusCode || err?.response?.status || 502,
      statusMessage: '搜尋失敗，請稍後再試',
    })
  }
})
