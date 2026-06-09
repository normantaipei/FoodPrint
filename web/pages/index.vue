<script setup lang="ts">
// 美食口袋地圖主頁：頂列搜尋 + 側欄篩選/清單 + Leaflet 地圖。
// 資料走 Nuxt 代理（/api/*，token 不外洩）；地圖在瀏覽器端（onMounted）初始化。
import { cardHtml, DIM_LABELS, type Place } from '~/utils/place'
import { loadLeaflet } from '~/utils/leaflet'

const {
  places,
  filtered,
  tagDims,
  statsText,
  error,
  loaded,
  status,
  favOnly,
  keyword,
  priceLevels,
  loadStats,
  loadAll,
  toggleTag,
  isTagOn,
  togglePrice,
  clearFilters,
  setDistances,
} = usePlaces()

const DEFAULT_CENTER: [number, number] = [25.0375, 121.5637] // 台北 101 一帶
const DEFAULT_ZOOM = 12

const sidebarOpen = ref(false)
const mapEl = ref<HTMLElement | null>(null)

// Leaflet 物件用「非響應式」變數持有（別讓 Vue proxy 包住，避免效能/相容問題）
let L: any = null
let map: any = null
let clusterLayer: any = null
let markers: Record<number, any> = {}
let didFit = false

const listCount = computed(() => {
  const rows = filtered.value
  const withGeo = rows.filter((p) => p.lat != null && p.lng != null).length
  return rows.length + ' 間' + (withGeo < rows.length ? `（${rows.length - withGeo} 間無座標）` : '')
})

function pinIcon(p: Place) {
  const cls = p.status === 'visited' ? 'visited' : 'want'
  const star = p.favorite ? '<span class="fav">★</span>' : ''
  return L.divIcon({
    className: '',
    html: `<div class="pin ${cls}">${star}</div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  })
}

function renderMarkers() {
  if (!clusterLayer) return
  clusterLayer.clearLayers()
  markers = {}
  for (const p of filtered.value) {
    if (p.lat == null || p.lng == null) continue
    const m = L.marker([p.lat, p.lng], { icon: pinIcon(p) }).bindPopup(cardHtml(p), {
      maxWidth: 250,
    })
    markers[p.id] = m
    clusterLayer.addLayer(m)
  }
}

function fitToData() {
  const pts = filtered.value
    .filter((p) => p.lat != null && p.lng != null)
    .map((p) => [p.lat, p.lng] as [number, number])
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 16 })
}

function focusPlace(id: number) {
  const p = filtered.value.find((x) => x.id === id)
  if (!p || p.lat == null || p.lng == null) return
  if (window.innerWidth <= 760) sidebarOpen.value = false
  map.flyTo([p.lat, p.lng], Math.max(map.getZoom(), 16), { duration: 0.6 })
  const m = markers[id]
  if (m) {
    if (clusterLayer.zoomToShowLayer) clusterLayer.zoomToShowLayer(m, () => m.openPopup())
    else m.openPopup()
  }
}

function locateMe() {
  if (!navigator.geolocation) {
    alert('此裝置不支援定位')
    return
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const lat = pos.coords.latitude,
        lng = pos.coords.longitude
      L.circleMarker([lat, lng], {
        radius: 7,
        color: '#1a73e8',
        fillColor: '#1a73e8',
        fillOpacity: 1,
        weight: 2,
      })
        .addTo(map)
        .bindPopup('你在這裡')
        .openPopup()
      map.flyTo([lat, lng], 15, { duration: 0.6 })
      $fetch<Place[]>('/api/nearby', {
        params: { lat, lng, radius_km: 3, limit: 50 },
      })
        .then((rows) => setDistances(rows))
        .catch(() => {})
    },
    () => alert('無法取得定位（請允許瀏覽器定位權限）'),
    { enableHighAccuracy: true, timeout: 8000 },
  )
}

onMounted(async () => {
  try {
    L = await loadLeaflet()
  } catch {
    error.value = '地圖元件載入失敗（請確認可連到 unpkg CDN）'
    return
  }
  map = L.map(mapEl.value, { zoomControl: true }).setView(DEFAULT_CENTER, DEFAULT_ZOOM)
  // 簡約淺色底圖（CartoDB Positron）：弱化路網/POI，店家 pin 更突出
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 20,
    subdomains: 'abcd',
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
  }).addTo(map)
  clusterLayer =
    typeof L.markerClusterGroup === 'function'
      ? L.markerClusterGroup({ maxClusterRadius: 45, spiderfyOnMaxZoom: true })
      : L.layerGroup()
  map.addLayer(clusterLayer)

  // filtered 變動就重畫 marker；首次載入完成把視野貼齊資料
  watch(filtered, renderMarkers, { immediate: true })
  watch(
    loaded,
    (ok) => {
      if (ok && !didFit) {
        didFit = true
        fitToData()
      }
    },
    { immediate: true },
  )

  loadStats()
  loadAll()
})

onBeforeUnmount(() => {
  if (map) map.remove()
})
</script>

<template>
  <div id="app">
    <header id="topbar">
      <button
        id="menu-toggle"
        class="icon-btn"
        title="篩選 / 清單"
        aria-label="開關側欄"
        @click="sidebarOpen = !sidebarOpen"
      >
        ☰
      </button>
      <h1>🍜 FoodPrint</h1>
      <div class="stats" v-html="statsText ? statsText.replace(/(\d+)/g, '<b>$1</b>') : ''" />
      <div class="search-wrap">
        <input
          v-model.trim="keyword"
          type="search"
          placeholder="搜尋店名 / 地址 / 標籤…"
          autocomplete="off"
        />
      </div>
      <button class="icon-btn" title="找我附近" aria-label="找我附近" @click="locateMe">📍</button>
    </header>

    <aside id="sidebar" :class="{ open: sidebarOpen }">
      <div class="filters">
        <div class="filter-row status-row">
          <label class="seg"
            ><input v-model="status" type="radio" value="" /><span>全部</span></label
          >
          <label class="seg"
            ><input v-model="status" type="radio" value="want" /><span>想去</span></label
          >
          <label class="seg"
            ><input v-model="status" type="radio" value="visited" /><span>吃過</span></label
          >
        </div>
        <div class="filter-row">
          <label class="check"><input v-model="favOnly" type="checkbox" /> 只看 ★ 最愛</label>
        </div>
        <div class="tag-dim">
          <h4>價位</h4>
          <div class="chips">
            <span
              v-for="lv in [1, 2, 3, 4]"
              :key="lv"
              class="chip"
              :class="{ on: priceLevels.has(lv) }"
              @click="togglePrice(lv)"
              >{{ '$'.repeat(lv) }}</span
            >
          </div>
        </div>
        <div v-for="dim in tagDims" :key="dim.cat" class="tag-dim">
          <h4>{{ DIM_LABELS[dim.cat] || dim.cat }}</h4>
          <div class="chips">
            <span
              v-for="n in dim.names"
              :key="n.name"
              class="chip"
              :class="{ on: isTagOn(dim.cat, n.name) }"
              @click="toggleTag(dim.cat, n.name)"
              >{{ n.name }}</span
            >
          </div>
        </div>
      </div>
      <div class="list-head">
        <span>{{ listCount }}</span>
        <button class="link-btn" @click="clearFilters">清除篩選</button>
      </div>
      <ul id="list">
        <li v-if="error" class="empty">
          連不到後端 😵<br />請確認 server 已啟動、token 已設定。<br /><small>{{ error }}</small>
        </li>
        <li v-else-if="!filtered.length" class="empty">
          {{
            places?.length
              ? '沒有符合篩選的店家'
              : '口袋名單還是空的——去 Claude 對話裡貼連結 / 照片入庫吧 🍜'
          }}
        </li>
        <li v-for="p in filtered" v-else :key="p.id" class="item" @click="focusPlace(p.id)">
          <span class="dot" :class="p.status === 'visited' ? 'visited' : 'want'" />
          <div class="body">
            <div class="name">{{ p.favorite ? '★ ' : '' }}{{ p.name }}</div>
            <div class="meta">
              {{
                [
                  p.address,
                  (p.tags || [])
                    .slice(0, 3)
                    .map((t) => t.split(':').slice(1).join(':'))
                    .join(' · '),
                ]
                  .filter(Boolean)
                  .join(' — ') || '無地址'
              }}
            </div>
          </div>
        </li>
      </ul>
    </aside>

    <main id="map" ref="mapEl" />
  </div>
</template>
