// GET /api/media/<相對路徑>  例如 /api/media/thumbs/abc.jpg
// 串流代理私有 DB server 的店家縮圖。token 在 server 端補上，原圖網址與 token
// 都不會出現在瀏覽器。回傳原始 content-type，並加上快取標頭。
export default defineEventHandler(async (event) => {
  const { baseUrl, token } = backendConfig(event)
  const rel = (getRouterParam(event, 'path') || '').replace(/^\/+/, '')
  // 白名單：只代理 thumbs/<檔名>。避免被當成「萬用轉發」打到後端任意路徑，
  // 也擋掉 ../ 之類的路徑穿越（地圖頁只會用到縮圖，原圖端點本就需讀寫 token）。
  if (!/^thumbs\/[\w.-]+$/.test(rel)) {
    throw createError({ statusCode: 400, statusMessage: '不合法的圖片路徑' })
  }

  const url = new URL(`${baseUrl}/${rel}`)
  if (token) url.searchParams.set('t', token)

  const upstream = await fetch(url, { headers: { 'User-Agent': 'FoodPrint-web' } })
  if (!upstream.ok || !upstream.body) {
    throw createError({
      statusCode: upstream.status || 502,
      statusMessage: `取圖失敗（${upstream.status}）`,
    })
  }

  setHeader(event, 'content-type', upstream.headers.get('content-type') || 'image/jpeg')
  const len = upstream.headers.get('content-length')
  if (len) setHeader(event, 'content-length', len)
  // 縮圖內容不變、可長快取（私有頁，放 private 即可）。
  setHeader(event, 'cache-control', 'private, max-age=86400')

  return upstream.body
})
