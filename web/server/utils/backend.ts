// 後端代理共用工具：從 runtimeConfig 取私有 DB server 的 baseUrl + token，
// 組出帶 token 的目標網址。token 只在 server 端使用，不會回傳給瀏覽器。
import type { H3Event } from 'h3'

export function backendConfig(event: H3Event) {
  const cfg = useRuntimeConfig(event)
  const baseUrl = String(cfg.foodprintBaseUrl || '').replace(/\/+$/, '')
  const token = String(cfg.foodprintToken || '')
  if (!baseUrl) {
    throw createError({
      statusCode: 500,
      statusMessage:
        '尚未設定 NUXT_FOODPRINT_BASE_URL（你的私有 DB server domain）。請看 web/.env.example。',
    })
  }
  return { baseUrl, token }
}

// 取「真實 client IP」轉給後端：後端限流（slowapi）讀 X-Forwarded-For 第一段分辨來源。
// 若不轉發，所有公開訪客都頂著 Nuxt 這一個 IP，60/min 會變成全站共用、一人即可卡死所有人。
// 有上游代理（Cloudflare 等）時取其 XFF，否則退回 TCP 連線位址。
export function clientIp(event: H3Event): string {
  return getRequestIP(event, { xForwardedFor: true }) || ''
}

// 把 runtimeConfig 帶來的 query 子集挑出來（只放行白名單欄位），再補上 token（?t=）。
export function pickParams(
  q: Record<string, unknown>,
  keys: readonly string[],
  token: string,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const k of keys) {
    if (q[k] !== undefined && q[k] !== '') out[k] = q[k]
  }
  if (token) out.t = token
  return out
}
