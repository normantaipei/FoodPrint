/* FoodPrint 地圖前端 — 純前端 SPA（Leaflet + OSM 圖磚，免 API key）。
 *
 * 資料來自自架 server 的唯讀端點，透過同源的 nginx 反向代理存取：
 *   GET /api/stats              庫狀態
 *   GET /api/search?...         店家粗篩（本前端一次撈完、之後純前端篩選）
 *   GET /api/nearby?lat=&lng=   「找我附近」
 *   GET /api/thumbs/<name>      縮圖
 * 代理會在 server 端注入讀取 token，所以瀏覽器端不持有 token、也沒有跨源問題。
 * 進階：可在載入本檔前設 window.FOODPRINT_API_BASE 改打其他位址。
 */
(function () {
  "use strict";

  var API = (window.FOODPRINT_API_BASE || "/api").replace(/\/$/, "");
  var PAGE = 100;            // /search 單頁上限
  var MAX_PLACES = 2000;     // 安全上限，避免無限撈
  var DEFAULT_CENTER = [25.0375, 121.5637]; // 台北 101 一帶
  var DEFAULT_ZOOM = 12;

  // category → 中文維度名（決定篩選面板的分組與排序）
  var DIM_LABELS = {
    cuisine: "料理", dish: "招牌", meal_type: "餐別", vibe: "氛圍",
    occasion: "場合", feature: "特色", district: "地區"
  };
  var DIM_ORDER = ["district", "cuisine", "dish", "meal_type", "vibe", "occasion", "feature"];

  var STATE = {
    places: [],            // 全部店家
    markers: {},           // id -> leaflet marker
    selectedTags: {},      // category -> Set(name)
    status: "",            // "" | want | visited
    favOnly: false,
    keyword: ""
  };

  var map, clusterLayer;

  // ── 工具 ──────────────────────────────────────────
  function el(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function api(path) {
    return fetch(API + path, { headers: { "Accept": "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(path + " → " + r.status);
      return r.json();
    });
  }
  function mediaUrl(rel) { return rel ? API + "/" + rel.replace(/^\//, "") : null; }
  function parseTag(t) { var i = t.indexOf(":"); return i < 0 ? ["", t] : [t.slice(0, i), t.slice(i + 1)]; }

  // ── 撈資料 ────────────────────────────────────────
  function loadStats() {
    return api("/stats").then(function (s) {
      el("stats").innerHTML =
        "<b>" + s.places + "</b> 間 · 想去 <b>" + s.want + "</b> · 吃過 <b>" + s.visited + "</b>";
    }).catch(function () { /* 無 token 或離線時靜默 */ });
  }

  function loadAll() {
    var acc = [];
    function page(offset) {
      return api("/search?limit=" + PAGE + "&offset=" + offset).then(function (rows) {
        acc = acc.concat(rows);
        if (rows.length === PAGE && acc.length < MAX_PLACES) return page(offset + PAGE);
        return acc;
      });
    }
    return page(0).then(function (rows) {
      STATE.places = rows;
      buildTagFilters();
      render();
      fitToData();
    });
  }

  // ── 篩選面板（由已載入資料推導，因為 server 無「列出所有 tag」端點）──
  function buildTagFilters() {
    var byDim = {};
    STATE.places.forEach(function (p) {
      (p.tags || []).forEach(function (t) {
        var pair = parseTag(t), cat = pair[0], name = pair[1];
        if (!cat) return;
        (byDim[cat] = byDim[cat] || {})[name] = (byDim[cat][name] || 0) + 1;
      });
    });
    var dims = Object.keys(byDim).sort(function (a, b) {
      var ia = DIM_ORDER.indexOf(a), ib = DIM_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    var html = dims.map(function (cat) {
      var names = Object.keys(byDim[cat]).sort(function (a, b) { return byDim[cat][b] - byDim[cat][a]; });
      var chips = names.map(function (n) {
        return '<span class="chip" data-cat="' + esc(cat) + '" data-name="' + esc(n) + '">' +
          esc(n) + "</span>";
      }).join("");
      return '<div class="tag-dim"><h4>' + esc(DIM_LABELS[cat] || cat) + "</h4>" +
        '<div class="chips">' + chips + "</div></div>";
    }).join("");
    el("tag-filters").innerHTML = html;

    el("tag-filters").querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var cat = chip.dataset.cat, name = chip.dataset.name;
        var set = STATE.selectedTags[cat] || (STATE.selectedTags[cat] = new Set());
        if (set.has(name)) { set.delete(name); chip.classList.remove("on"); }
        else { set.add(name); chip.classList.add("on"); }
        render();
      });
    });
  }

  // ── 篩選邏輯：維度間 AND、維度內 OR ──────────────────
  function passesFilters(p) {
    if (STATE.status && p.status !== STATE.status) return false;
    if (STATE.favOnly && !p.favorite) return false;

    if (STATE.keyword) {
      var hay = (p.name + " " + (p.address || "") + " " + (p.description || "") + " " +
        (p.tags || []).join(" ")).toLowerCase();
      var ok = STATE.keyword.toLowerCase().split(/\s+/).every(function (w) { return !w || hay.indexOf(w) >= 0; });
      if (!ok) return false;
    }

    var ptags = {};
    (p.tags || []).forEach(function (t) { var pr = parseTag(t); (ptags[pr[0]] = ptags[pr[0]] || []).push(pr[1]); });
    for (var cat in STATE.selectedTags) {
      var want = STATE.selectedTags[cat];
      if (!want.size) continue;
      var has = (ptags[cat] || []).some(function (n) { return want.has(n); });
      if (!has) return false;
    }
    return true;
  }

  // ── 渲染地圖 marker + 側欄清單 ───────────────────────
  function pinIcon(p) {
    var cls = p.status === "visited" ? "visited" : "want";
    var star = p.favorite ? '<span class="fav">★</span>' : "";
    return L.divIcon({ className: "", html: '<div class="pin ' + cls + '">' + star + "</div>", iconSize: [16, 16], iconAnchor: [8, 8] });
  }

  function cardHtml(p) {
    var thumb = mediaUrl(p.thumbnail_path);
    var badges = [];
    badges.push('<span class="badge ' + (p.status === "visited" ? "visited" : "want") + '">' +
      (p.status === "visited" ? "吃過" : "想去") + "</span>");
    if (p.rating) badges.push('<span class="badge rating">' + "★".repeat(p.rating) + "</span>");
    if (p.price_level) badges.push('<span class="badge price">' + "$".repeat(p.price_level) + "</span>");
    if (p.favorite) badges.push('<span class="badge rating">★ 最愛</span>');

    var tags = (p.tags || []).map(function (t) { return "<span>" + esc(parseTag(t)[1]) + "</span>"; }).join("");
    var q = (p.lat != null && p.lng != null) ? (p.lat + "," + p.lng) : encodeURIComponent(p.name);
    var gmap = "https://www.google.com/maps/search/?api=1&query=" + q;
    var dist = (p.distance_km != null) ? '<div class="dist">距離約 ' + p.distance_km + " km</div>" : "";

    return '<div class="card">' +
      (thumb ? '<img class="thumb" src="' + esc(thumb) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'" />' : "") +
      '<div class="pad">' +
        '<p class="title">' + esc(p.name) + "</p>" +
        '<div class="badges">' + badges.join("") + "</div>" +
        dist +
        (p.description ? '<div class="desc">' + esc(p.description) + "</div>" : "") +
        (p.address ? '<div class="addr">📍 ' + esc(p.address) + "</div>" : "") +
        (tags ? '<div class="tags">' + tags + "</div>" : "") +
        '<div class="links">' +
          '<a href="' + esc(gmap) + '" target="_blank" rel="noopener">Google Maps ↗</a>' +
          (p.source ? '<a href="' + esc(p.source) + '" target="_blank" rel="noopener">來源 ↗</a>' : "") +
        "</div>" +
      "</div></div>";
  }

  function render() {
    var rows = STATE.places.filter(passesFilters);

    // 地圖
    clusterLayer.clearLayers();
    STATE.markers = {};
    rows.forEach(function (p) {
      if (p.lat == null || p.lng == null) return;
      var m = L.marker([p.lat, p.lng], { icon: pinIcon(p) }).bindPopup(cardHtml(p), { maxWidth: 250 });
      STATE.markers[p.id] = m;
      clusterLayer.addLayer(m);
    });

    // 側欄清單
    var withGeo = rows.filter(function (p) { return p.lat != null && p.lng != null; }).length;
    el("list-count").textContent = rows.length + " 間" + (withGeo < rows.length ? "（" + (rows.length - withGeo) + " 間無座標）" : "");
    var list = el("list");
    if (!rows.length) {
      list.innerHTML = '<li class="empty">' + (STATE.places.length ? "沒有符合篩選的店家" : "口袋名單還是空的——去 Claude 對話裡貼連結 / 照片入庫吧 🍜") + "</li>";
      return;
    }
    list.innerHTML = rows.map(function (p) {
      var meta = [p.address, (p.tags || []).slice(0, 3).map(function (t) { return parseTag(t)[1]; }).join(" · ")]
        .filter(Boolean).join(" — ");
      return '<li class="item" data-id="' + p.id + '">' +
        '<span class="dot ' + (p.status === "visited" ? "visited" : "want") + '"></span>' +
        '<div class="body"><div class="name">' + (p.favorite ? "★ " : "") + esc(p.name) + "</div>" +
        '<div class="meta">' + esc(meta || "無地址") + "</div></div></li>";
    }).join("");
    list.querySelectorAll(".item").forEach(function (li) {
      li.addEventListener("click", function () { focusPlace(+li.dataset.id); });
    });
  }

  function focusPlace(id) {
    var p = STATE.places.filter(function (x) { return x.id === id; })[0];
    if (!p || p.lat == null) return;
    closeSidebarOnMobile();
    map.flyTo([p.lat, p.lng], Math.max(map.getZoom(), 16), { duration: 0.6 });
    var m = STATE.markers[id];
    if (m) clusterLayer.zoomToShowLayer(m, function () { m.openPopup(); });
  }

  function fitToData() {
    var pts = STATE.places.filter(function (p) { return p.lat != null && p.lng != null; })
      .map(function (p) { return [p.lat, p.lng]; });
    if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 16 });
  }

  // ── 找我附近 ──────────────────────────────────────
  function locateMe() {
    if (!navigator.geolocation) { alert("此裝置不支援定位"); return; }
    navigator.geolocation.getCurrentPosition(function (pos) {
      var lat = pos.coords.latitude, lng = pos.coords.longitude;
      L.circleMarker([lat, lng], { radius: 7, color: "#1a73e8", fillColor: "#1a73e8", fillOpacity: 1, weight: 2 })
        .addTo(map).bindPopup("你在這裡").openPopup();
      map.flyTo([lat, lng], 15, { duration: 0.6 });
      api("/nearby?lat=" + lat + "&lng=" + lng + "&radius_km=3&limit=50").then(function (rows) {
        // 用 distance_km 標注已載入的店家（附近端點回傳的子集）
        var dmap = {}; rows.forEach(function (r) { dmap[r.id] = r.distance_km; });
        STATE.places.forEach(function (p) { p.distance_km = dmap[p.id]; });
        render();
      }).catch(function () {});
    }, function () { alert("無法取得定位（請允許瀏覽器定位權限）"); }, { enableHighAccuracy: true, timeout: 8000 });
  }

  // ── 側欄（手機）────────────────────────────────────
  function closeSidebarOnMobile() { if (window.innerWidth <= 760) el("sidebar").classList.remove("open"); }

  // ── 啟動 ──────────────────────────────────────────
  function init() {
    map = L.map("map", { zoomControl: true }).setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);

    clusterLayer = (typeof L.markerClusterGroup === "function")
      ? L.markerClusterGroup({ maxClusterRadius: 45, spiderfyOnMaxZoom: true })
      : L.layerGroup();
    map.addLayer(clusterLayer);
    if (!clusterLayer.zoomToShowLayer) clusterLayer.zoomToShowLayer = function (m, cb) { cb && cb(); };

    // 事件
    var kwTimer;
    el("kw").addEventListener("input", function (e) {
      clearTimeout(kwTimer);
      kwTimer = setTimeout(function () { STATE.keyword = e.target.value.trim(); render(); }, 180);
    });
    document.querySelectorAll('input[name="status"]').forEach(function (r) {
      r.addEventListener("change", function () { STATE.status = r.value; render(); });
    });
    el("fav-only").addEventListener("change", function (e) { STATE.favOnly = e.target.checked; render(); });
    el("locate").addEventListener("click", locateMe);
    el("menu-toggle").addEventListener("click", function () { el("sidebar").classList.toggle("open"); });
    el("clear-filters").addEventListener("click", function () {
      STATE.selectedTags = {}; STATE.status = ""; STATE.favOnly = false; STATE.keyword = "";
      el("kw").value = ""; el("fav-only").checked = false;
      document.querySelector('input[name="status"][value=""]').checked = true;
      el("tag-filters").querySelectorAll(".chip.on").forEach(function (c) { c.classList.remove("on"); });
      STATE.places.forEach(function (p) { delete p.distance_km; });
      render();
    });

    loadStats();
    loadAll().catch(function (err) {
      el("list").innerHTML = '<li class="empty">連不到後端 😵<br>請確認 server 已啟動、token 已設定。<br><small>' + esc(err.message) + "</small></li>";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
