// GET /api/nearby?lat=&lng=&radius_km=&status=&price_min=&price_max=&limit=
// 代理私有 DB server 的 /nearby（「找我附近」），回傳含 distance_km 的店家陣列。
const FIELDS = ['lat', 'lng', 'radius_km', 'tag', 'status', 'price_min', 'price_max', 'limit'] as const

export default defineEventHandler(async (event) => {
  const { baseUrl, token } = backendConfig(event)
  const params = pickParams(getQuery(event), FIELDS, token)

  try {
    return await $fetch(`${baseUrl}/nearby`, {
      params,
      headers: { 'x-forwarded-for': clientIp(event) },
    })
  } catch (err: any) {
    console.error('[api/nearby] 後端連線異常：', err?.message || err)
    throw createError({
      statusCode: err?.statusCode || err?.response?.status || 502,
      statusMessage: '找附近失敗，請稍後再試',
    })
  }
})
