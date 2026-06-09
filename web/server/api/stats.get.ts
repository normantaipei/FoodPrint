// GET /api/stats → 代理私有 DB server 的 /stats（{ places, want, visited, ... }）。
export default defineEventHandler(async (event) => {
  const { baseUrl, token } = backendConfig(event)
  try {
    // 轉發真實 client IP，後端限流才能 per-user，而非全站共用一個額度。
    return await $fetch(`${baseUrl}/stats`, {
      params: token ? { t: token } : {},
      headers: { 'x-forwarded-for': clientIp(event) },
    })
  } catch (err: any) {
    console.error('[api/stats] 後端連線異常：', err?.message || err)
    throw createError({
      statusCode: err?.statusCode || 502,
      statusMessage: '讀取庫狀態失敗，請稍後再試',
    })
  }
})
