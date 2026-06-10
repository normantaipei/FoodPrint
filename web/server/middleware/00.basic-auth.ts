// 選用的 HTTP Basic Auth 閘：保護整個地圖頁與 /api 代理。
// 只有在 runtimeConfig.basicAuth（環境變數 NUXT_BASIC_AUTH，格式 "user:pass"）有值時才啟用；
// 留空＝完全不設防（任何有網址的人都能瀏覽全庫）。檔名前綴 00. 確保它最先執行。
//
// 注意：這道閘擋的是「瀏覽器訪客」。後端寫入端點另有「區網 + 讀寫 token」把關，
// 與這裡彼此獨立。
export default defineEventHandler((event) => {
  const expected = String(useRuntimeConfig(event).basicAuth || '')
  if (!expected) return // 未設定 → 不設防

  const challenge = () => {
    setHeader(event, 'WWW-Authenticate', 'Basic realm="FoodPrint", charset="UTF-8"')
    throw createError({ statusCode: 401, statusMessage: '需要登入' })
  }

  const header = getRequestHeader(event, 'authorization') || ''
  if (!header.startsWith('Basic ')) return challenge()

  let provided = ''
  try {
    provided = Buffer.from(header.slice(6).trim(), 'base64').toString('utf-8')
  } catch {
    return challenge()
  }

  // 等長且逐位元比較，避免以回應時間反推密碼。
  if (!timingSafeEqualStr(provided, expected)) return challenge()
})

function timingSafeEqualStr(a: string, b: string): boolean {
  const ba = Buffer.from(a, 'utf-8')
  const bb = Buffer.from(b, 'utf-8')
  if (ba.length !== bb.length) return false
  let diff = 0
  for (let i = 0; i < ba.length; i++) diff |= ba[i] ^ bb[i]
  return diff === 0
}
