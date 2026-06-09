// 美食地圖的資料與篩選狀態：撈全部店家（分頁）、庫狀態、各維度標籤面板、
// 以及「維度間 AND、維度內 OR」的篩選邏輯。資料一律走 Nuxt 代理（/api/*），
// token 不出現在前端。Leaflet 地圖渲染留在 pages/index.vue（瀏覽器端）。
import { DIM_ORDER, parseTag, type Place } from '~/utils/place'

const PAGE = 100 // /search 單頁上限
const MAX_PLACES = 2000 // 安全上限，避免無限撈

export interface TagDim {
  cat: string
  names: { name: string; count: number }[]
}

export function usePlaces() {
  const places = ref<Place[]>([])
  const statsText = ref('')
  const error = ref('')
  const loaded = ref(false)

  // ── 篩選狀態 ─────────────────────────────────────────
  const status = ref('') // '' | want | visited
  const favOnly = ref(false)
  const keyword = ref('')
  const priceLevels = reactive(new Set<number>()) // 多選＝OR；空＝不限
  const selectedTags = reactive<Record<string, Set<string>>>({}) // category -> 選中的 name
  // 「找我附近」算出的距離：id -> km（不直接改 places，方便重置）
  const distances = ref<Record<number, number>>({})

  // ── 撈資料 ───────────────────────────────────────────
  async function loadStats() {
    try {
      const s = await $fetch<Record<string, number>>('/api/stats')
      statsText.value = `${s.places ?? 0} 間 · 想去 ${s.want ?? 0} · 吃過 ${s.visited ?? 0}`
    } catch {
      /* 無 token 或離線時靜默 */
    }
  }

  async function loadAll() {
    const acc: Place[] = []
    let offset = 0
    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const rows = await $fetch<Place[]>('/api/search', { params: { limit: PAGE, offset } })
        if (!Array.isArray(rows)) throw new Error('server 回傳非預期格式')
        acc.push(...rows)
        offset += rows.length
        if (rows.length < PAGE || acc.length >= MAX_PLACES) break
      }
      places.value = acc
      loaded.value = true
    } catch (err: any) {
      error.value = err?.message || String(err)
    }
  }

  // ── 標籤面板（由已載入資料推導，因為 server 無「列出所有 tag」端點）──
  const tagDims = computed<TagDim[]>(() => {
    const byDim: Record<string, Record<string, number>> = {}
    for (const p of places.value) {
      for (const t of p.tags || []) {
        const [cat, name] = parseTag(t)
        if (!cat) continue
        ;(byDim[cat] = byDim[cat] || {})[name] = (byDim[cat][name] || 0) + 1
      }
    }
    return Object.keys(byDim)
      .sort((a, b) => {
        const ia = DIM_ORDER.indexOf(a),
          ib = DIM_ORDER.indexOf(b)
        return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
      })
      .map((cat) => ({
        cat,
        names: Object.keys(byDim[cat])
          .sort((a, b) => byDim[cat][b] - byDim[cat][a])
          .map((name) => ({ name, count: byDim[cat][name] })),
      }))
  })

  // ── 篩選邏輯：維度間 AND、維度內 OR ─────────────────────
  function passes(p: Place): boolean {
    if (status.value && p.status !== status.value) return false
    if (favOnly.value && !p.favorite) return false
    if (priceLevels.size && !(p.price_level != null && priceLevels.has(p.price_level))) return false

    if (keyword.value) {
      const hay = (
        p.name +
        ' ' +
        (p.address || '') +
        ' ' +
        (p.description || '') +
        ' ' +
        (p.tags || []).join(' ')
      ).toLowerCase()
      const ok = keyword.value
        .toLowerCase()
        .split(/\s+/)
        .every((w) => !w || hay.indexOf(w) >= 0)
      if (!ok) return false
    }

    const ptags: Record<string, string[]> = {}
    for (const t of p.tags || []) {
      const [c, n] = parseTag(t)
      ;(ptags[c] = ptags[c] || []).push(n)
    }
    for (const cat of Object.keys(selectedTags)) {
      const want = selectedTags[cat]
      if (!want.size) continue
      if (!(ptags[cat] || []).some((n) => want.has(n))) return false
    }
    return true
  }

  // filtered：套用篩選，並把「找我附近」的距離合進每筆（不改原物件）
  const filtered = computed<Place[]>(() =>
    places.value.filter(passes).map((p) => {
      const d = distances.value[p.id]
      return d != null ? { ...p, distance_km: d } : p
    }),
  )

  // ── 互動：切換標籤 / 價位 ──────────────────────────────
  function toggleTag(cat: string, name: string) {
    const set = selectedTags[cat] || (selectedTags[cat] = reactive(new Set<string>()))
    if (set.has(name)) set.delete(name)
    else set.add(name)
  }
  function isTagOn(cat: string, name: string): boolean {
    return !!selectedTags[cat]?.has(name)
  }
  function togglePrice(lv: number) {
    if (priceLevels.has(lv)) priceLevels.delete(lv)
    else priceLevels.add(lv)
  }

  function clearFilters() {
    status.value = ''
    favOnly.value = false
    keyword.value = ''
    priceLevels.clear()
    for (const k of Object.keys(selectedTags)) delete selectedTags[k]
    distances.value = {}
  }

  function setDistances(rows: { id: number; distance_km?: number | null }[]) {
    const map: Record<number, number> = {}
    for (const r of rows) if (r.distance_km != null) map[r.id] = r.distance_km
    distances.value = map
  }

  return {
    // 資料
    places,
    filtered,
    tagDims,
    statsText,
    error,
    loaded,
    // 篩選狀態
    status,
    favOnly,
    keyword,
    priceLevels,
    // 動作
    loadStats,
    loadAll,
    toggleTag,
    isTagOn,
    togglePrice,
    clearFilters,
    setDistances,
  }
}
