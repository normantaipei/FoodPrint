// 在瀏覽器端按需載入 Leaflet + markercluster（OSM 圖磚，免 API key）。
// 走 CDN，不打包進 bundle，也避免 SSR 時 Leaflet 去碰 window。
// 只在 onMounted（client）呼叫；回傳全域的 L。

const CSS = [
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css',
  'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css',
]
const JS = [
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js',
]

function addCss(href: string) {
  if (document.querySelector(`link[href="${href}"]`)) return
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = href
  document.head.appendChild(link)
}

function addScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`) as HTMLScriptElement | null
    if (existing) {
      if (existing.dataset.loaded) resolve()
      else existing.addEventListener('load', () => resolve())
      return
    }
    const s = document.createElement('script')
    s.src = src
    s.async = false // 保留順序：markercluster 依賴 leaflet 先載入
    s.onload = () => {
      s.dataset.loaded = '1'
      resolve()
    }
    s.onerror = () => reject(new Error('載入失敗：' + src))
    document.head.appendChild(s)
  })
}

let loading: Promise<any> | null = null

export function loadLeaflet(): Promise<any> {
  if ((window as any).L?.markerClusterGroup) return Promise.resolve((window as any).L)
  if (loading) return loading
  loading = (async () => {
    CSS.forEach(addCss)
    for (const src of JS) await addScript(src) // 依序載入
    return (window as any).L
  })()
  return loading
}
