// FoodPrint 店家資料的型別與純函式（好測、與 Leaflet/DOM 無關）。
// 圖片改走 Nuxt 代理：/api/media/<相對路徑>（token 由 server 端補，前端不碰）。

export interface Place {
  id: number
  name: string
  address?: string | null
  lat?: number | null
  lng?: number | null
  description?: string | null
  status?: string // want / visited
  price_level?: number | null // 1..4
  rating?: number | null // 1..5
  favorite?: boolean
  source?: string | null
  image_path?: string | null
  thumbnail_path?: string | null
  tags?: string[] // "category:name"
  distance_km?: number | null // 由「找我附近」標注
}

// category → 中文維度名（決定篩選面板的分組與排序）
export const DIM_LABELS: Record<string, string> = {
  cuisine: '料理',
  dish: '招牌',
  meal_type: '餐別',
  vibe: '氛圍',
  occasion: '場合',
  feature: '特色',
  district: '地區',
}
export const DIM_ORDER = ['district', 'cuisine', 'dish', 'meal_type', 'vibe', 'occasion', 'feature']

export function mediaUrl(rel?: string | null): string {
  if (!rel) return ''
  return `/api/media/${String(rel).replace(/^\/+/, '')}`
}

// "category:name" → [category, name]
export function parseTag(t: string): [string, string] {
  const i = t.indexOf(':')
  return i < 0 ? ['', t] : [t.slice(0, i), t.slice(i + 1)]
}

// 取某維度（category）下的所有 tag 名稱，例如菜系（cuisine）。
export function tagNames(p: Place, cat: string): string[] {
  return (p.tags || [])
    .map(parseTag)
    .filter(([c]) => c === cat)
    .map(([, n]) => n)
}

// 這間店的菜系（cuisine）名稱，逗號分隔；無則回空字串。
export function cuisineText(p: Place): string {
  return tagNames(p, 'cuisine').join('、')
}

// 價位：1..4 → $ ~ $$$$；無資料回空字串。
export function priceText(p: Place): string {
  return p.price_level ? '$'.repeat(p.price_level) : ''
}

function esc(s: unknown): string {
  return String(s == null ? '' : s).replace(
    /[&<>"']/g,
    (c) => (({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }) as any)[c],
  )
}

// 地圖 pin 的 Leaflet popup 內容（HTML 字串；Leaflet popup 只吃 HTML）。
export function cardHtml(p: Place): string {
  const thumb = mediaUrl(p.thumbnail_path)
  const badges: string[] = []
  badges.push(
    '<span class="badge ' +
      (p.status === 'visited' ? 'visited' : 'want') +
      '">' +
      (p.status === 'visited' ? '吃過' : '想去') +
      '</span>',
  )
  if (p.rating) badges.push('<span class="badge rating">' + '★'.repeat(p.rating) + '</span>')
  if (p.price_level) badges.push('<span class="badge price">' + '$'.repeat(p.price_level) + '</span>')
  if (p.favorite) badges.push('<span class="badge rating">★ 最愛</span>')

  const tags = (p.tags || [])
    .map((t) => '<span>' + esc(parseTag(t)[1]) + '</span>')
    .join('')
  const q =
    p.lat != null && p.lng != null ? p.lat + ',' + p.lng : encodeURIComponent(p.name)
  const gmap = 'https://www.google.com/maps/search/?api=1&query=' + q
  const dist =
    p.distance_km != null ? '<div class="dist">距離約 ' + p.distance_km + ' km</div>' : ''

  return (
    '<div class="card">' +
    (thumb
      ? '<img class="thumb" src="' +
        esc(thumb) +
        '" alt="" loading="lazy" onerror="this.style.display=\'none\'" />'
      : '') +
    '<div class="pad">' +
    '<p class="title">' +
    esc(p.name) +
    '</p>' +
    '<div class="badges">' +
    badges.join('') +
    '</div>' +
    dist +
    (p.description ? '<div class="desc">' + esc(p.description) + '</div>' : '') +
    (p.address ? '<div class="addr">📍 ' + esc(p.address) + '</div>' : '') +
    (tags ? '<div class="tags">' + tags + '</div>' : '') +
    '<div class="links">' +
    '<a href="' +
    esc(gmap) +
    '" target="_blank" rel="noopener">Google Maps ↗</a>' +
    (p.source
      ? '<a href="' + esc(p.source) + '" target="_blank" rel="noopener">來源 ↗</a>'
      : '') +
    '</div>' +
    '</div></div>'
  )
}
