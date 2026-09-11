const $ = (id) => document.getElementById(id);

function dbg(hypothesisId, location, message, data) {
  // #region agent log
  fetch("http://127.0.0.1:7916/ingest/9c91a5b2-77cd-4844-bfa2-c3efbfb401ba", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "c31723" },
    body: JSON.stringify({ sessionId: "c31723", hypothesisId, location, message, data, timestamp: Date.now(), runId: "post-fix" }),
  }).catch(() => {});
  // #endregion
}

const livePill = $("live-pill");
const liveLabel = $("live-label");
const toggleBtn = $("toggle");
const checkBtn = $("check");
const testBtn = $("test");
const errorEl = $("error");

let hitsMap = null;
let hitsLayer = null;
let hitsPins = [];
let lastMapKey = "";
let statusCache = { monitors: [], templates: [] };
let filterCatalog = null;
let filterState = {};
let filterSuggestedName = "";
let templateState = null;
let variables = [];
let sampleVars = {};
let lastInserted = null;
let discordPoll = null;

function formatTime(iso) {
  if (!iso) return "ještě ne";
  return new Date(iso).toLocaleString("cs-CZ", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "numeric",
    month: "numeric",
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const carousels = new Map();
const CAROUSEL_GAP = 16;

function uniqueListings(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    const key = item.listing_key || item.url || `${item.monitor_id}:${item.id}`;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function localDayStartMs() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return start.getTime();
}

function hitsFromToday(status) {
  const fromApi = uniqueListings(status.recent_today || []);
  if (fromApi.length) return fromApi;
  const start = localDayStartMs();
  return uniqueListings(status.recent || []).filter((item) => {
    const stamp = Date.parse(item.hit_at || item.first_seen || "");
    return Number.isFinite(stamp) && stamp >= start;
  });
}

function fillCarousel(id, emptyId, items) {
  const viewport = $(id);
  const empty = $(emptyId);
  const list = uniqueListings(items);
  if (!viewport) return;
  let track = viewport.querySelector(".carousel-track");
  if (!track) {
    track = document.createElement("div");
    track.className = "carousel-track";
    viewport.replaceChildren(track);
    bindCarousel(viewport);
  }
  track.innerHTML = list.map((item) => listingCardHtml(item)).join("");
  const state = carousels.get(id) || {};
  state.page = 0;
  state.dragX = 0;
  carousels.set(id, state);
  viewport.hidden = list.length === 0;
  if (empty) empty.hidden = list.length > 0;
  if (list.length) layoutCarousel(viewport);
  else {
    const pager = viewport.parentElement?.querySelector(".carousel-pager");
    if (pager) pager.hidden = true;
  }
}

function carouselPerPage(viewport) {
  const width = viewport.clientWidth;
  if (width < 620) return 1;
  if (width < 960) return 2;
  return 3;
}

function layoutCarousel(viewport) {
  const track = viewport.querySelector(".carousel-track");
  const state = carousels.get(viewport.id);
  if (!track || !state) return;
  const cards = [...track.children];
  const per = Math.max(1, carouselPerPage(viewport));
  const pages = Math.max(1, Math.ceil(cards.length / per) || 1);
  state.per = per;
  state.pages = pages;
  state.page = Math.min(state.page || 0, pages - 1);
  const cardW = cards.length ? Math.max(180, (viewport.clientWidth - CAROUSEL_GAP * (per - 1)) / per) : 0;
  cards.forEach((card) => {
    card.style.flex = `0 0 ${cardW}px`;
    card.style.width = `${cardW}px`;
    card.style.minWidth = `${cardW}px`;
  });
  applyCarousel(viewport, false);
  renderCarouselPager(viewport);
}

function applyCarousel(viewport, animate = true) {
  const track = viewport.querySelector(".carousel-track");
  const state = carousels.get(viewport.id);
  if (!track || !state) return;
  const pageWidth = viewport.clientWidth + CAROUSEL_GAP;
  const x = -(state.page || 0) * pageWidth + (state.dragX || 0);
  track.style.transition = animate && !state.dragX ? "transform 0.35s ease" : "none";
  track.style.transform = `translate3d(${x}px, 0, 0)`;
}

function renderCarouselPager(viewport) {
  const pager = viewport.parentElement?.querySelector(".carousel-pager");
  const state = carousels.get(viewport.id);
  if (!pager || !state) return;
  const pages = state.pages || 1;
  const page = state.page || 0;
  pager.hidden = viewport.hidden || pages <= 1;
  pager.innerHTML = `
    <button type="button" class="carousel-nav" data-dir="-1" aria-label="Předchozí" ${page === 0 ? "disabled" : ""}>‹</button>
    <div class="carousel-dots">
      ${Array.from({ length: pages }, (_, index) =>
        `<button type="button" class="carousel-dot${index === page ? " on" : ""}" data-page="${index}" aria-label="Strana ${index + 1}"></button>`,
      ).join("")}
    </div>
    <button type="button" class="carousel-nav" data-dir="1" aria-label="Další" ${page >= pages - 1 ? "disabled" : ""}>›</button>
  `;
}

function goCarouselPage(viewport, page) {
  const state = carousels.get(viewport.id);
  if (!state) return;
  state.page = Math.max(0, Math.min((state.pages || 1) - 1, page));
  state.dragX = 0;
  applyCarousel(viewport, true);
  renderCarouselPager(viewport);
}

function bindCarousel(viewport) {
  if (viewport.dataset.bound) return;
  viewport.dataset.bound = "1";
  let startX = 0;
  let startY = 0;
  let dragging = false;
  let axis = null;
  const finish = () => {
    const state = carousels.get(viewport.id);
    dragging = false;
    axis = null;
    viewport.classList.remove("is-dragging");
    if (!state) return;
    const dx = state.dragX || 0;
    const thresh = Math.min(90, viewport.clientWidth * 0.18);
    if (dx < -thresh) goCarouselPage(viewport, (state.page || 0) + 1);
    else if (dx > thresh) goCarouselPage(viewport, (state.page || 0) - 1);
    else {
      state.dragX = 0;
      applyCarousel(viewport, true);
    }
  };
  viewport.addEventListener("pointerdown", (event) => {
    if (event.button != null && event.button !== 0) return;
    const state = carousels.get(viewport.id);
    if (!state || (state.pages || 1) <= 1) return;
    dragging = true;
    axis = null;
    startX = event.clientX;
    startY = event.clientY;
    state.dragX = 0;
    state.moved = false;
    viewport.classList.add("is-dragging");
    viewport.setPointerCapture?.(event.pointerId);
    applyCarousel(viewport, false);
  });
  viewport.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (!axis) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      axis = Math.abs(dx) >= Math.abs(dy) ? "x" : "y";
      if (axis === "y") {
        dragging = false;
        viewport.classList.remove("is-dragging");
        const state = carousels.get(viewport.id);
        if (state) state.dragX = 0;
        applyCarousel(viewport, true);
        return;
      }
    }
    if (axis !== "x") return;
    event.preventDefault();
    const state = carousels.get(viewport.id);
    if (!state) return;
    state.moved = Math.abs(dx) > 8;
    const atStart = (state.page || 0) === 0 && dx > 0;
    const atEnd = (state.page || 0) >= (state.pages || 1) - 1 && dx < 0;
    state.dragX = atStart || atEnd ? dx * 0.35 : dx;
    applyCarousel(viewport, false);
  });
  viewport.addEventListener("pointerup", finish);
  viewport.addEventListener("pointercancel", finish);
  viewport.addEventListener(
    "click",
    (event) => {
      const state = carousels.get(viewport.id);
      if (state?.moved) {
        event.preventDefault();
        event.stopPropagation();
        state.moved = false;
      }
    },
    true,
  );
  viewport.parentElement?.querySelector(".carousel-pager")?.addEventListener("click", (event) => {
    const dir = event.target.closest("[data-dir]");
    const dot = event.target.closest("[data-page]");
    const state = carousels.get(viewport.id);
    if (!state) return;
    if (dir) goCarouselPage(viewport, (state.page || 0) + Number(dir.dataset.dir));
    else if (dot) goCarouselPage(viewport, Number(dot.dataset.page));
  });
}

window.addEventListener("resize", () => {
  for (const id of carousels.keys()) {
    const viewport = $(id);
    if (viewport) layoutCarousel(viewport);
  }
});

function listingCardHtml(item, { compact = false } = {}) {
  const photo = item.image_url || item.photos?.[0] || "";
  const area = item.area_m2 ? `${item.area_m2} m²` : "rozloha neuvedena";
  const kindLabel = { changed: "Změna", refresh: "Obnoveno", new: "Nový" }[item.last_kind] || "Nový";
  const monitors = (item.monitors || []).map((row) => row.name).join(" · ") || item.monitor_name || "";
  const discount = item.discount_czk ? ` · −${Number(item.discount_czk).toLocaleString("cs-CZ")} Kč` : "";
  const meta = compact
    ? `${escapeHtml(item.disposition || "—")} · ${escapeHtml(area)}<br />${escapeHtml(item.locality || "")}${
        item.discount_czk ? `<br />−${Number(item.discount_czk).toLocaleString("cs-CZ")} Kč` : ""
      }`
    : `${escapeHtml(monitors)} · ${escapeHtml(kindLabel)}${discount} · ${escapeHtml(item.disposition || "—")} · ${escapeHtml(area)}<br />${escapeHtml(item.locality || "")}`;
  return `
    <a class="listing" href="${escapeHtml(item.url || "#")}" target="_blank" rel="noreferrer">
      ${photo ? `<img src="${escapeHtml(photo)}" alt="" />` : `<div class="listing-photo-empty" aria-hidden="true"></div>`}
      <div class="listing-body">
        <h3>${escapeHtml(item.name || item.locality || "Nabídka")}</h3>
        <div class="price">${escapeHtml(item.price_label || "")}</div>
        <div class="meta">${meta}</div>
      </div>
    </a>
  `;
}

function setBusy(busy) {
  [toggleBtn, checkBtn, testBtn].forEach((btn) => {
    if (btn) btn.disabled = busy;
  });
}

function toast(message, kind = "ok") {
  const host = $("toasts");
  if (!host || !message) return;
  const item = document.createElement("div");
  item.className = `toast toast-${kind}`;
  item.innerHTML = `<span class="toast-dot"></span><p>${escapeHtml(message)}</p>`;
  host.appendChild(item);
  requestAnimationFrame(() => item.classList.add("in"));
  const hide = () => {
    item.classList.remove("in");
    setTimeout(() => item.remove(), 240);
  };
  const timer = setTimeout(hide, kind === "error" || message.length > 80 ? 7000 : 3200);
  item.addEventListener("click", () => {
    clearTimeout(timer);
    hide();
  });
}

const ROUTES = {
  "/prehled": "overview",
  "/nabidka": "catalog",
  "/nastaveni": "settings",
  "/nastaveni/profil": "settings",
  "/nastaveni/profily": "settings",
  "/nastaveni/notifikace": "settings",
  "/nastaveni/predplatne": "settings",
  "/nastaveni/bezpecnost": "settings",
  "/monitory": "settings",
  "/filtry": "settings",
  "/zprava": "settings",
};

const PAGE_TITLES = {
  overview: "Přehled",
  catalog: "Nabídka",
  settings: "Nastavení",
};

const SETTINGS_PANELS = {
  profile: { title: "Nastavení účtu", path: "/nastaveni" },
  watch: { title: "Hlídací profily", path: "/nastaveni/profily" },
  notify: { title: "Notifikace", path: "/nastaveni/notifikace" },
  billing: { title: "Předplatné", path: "/nastaveni/predplatne" },
  security: { title: "Bezpečnost", path: "/nastaveni/bezpecnost" },
};

function currentPath() {
  return location.pathname.replace(/\/$/, "") || "/prehled";
}

function currentPage() {
  const path = currentPath();
  if (path.startsWith("/nastaveni") || path === "/monitory" || path === "/filtry" || path === "/zprava") return "settings";
  return ROUTES[path] || "overview";
}

function settingsPanel() {
  const path = currentPath();
  if (path === "/nastaveni/notifikace") return "notify";
  if (path === "/nastaveni/predplatne") return "billing";
  if (path === "/nastaveni/bezpecnost") return "security";
  if (path === "/nastaveni/profily" || path === "/monitory" || path === "/filtry" || path === "/zprava") return "watch";
  return "profile";
}

function canonicalizeSettingsPath() {
  const path = currentPath();
  const map = {
    "/monitory": "/nastaveni/profily",
    "/filtry": "/nastaveni/profily",
    "/zprava": "/nastaveni/profily",
    "/nastaveni/profil": "/nastaveni",
  };
  const next = map[path];
  if (!next) return;
  history.replaceState(null, "", `${next}${location.search}`);
}

function applySettingsPanel() {
  const panel = settingsPanel();
  document.querySelectorAll(".set-nav[data-panel]").forEach((link) => {
    link.classList.toggle("on", link.dataset.panel === panel);
  });
  document.querySelectorAll(".set-panel").forEach((el) => {
    el.hidden = el.id !== `panel-${panel}`;
  });
  const meta = SETTINGS_PANELS[panel];
  document.title = `${meta?.title || "Nastavení"} · Sreality monitor`;
  if (panel === "billing") loadBillingInvoices();
  if (panel === "notify") {
    loadDiscordLink().then((data) => {
      if (data?.bot_ready && !data.linked) startDiscordPoll();
    });
    updateEmailHint();
  } else {
    stopDiscordPoll();
  }
}

function isAppPath(path) {
  const normalized = (path || "").replace(/\/$/, "") || "/";
  return Boolean(ROUTES[normalized]) || normalized.startsWith("/nastaveni");
}

function navigateApp(next, { replace = false } = {}) {
  const url = new URL(next, location.origin);
  const href = `${url.pathname}${url.search}${url.hash}`;
  const current = `${location.pathname}${location.search}${location.hash}`;
  if (href !== current) {
    history[replace ? "replaceState" : "pushState"](null, "", href);
  }
  applyRoute();
}

function applyRoute() {
  canonicalizeSettingsPath();
  const page = currentPage();
  document.documentElement.dataset.page = page;
  document.body.classList.toggle("is-settings", page === "settings");
  document.querySelectorAll(".nav-tabs .tab").forEach((link) => {
    const href = link.getAttribute("href");
    const on = href === "/prehled" ? page === "overview" : href === "/nabidka" ? page === "catalog" : page === "settings";
    link.classList.toggle("on", on);
  });
  ["overview", "catalog", "settings"].forEach((name) => {
    const view = $(`view-${name}`);
    if (view) view.hidden = name !== page;
  });
  if (page === "settings") applySettingsPanel();
  else document.title = `${PAGE_TITLES[page]} · Sreality monitor`;
  if (page === "overview") {
    setTimeout(() => {
      hitsMap?.invalidateSize();
      for (const id of carousels.keys()) {
        const viewport = $(id);
        if (viewport && !viewport.hidden) layoutCarousel(viewport);
      }
    }, 80);
  }
  if (page === "catalog") window.dispatchEvent(new Event("catalog-show"));
}

applyRoute();

document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest("a[href]");
  if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
  let url;
  try {
    url = new URL(link.getAttribute("href"), location.origin);
  } catch {
    return;
  }
  if (url.origin !== location.origin || !isAppPath(url.pathname)) return;
  event.preventDefault();
  navigateApp(`${url.pathname}${url.search}${url.hash}`);
});

window.addEventListener("popstate", () => applyRoute());

function renderStatus(status) {
  const started = performance.now();
  statusCache = status;
  const running = Boolean(status.running);
  livePill.dataset.state = status.last_error ? "error" : running ? "on" : "off";
  liveLabel.textContent = status.last_error ? "Chyba" : running ? "Hlídám" : "Zastaveno";
  setLabeled(toggleBtn, running ? "Zastavit hlídání" : "Spustit hlídání", running ? "pause" : "play");
  $("new-today").textContent = status.new_today ?? 0;
  $("tracked").textContent = status.tracked ?? 0;
  $("total").textContent = status.search_total || "–";
  $("last-check").textContent = formatTime(status.last_check);
  $("interval").textContent = `${status.interval_sec} s`;
  if (status.version) {
    if ($("app-version")) $("app-version").textContent = `v${status.version}`;
    if ($("footer-version")) $("footer-version").textContent = `v${status.version}`;
  }
  $("mint").textContent = status.seeded
    ? "Monitory berou unikátní hledání. Nové byty jdou na Discord, katalog se doplňuje denně."
    : "Nový monitor se nasadí z katalogu, bez stahování celé nabídky.";
  if (status.last_error) {
    errorEl.hidden = false;
    errorEl.textContent = status.last_error;
  } else {
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  fillCarousel("carousel-today", "empty-today", hitsFromToday(status));
  fillCarousel("carousel-all", "empty-all", status.recent || []);
  renderMap(uniqueListings(status.recent || []));
  renderMonitors(status.monitors || []);
  renderBilling(status.billing);
  renderCatalogSync(status.catalog_sync, status.catalog_running);
  fillTemplateSelects(status.templates || []);
  // #region agent log
  dbg("D", "app.js:renderStatus", "renderStatus done", {
    ms: Math.round(performance.now() - started),
    monitors: (status.monitors || []).length,
    recent: (status.recent || []).length,
    page: location.pathname,
  });
  // #endregion
}

function pinPrice(item) {
  if (item.price_czk != null && Number.isFinite(Number(item.price_czk))) {
    return `${Number(item.price_czk).toLocaleString("cs-CZ")} Kč`;
  }
  return String(item.price_label || "Cena")
    .replace(/\s*\/\s*měsíc.*/i, "")
    .replace(/\s*\(.*/, "")
    .trim() || "Cena";
}

function clusterCell(zoom) {
  if (zoom >= 16) return 0;
  if (zoom >= 14) return 0.004;
  if (zoom >= 13) return 0.008;
  if (zoom >= 12) return 0.016;
  if (zoom >= 11) return 0.03;
  return 0.06;
}

function groupedPins(items, zoom) {
  const cell = clusterCell(zoom);
  if (!cell) return items.map((item) => ({ items: [item], lat: item.lat, lon: item.lon }));
  const groups = new Map();
  for (const item of items) {
    const key = `${Math.round(item.lat / cell)}:${Math.round(item.lon / cell)}`;
    const group = groups.get(key) || { items: [], lat: 0, lon: 0 };
    group.items.push(item);
    group.lat += item.lat;
    group.lon += item.lon;
    groups.set(key, group);
  }
  return [...groups.values()].map((group) => ({
    items: group.items,
    lat: group.lat / group.items.length,
    lon: group.lon / group.items.length,
  }));
}

function ensureMap() {
  if (hitsMap || typeof L === "undefined") return;
  hitsMap = L.map("hits-map", { scrollWheelZoom: false, attributionControl: false }).setView([50.08, 14.44], 12);
  L.maplibreGL({
    style: "https://tiles.openfreemap.org/styles/liberty",
  }).addTo(hitsMap);
  hitsLayer = L.layerGroup().addTo(hitsMap);
  hitsMap.on("zoomend", () => drawHitsPins(false));
}

function hitMarker(item) {
  const pin = L.divIcon({
    className: "price-pin-wrap",
    html: `<div class="price-pin">${escapeHtml(pinPrice(item))}</div>`,
    iconSize: [88, 32],
    iconAnchor: [44, 16],
    popupAnchor: [0, -18],
  });
  const maps = item.maps_url
    ? `<a class="map-popup-link" href="${item.maps_url}" target="_blank" rel="noreferrer">Google Maps</a>`
    : "";
  const portal = String(item.url || "").includes("bezrealitky") ? "Bezrealitky" : "Sreality";
  const marker = L.marker([item.lat, item.lon], { icon: pin, riseOnHover: true });
  marker.bindPopup(
    `<div class="map-popup-card">${item.image_url ? `<img class="map-popup-photo" src="${item.image_url}" alt="" />` : ""}<div class="map-popup-body"><p class="map-popup-price">${escapeHtml(item.price_label || "")}</p><p class="map-popup-name">${escapeHtml(item.name)}</p><p class="map-popup-place">${escapeHtml(item.locality || "")}</p><div class="map-popup-links"><a class="map-popup-link" href="${item.url}" target="_blank" rel="noreferrer">${portal}</a>${maps}</div></div></div>`,
    { className: "map-popup", maxWidth: 280, minWidth: 240 },
  );
  marker.on("popupopen", () => marker.getElement()?.classList.add("is-open"));
  marker.on("popupclose", () => marker.getElement()?.classList.remove("is-open"));
  return marker;
}

function drawHitsPins(fit) {
  if (!hitsMap || !hitsLayer) return;
  const mappable = hitsPins.filter((item) => item.lat != null && item.lon != null);
  hitsLayer.clearLayers();
  const groups = groupedPins(mappable, hitsMap.getZoom());
  const points = [];
  for (const group of groups) {
    if (group.items.length === 1) {
      hitsLayer.addLayer(hitMarker(group.items[0]));
    } else {
      const size = group.items.length > 99 ? 48 : group.items.length > 9 ? 42 : 36;
      const marker = L.marker([group.lat, group.lon], {
        icon: L.divIcon({
          className: "price-cluster-wrap",
          html: `<div class="price-cluster">${group.items.length}</div>`,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
      });
      marker.on("click", () => hitsMap.setView([group.lat, group.lon], Math.min(hitsMap.getZoom() + 2, 16)));
      hitsLayer.addLayer(marker);
    }
    points.push([group.lat, group.lon]);
  }
  if (fit) {
    if (points.length === 1) hitsMap.setView(points[0], 14);
    else if (points.length > 1) hitsMap.fitBounds(points, { padding: [48, 48], maxZoom: 14 });
  }
  setTimeout(() => hitsMap.invalidateSize(), 80);
}

function renderMap(items) {
  ensureMap();
  if (!hitsMap || !hitsLayer) return;
  const mappable = items.filter((item) => item.lat != null && item.lon != null);
  const nextKey = mappable.map((item) => `${item.id}:${item.lat}:${item.lon}:${item.image_url || ""}`).join("|");
  hitsPins = mappable;
  if (nextKey === lastMapKey) {
    setTimeout(() => hitsMap.invalidateSize(), 80);
    return;
  }
  lastMapKey = nextKey;
  drawHitsPins(true);
}

function portalLabel(url) {
  return String(url || "").includes("bezrealitky") ? "Bezrealitky" : "Sreality";
}

function portalIcon(url) {
  return String(url || "").includes("bezrealitky")
    ? "/static/icons/bezrealitky.svg"
    : "/static/icons/sreality.svg";
}

let editingMonitorId = null;
let editingDraft = null;

function templateOptions(selected) {
  return (statusCache.templates || [])
    .map((row) => `<option value="${escapeHtml(row.id)}"${row.id === selected ? " selected" : ""}>${escapeHtml(row.name)}</option>`)
    .join("");
}

function templateName(id) {
  const key = id || "default";
  return (statusCache.templates || []).find((row) => row.id === key)?.name || key;
}

function normalizeMonitorPortals(value) {
  const raw = String(value || "all").toLowerCase();
  return raw === "sreality" || raw === "bezrealitky" ? raw : "all";
}

function portalTitle(portal) {
  return portal === "bezrealitky" ? "Bezrealitky" : "Sreality";
}

function renderPortalUrlStack(host, targets, primary) {
  if (!host) return;
  const rows = (targets || [])
    .map((item) => ({ portal: item.portal, url: item.search_url || item.url || "" }))
    .filter((item) => item.url);
  if (!rows.length && primary) {
    rows.push({
      portal: String(primary).includes("bezrealitky") ? "bezrealitky" : "sreality",
      url: primary,
    });
  }
  host.innerHTML = rows
    .map(
      (item) =>
        `<label>${escapeHtml(portalTitle(item.portal))}<textarea rows="3" readonly>${escapeHtml(item.url)}</textarea></label>`,
    )
    .join("");
}

function portalsSummary(value) {
  const portals = normalizeMonitorPortals(value);
  if (portals === "sreality") return "Jen Sreality";
  if (portals === "bezrealitky") return "Jen Bezrealitky";
  return "Všechny (Sreality i Bezrealitky)";
}

function selectedMonitorPortals() {
  return normalizeMonitorPortals($("monitor-portals")?.querySelector(".chip.on")?.dataset.portals);
}

function setMonitorPortals(value) {
  const portals = normalizeMonitorPortals(value);
  $("monitor-portals")?.querySelectorAll(".chip").forEach((chip) => {
    chip.classList.toggle("on", chip.dataset.portals === portals);
  });
}

function readMonitorDraft(form) {
  return {
    name: form.querySelector("[name='name']")?.value || "",
    search_url: form.querySelector("[name='search_url']")?.value || "",
    webhook_url: form.querySelector("[name='webhook_url']")?.value || "",
    template_id: form.querySelector("[name='template_id']")?.value || "default",
    enabled: Boolean(form.querySelector("[name='enabled']")?.checked),
    interval_sec: form.querySelector("[name='interval_sec']")?.value || "",
    portals: normalizeMonitorPortals(form.querySelector("[name='portals']")?.value || form.querySelector(".chip.on[data-portals]")?.dataset.portals),
  };
}

function monitorEditorHtml(item, draft) {
  const data = {
    name: draft?.name ?? item.name ?? "",
    search_url: draft?.search_url ?? item.search_url ?? "",
    webhook_url: draft?.webhook_url ?? item.webhook_url ?? "",
    template_id: draft?.template_id ?? item.template_id ?? "default",
    enabled: draft?.enabled ?? Boolean(item.enabled),
    interval_sec: draft?.interval_sec ?? item.interval_sec ?? "",
    portals: normalizeMonitorPortals(draft?.portals ?? item.portals),
  };
  return `
    <form class="form-card" data-monitor-edit="${escapeHtml(item.id)}">
      <label>Název<input name="name" value="${escapeHtml(data.name)}" /></label>
      <label>URL hledání</label>
      <div class="url-stack">${(item.search_targets || []).map((row) => `<label>${escapeHtml(portalTitle(row.portal))}<textarea rows="3" readonly name="search_url_${escapeHtml(row.portal)}">${escapeHtml(row.search_url || "")}</textarea></label>`).join("")}</div>
      <label>Primární URL<textarea name="search_url" rows="3">${escapeHtml(data.search_url)}</textarea></label>
      <input type="hidden" name="portals" value="${escapeHtml(data.portals)}" />
      <div class="filter-group">
        <h3>Hlídané portály</h3>
        <div class="chip-row" data-modal-portals>
          <button type="button" class="chip${data.portals === "all" ? " on" : ""}" data-portals="all">Všechny</button>
          <button type="button" class="chip${data.portals === "sreality" ? " on" : ""}" data-portals="sreality">Jen Sreality</button>
          <button type="button" class="chip${data.portals === "bezrealitky" ? " on" : ""}" data-portals="bezrealitky">Jen Bezrealitky</button>
        </div>
      </div>
      <label>Discord webhook<input name="webhook_url" value="${escapeHtml(data.webhook_url)}" placeholder="prázdné = výchozí webhook pro tento portál" /></label>
      <label>Šablona zprávy<select name="template_id">${templateOptions(data.template_id)}</select></label>
      <label>Interval (s)<input name="interval_sec" type="number" min="20" value="${escapeHtml(data.interval_sec || "")}" placeholder="výchozí 60" /></label>
      <p class="monitor-preview-label">Náhled nabídek</p>
      <div class="monitor-preview" id="monitor-preview"></div>
      <label class="switch-field">
        <span class="switch-copy">
          <span class="switch-title">Stav</span>
          <span class="switch-state"></span>
        </span>
        <input name="enabled" type="checkbox"${data.enabled ? " checked" : ""} />
        <span class="switch-ui" aria-hidden="true"></span>
      </label>
      <div class="actions">
        <button class="btn" type="submit" data-icon="check">Uložit</button>
        <button class="btn btn-ghost" type="button" data-cancel-edit data-icon="x">Zrušit</button>
      </div>
    </form>
  `;
}

function editForm() {
  return document.querySelector("#monitor-modal [data-monitor-edit]");
}

function closeMonitorEditor() {
  editingMonitorId = null;
  editingDraft = null;
  const modal = $("monitor-modal");
  if (modal) modal.hidden = true;
  document.body.classList.remove("monitor-modal-open");
  const body = $("monitor-edit-body");
  if (body) body.innerHTML = "";
}

function openMonitorEditor(id, draft = null) {
  const item = (statusCache.monitors || []).find((row) => String(row.id) === String(id));
  if (!item) return;
  editingMonitorId = item.id;
  editingDraft = draft;
  $("monitor-modal-title").textContent = draft?.name || item.name;
  $("monitor-edit-body").innerHTML = monitorEditorHtml(item, draft);
  decorateIcons($("monitor-edit-body"));
  $("monitor-modal").hidden = false;
  document.body.classList.add("monitor-modal-open");
  fetch(`/api/monitors/${encodeURIComponent(item.id)}/preview`)
    .then((res) => res.json())
    .then((data) => {
      const host = $("monitor-preview");
      if (!host) return;
      const items = data.items || [];
      host.classList.toggle("is-empty", items.length === 0);
      host.innerHTML = items.length
        ? items.map((row) => listingCardHtml(row, { compact: true })).join("")
        : `<p class="monitor-preview-empty">Zatím žádné nabídky v tomto monitoru.</p>`;
    })
    .catch(() => {});
}

function renderCatalogSync(sync, running) {
  const host = $("catalog-sync-status");
  if (!host) return;
  const data = sync || {};
  const jobs = Number(data.jobs || 0);
  const done = Number(data.done || 0);
  const listings = Number(data.listings || 0).toLocaleString("cs-CZ");
  const shard = data.running ? `Běží shard ${data.running}.` : jobs ? `Shardy ${done}/${jobs}.` : "Shardy se spustí v nočním okně.";
  const last = data.last_run ? `Poslední běh ${formatTime(data.last_run)}.` : "Zatím bez dokončeného běhu.";
  const err = data.last_error ? ` Chyba: ${data.last_error}` : "";
  const state = running || data.status === "running" ? "Probíhá sync." : data.status === "partial" ? "Poslední běh byl neúplný." : data.status === "done" ? "Katalog je aktuální." : "Čeká na denní sync.";
  host.textContent = `${state} ${shard} V katalogu ${listings} inzerátů. ${last}${err}`;
}

function labelFromCatalog(catalog, key, ids) {
  const options = catalog?.[key] || [];
  const map = new Map(options.map((row) => [String(row[0]), row[1]]));
  const labels = (ids || []).map((id) => map.get(String(id)) || String(id)).filter(Boolean);
  return labels.join(", ") || "—";
}

function formatMoneyRange(from, to, suffix = " Kč") {
  const fmt = (n) => Number(n).toLocaleString("cs-CZ");
  if (from == null && to == null) return "Bez limitu";
  if (from != null && to != null) return `${fmt(from)} – ${fmt(to)}${suffix}`;
  if (from != null) return `od ${fmt(from)}${suffix}`;
  return `do ${fmt(to)}${suffix}`;
}

function localitySummary(filters) {
  const named = (filters.localities || []).map((item) => item.label || item.id).filter(Boolean);
  if (named.length) return named.join(", ");
  const districts = filters.districts || [];
  if (!districts.length) return "Česko";
  return districts
    .map((id) => String(id).replace(/^praha-/, "Praha ").replace(/-/g, " "))
    .join(", ");
}

function offerSummary(catalog, filters, url) {
  const offer = labelFromCatalog(catalog, "offers", filters.offers);
  const estate = labelFromCatalog(catalog, "estates", filters.estates);
  if (offer === "—" && estate === "—") return portalLabel(url);
  if (estate === "—") return offer;
  if (offer === "—") return estate;
  return `${estate} · ${offer}`.replace("pronajem", "k pronájmu");
}

function intOrNull(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function parseMonitorUrl(url) {
  const filters = {
    offers: [],
    estates: [],
    sizes: [],
    districts: [],
    localities: [],
    price_from: null,
    price_to: null,
    area_from: null,
  };
  if (!url) return filters;
  try {
    const parsed = new URL(url);
    const query = parsed.searchParams;
    const path = parsed.pathname.split("/").filter(Boolean);
    if (url.includes("bezrealitky")) {
      filters.offers = query.getAll("offerType");
      filters.estates = query.getAll("estateType");
      filters.sizes = query.getAll("disposition");
      filters.districts = query.getAll("regionOsmIds");
      const osm = query.get("osm_value");
      if (osm) filters.localities = [{ label: decodeURIComponent(osm) }];
      filters.price_from = intOrNull(query.get("priceFrom"));
      filters.price_to = intOrNull(query.get("priceTo"));
      filters.area_from = intOrNull(query.get("surfaceFrom"));
      return filters;
    }
    const start = path.indexOf("hledani");
    const parts = start >= 0 ? path.slice(start + 1) : path;
    if (parts[0]) filters.offers = parts[0].split(",").filter(Boolean);
    if (parts[1]) filters.estates = parts[1].split(",").filter(Boolean);
    if (parts[2]) filters.districts = parts[2].split(",").filter(Boolean);
    const sizes = query.get("velikost");
    if (sizes) filters.sizes = sizes.split(",").filter(Boolean);
    filters.price_from = intOrNull(query.get("cena-od"));
    filters.price_to = intOrNull(query.get("cena-do"));
    filters.area_from = intOrNull(query.get("plocha-od"));
  } catch {
    return filters;
  }
  return filters;
}

function watchFieldsHtml(item, filters) {
  const source = (item.search_url || "").includes("bezrealitky") ? "bezrealitky" : "sreality";
  const catalog = filterCatalog?.sources?.[source]?.catalog || {};
  const area = filters.area_from ? `${filters.area_from} m²` : "Bez minima";
  const rows = [
    ["Typ nabídky", offerSummary(catalog, filters, item.search_url)],
    ["Preferované lokality", localitySummary(filters)],
    ["Dispozice bytu", labelFromCatalog(catalog, "sizes", filters.sizes)],
    ["Cenový rozsah", formatMoneyRange(filters.price_from, filters.price_to, " Kč/měs.")],
    ["Minimální plocha", area],
    ["Portály", portalsSummary(item.portals)],
  ];
  return rows
    .map(
      ([key, value]) =>
        `<div><p class="k">${escapeHtml(key)}</p><p class="v">${escapeHtml(value)}</p></div>`,
    )
    .join("");
}

function showWatchHome() {
  if ($("watch-home")) $("watch-home").hidden = false;
  if ($("watch-editor")) $("watch-editor").hidden = true;
}

function showWatchEditor(title) {
  if ($("watch-home")) $("watch-home").hidden = true;
  if ($("watch-editor")) $("watch-editor").hidden = false;
  if ($("watch-editor-title")) $("watch-editor-title").textContent = title || "NOVÝ HLÍDACÍ PROFIL";
}

async function applyUrlToFilters(url) {
  if (!url) {
    filterState = structuredClone(filterCatalog?.defaults || {});
    renderFilterGroups();
    await rebuildUrl();
    return;
  }
  const response = await fetch("/api/filters/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.detail || "URL se nepodařilo načíst", "error");
    return;
  }
  filterState = data.filters || {};
  filterState.source = (url || "").includes("bezrealitky") ? "bezrealitky" : "sreality";
  applySourceCatalog(filterState.source);
  renderFilterGroups();
  $("generated-url").value = data.url || url;
}

async function openWatchEditor(item) {
  showWatchEditor(item?.name ? item.name.toUpperCase() : "NOVÝ HLÍDACÍ PROFIL");
  fillMonitorForm(item || null);
  const tplId = item?.template_id || "default";
  const tpl = (statusCache.templates || []).find((row) => row.id === tplId);
  if (tpl) {
    templateState = structuredClone(tpl);
    fillTemplateSelects(statusCache.templates || []);
    renderTemplateEditor();
  }
  await applyUrlToFilters(item?.search_url || "");
  $("watch-editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

let lastMonitorRenderKey = "";

function currentBilling() {
  return statusCache.billing || { plan: "free", label: "Zdarma", watch_limit: 1, price_czk: 0, features: [] };
}

function watchLimitCopy(count) {
  const billing = currentBilling();
  const enabled = (statusCache.monitors || []).filter((item) => item.enabled).length;
  const paused = Math.max(0, count - enabled);
  if (billing.watch_limit == null) {
    return `Využíváte ${enabled} aktivních hlídacích profilů v tarifu ${billing.label} (neomezeně).`;
  }
  let text = `Využíváte ${enabled} z ${billing.watch_limit} aktivních hlídacích profilů v tarifu ${billing.label}.`;
  if (paused) text += ` ${paused} je pozastavených — zapnete je upgradem.`;
  return text;
}

function formatBillingDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("cs-CZ");
}

function renderBilling(billing) {
  if (!billing) return;
  statusCache.billing = billing;
  if ($("billing-plan-pill")) $("billing-plan-pill").textContent = `Aktivní plán: ${billing.label || "Zdarma"}`;
  const price = billing.price_czk;
  if ($("billing-price")) $("billing-price").textContent = price == null ? "Dohodou" : `${price} Kč`;
  if ($("billing-features")) {
    $("billing-features").innerHTML = (billing.features || [])
      .map((row) => `<li><img src="/static/assets/settings/icon-plan-check.svg" alt="" />${escapeHtml(row)}</li>`)
      .join("");
  }
  let next = "";
  if (billing.pending_plan && billing.pending_at) {
    const when = formatBillingDate(billing.pending_at);
    next = `Do ${when} zůstáváte na tarifu ${billing.label}. Pak se automaticky přepne na ${billing.pending_label}. Extra hlídací psi se pozastaví až tehdy.`;
  } else if (billing.cancel_at_period_end && billing.current_period_end) {
    next = `Předplatné doběhne do ${formatBillingDate(billing.current_period_end)}, pak Zdarma. Extra hlídací psi se pozastaví až tehdy.`;
  } else if (billing.status === "trialing" && billing.current_period_end) {
    next = `Zkušební doba do ${formatBillingDate(billing.current_period_end)}.`;
  } else if (billing.current_period_end && billing.plan !== "free") {
    next = `Další platba: ${formatBillingDate(billing.current_period_end)}`;
  }
  if ($("billing-next")) {
    $("billing-next").hidden = !next;
    $("billing-next").textContent = next;
  }
  const plan = billing.plan || "free";
  const ranks = billing.plan_rank || { free: 0, start: 1, pro: 2 };
  document.querySelectorAll(".bill-tier").forEach((card) => {
    const id = card.dataset.plan;
    const current = id === plan;
    card.hidden = false;
    card.classList.toggle("is-current", current);
    const button = card.querySelector("[data-billing]");
    if (!button) return;
    button.classList.remove("bill-cta-current");
    if (current) {
      if (billing.pending_plan) {
        button.disabled = false;
        button.textContent = `Ponechat ${billing.label}`;
        return;
      }
      button.disabled = true;
      button.classList.add("bill-cta-current");
      const check = card.classList.contains("bill-tier-pro") ? "/static/site/assets/check.svg" : "/static/assets/settings/icon-plan-check.svg";
      button.innerHTML = `<img src="${check}" alt="" />Vaše předplatné`;
      return;
    }
    button.disabled = false;
    if (billing.pending_plan === id) {
      button.disabled = true;
      button.innerHTML = `<span>Naplánováno od ${formatBillingDate(billing.pending_at)}</span>`;
      return;
    }
    const rank = ranks[id] ?? 0;
    const mine = ranks[plan] ?? 0;
    let label = "Objednat";
    if (id === "free") label = "Přepnout na Zdarma";
    else if (rank > mine) label = id === "pro" ? "Objednat PRO" : "Objednat Start";
    else label = id === "pro" ? "Přepnout na PRO" : "Přepnout na Start";
    button.innerHTML = `<span>${label}</span>`;
  });
  const paid = plan === "start" || plan === "pro";
  if ($("billing-cancel-card")) $("billing-cancel-card").hidden = !paid;
  if ($("billing-payment-card")) $("billing-payment-card").hidden = !billing.customer_id;
  if (billing.payment) {
    if ($("billing-card-brand")) $("billing-card-brand").textContent = billing.payment.brand || "KARTA";
    if ($("billing-card-label")) $("billing-card-label").textContent = `${billing.payment.brand || "Karta"} končící na ${billing.payment.last4 || "----"}`;
    if ($("billing-card-exp")) $("billing-card-exp").textContent = billing.payment.exp ? `Expirace ${billing.payment.exp}` : "";
  }
}

async function startCheckout(plan) {
  const response = await fetch("/api/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.detail || "Objednávku se nepodařilo spustit", "error");
    return;
  }
  if (data.url) {
    location.href = data.url;
    return;
  }
  if (data.billing) {
    renderBilling(data.billing);
    const label = data.billing.label || "";
    if (data.switched && data.upgraded && data.charged_czk > 0) {
      toast(`Přešli jste na ${label}. Doplatek ${Number(data.charged_czk).toLocaleString("cs-CZ")} Kč za zbývající dny.`);
    } else if (data.switched && data.upgraded) {
      toast(`Přešli jste na ${label}. Do konce období se nic dalšího nestrhává.`);
    } else if (data.scheduled && data.billing) {
      const when = formatBillingDate(data.billing.pending_at || data.billing.current_period_end);
      const nextLabel = data.billing.pending_label || (data.billing.cancel_at_period_end ? "Zdarma" : label);
      toast(`Do ${when} zůstává ${data.billing.label}. Pak se přepne na ${nextLabel}.`);
    } else if (data.switched && !data.upgraded && !data.scheduled) {
      toast(`Naplánované snížení je zrušené. Zůstáváte na tarifu ${label}.`);
    } else if (data.switched && (data.paused_monitors || []).length) {
      toast(`Tarif ${label} je aktivní. Pozastaveno ${data.paused_monitors.length} hlídacích psů nad limitem.`);
    } else {
      toast(`Tarif ${label} je aktivní`);
    }
    await refresh();
  }
}

async function openBillingPortal() {
  const response = await fetch("/api/billing/portal", { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.detail || "Stripe portál se nepodařilo otevřít", "error");
    return;
  }
  if (data.url) location.href = data.url;
}

async function cancelBilling() {
  const response = await fetch("/api/billing/cancel", { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.detail || "Zrušení se nepovedlo", "error");
    return;
  }
  renderBilling(data);
  const paused = data.paused_monitors || [];
  toast(data.scheduled || data.pending_plan || data.cancel_at_period_end
    ? `Tarif ${currentBilling().label} doběhne do ${formatBillingDate(data.pending_at || data.current_period_end)}. Pak Zdarma.`
    : (paused.length ? `Tarif Zdarma. Pozastaveno ${paused.length} hlídacích psů.` : "Předplatné je zrušené"));
  await refresh();
}

function planRank(id) {
  return (currentBilling().plan_rank || { free: 0, start: 1, pro: 2 })[id] ?? 0;
}

function extraWatchNames(limit) {
  if (limit == null) return [];
  return (statusCache.monitors || []).filter((item) => item.enabled).slice(limit).map((item) => item.name);
}

function closeBillingConfirm() {
  if ($("billing-confirm")) $("billing-confirm").hidden = true;
}

function confirmPlanChange(plan) {
  return new Promise((resolve) => {
    const current = currentBilling().plan || "free";
    const losses = currentBilling().downgrade_losses?.[`${current}_${plan}`] || [];
    const targetLimit = plan === "free" ? 1 : plan === "start" ? 10 : null;
    const extras = extraWatchNames(targetLimit);
    const lead = $("billing-confirm-lead");
    const list = $("billing-confirm-losses");
    const box = $("billing-confirm");
    if ($("billing-confirm-title")) {
      $("billing-confirm-title").textContent = plan === "free" ? "Přepnout na Zdarma?" : `Přepnout na ${plan === "start" ? "Start" : "PRO"}?`;
    }
    if (lead) {
      const when = formatBillingDate(currentBilling().current_period_end);
      lead.textContent = when
        ? `Do ${when} zůstáváte na tarifu ${currentBilling().label}. Teprve potom se tarif sníží${extras.length ? ` a pozastaví se ${extras.length} hlídacích psů nad limitem` : ""}.`
        : extras.length
          ? `Ztratíte funkce vyššího tarifu a po konci období se pozastaví ${extras.length} hlídacích psů.`
          : "Ztratíte tyto funkce vyššího tarifu až po konci zaplaceného období:";
    }
    if (list) {
      list.innerHTML = [
        ...losses.map((row) => `<li>${escapeHtml(row)}</li>`),
        ...extras.map((name) => `<li>Po konci období se pozastaví: ${escapeHtml(name)}</li>`),
      ].join("") || "<li>Nižší tarif má menší limity.</li>";
    }
    if (!box) {
      resolve(confirm("Opravdu chcete přepnout na nižší tarif?"));
      return;
    }
    box.hidden = false;
    const ok = $("billing-confirm-ok");
    const cancel = $("billing-confirm-cancel");
    const done = (value) => {
      box.hidden = true;
      ok?.removeEventListener("click", onOk);
      cancel?.removeEventListener("click", onCancel);
      box.removeEventListener("click", onBackdrop);
      resolve(value);
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    const onBackdrop = (event) => {
      if (event.target === box) done(false);
    };
    ok?.addEventListener("click", onOk);
    cancel?.addEventListener("click", onCancel);
    box.addEventListener("click", onBackdrop);
  });
}

async function pickBillingPlan(plan) {
  const current = currentBilling().plan || "free";
  if (plan === current) {
    if (currentBilling().pending_plan) await startCheckout(plan);
    return;
  }
  if (planRank(plan) < planRank(current)) {
    const ok = await confirmPlanChange(plan);
    if (!ok) return;
  }
  if (plan === "free") await cancelBilling();
  else await startCheckout(plan);
}

async function loadBillingInvoices() {
  const response = await fetch("/api/billing");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) return;
  renderBilling(data);
  const body = $("billing-invoices");
  if (!body) return;
  const invoices = data.invoices || [];
  if (!invoices.length) {
    body.innerHTML = `<tr><td colspan="5" class="set-muted">Zatím žádné faktury. Tarif Zdarma se neúčtuje.</td></tr>`;
    return;
  }
  body.innerHTML = invoices
    .map(
      (row) => `<tr>
        <td>${escapeHtml(formatBillingDate(row.date))}</td>
        <td>${Number(row.amount || 0).toLocaleString("cs-CZ")} Kč</td>
        <td>${escapeHtml(row.plan || "")}</td>
        <td>${row.paid ? `<span class="set-chip-on">Zaplaceno</span>` : `<span class="set-muted">Čeká</span>`}</td>
        <td>${row.pdf ? `<a class="set-link" href="${escapeHtml(row.pdf)}" target="_blank" rel="noreferrer">Stáhnout PDF</a>` : `<span class="set-muted">-</span>`}</td>
      </tr>`,
    )
    .join("");
}

function renderMonitors(items) {
  const list = $("monitor-list");
  if (!list) return;
  const form = editForm();
  if (form && editingMonitorId && !$("monitor-modal").hidden) editingDraft = readMonitorDraft(form);
  const renderKey = JSON.stringify(items.map((item) => [item.id, item.name, item.enabled, item.search_url, item.portals, item.template_id]));
  if (renderKey === lastMonitorRenderKey && list.children.length) {
    const count = items.length;
    if ($("watch-limit-copy")) $("watch-limit-copy").textContent = watchLimitCopy(count);
    items.forEach((item) => {
      const card = list.querySelector(`[data-monitor-card="${item.id}"]`);
      const stats = card?.querySelectorAll(".watch-stats strong");
      if (stats?.[0]) stats[0].textContent = `${Number(item.tracked || 0).toLocaleString("cs-CZ")} nabídek`;
      if (stats?.[1]) stats[1].textContent = item.last_total ? Number(item.last_total).toLocaleString("cs-CZ") : "—";
    });
    return;
  }
  lastMonitorRenderKey = renderKey;
  const count = items.length;
  if ($("watch-limit-copy")) $("watch-limit-copy").textContent = watchLimitCopy(count);
  if ($("watch-create-hint")) {
    const limit = currentBilling().watch_limit;
    const enabled = items.filter((item) => item.enabled).length;
    if (limit == null) {
      $("watch-create-hint").textContent = "V tarifu máte neomezený počet profilů.";
    } else {
      const left = Math.max(0, limit - enabled);
      $("watch-create-hint").textContent = left
        ? `Zbývá vám ještě ${left} aktivn${left === 1 ? "í profil" : "í profily"} v tarifu ${currentBilling().label}.`
        : `Limit tarifu ${currentBilling().label} je ${limit} aktivních profilů. Další zapnete upgradem.`;
    }
  }
  list.innerHTML = items
    .map((item) => {
      const editing = String(item.id) === String(editingMonitorId);
      return `
      <article class="watch-card${editing ? " is-editing" : ""}${item.enabled ? "" : " is-off"}" data-monitor-card="${item.id}">
        <div class="watch-card-head">
          <h3>${escapeHtml(item.name)} <button type="button" data-edit-monitor="${item.id}" aria-label="Upravit"><img src="/static/assets/settings/icon-edit.svg" alt="" /></button></h3>
          <span class="watch-status${item.enabled ? "" : " is-off"}"><img src="/static/assets/settings/icon-dot.svg" alt="" />${item.enabled ? "Aktivní" : "Pozastaveno"}</span>
        </div>
        <div class="watch-fields">${watchFieldsHtml(item, parseMonitorUrl(item.search_url))}</div>
        <div class="watch-meta">
          <div class="watch-stats">
            <div><span>Nalezené nabídky</span><strong>${Number(item.tracked || 0).toLocaleString("cs-CZ")} nabídek</strong></div>
            <div><span>Poslední zásah</span><strong>${item.last_total ? Number(item.last_total).toLocaleString("cs-CZ") : "—"}</strong></div>
          </div>
          <div class="watch-notes">
            <span class="k">NOTIFIKACE:</span>
            <span class="watch-chip">DISCORD ON</span>
            <span class="watch-chip${item.enabled ? "" : " off"}">${item.enabled ? "HLÍDÁNÍ ON" : "HLÍDÁNÍ OFF"}</span>
            <span class="watch-chip off">SMS OFF</span>
          </div>
        </div>
        <div class="actions">
          <button class="btn" type="button" data-edit-monitor="${item.id}">Upravit profil</button>
          <button class="btn btn-ghost" type="button" data-toggle-monitor="${item.id}">${item.enabled ? "Pozastavit hlídání" : "Obnovit hlídání"}</button>
          <button class="set-link" type="button" data-del-monitor="${item.id}">Smazat profil</button>
        </div>
        ${!item.enabled ? `<p class="watch-upgrade-hint">Pro opětovné zapnutí upgradujte tarif, pokud už máte naplněný limit aktivních psů.</p>` : ""}
      </article>
    `;
    })
    .join("");
  decorateIcons(list);
}

const NEW_TEMPLATE_ID = "__new__";

function blankTemplate() {
  const config = structuredClone(
    (statusCache.templates || []).find((item) => item.id === "default")?.config ||
      (statusCache.templates || [])[0]?.config || {
        username: "Sreality Monitor",
        content_lines: [
          { text: "**{{headline}}**" },
          { text: "{{name}}" },
          { text: "" },
          { text: "**Cena:** {{price_label}}" },
          { text: "**Dispozice:** {{disposition}}" },
          { text: "**Rozloha:** {{area}}" },
          { text: "**Lokalita:** {{locality}}" },
          { text: "" },
          { text: "[Otevřít inzerát]({{url}})" },
          { text: "[Otevřít v Google Maps]({{maps_url}})" },
        ],
        embed: {
          title: "{{name}}",
          url: "{{url}}",
          color: "#9fe870",
          show_image: true,
          footer: "Sreality monitor",
          fields: [
            { name: "Cena", value: "{{price_label}}", inline: true },
            { name: "Dispozice", value: "{{disposition}}", inline: true },
            { name: "Rozloha", value: "{{area}}", inline: true },
            { name: "Lokalita", value: "{{locality}}", inline: false },
          ],
        },
      },
  );
  return { id: "", name: "Nová šablona", config };
}

function startNewTemplate() {
  templateState = blankTemplate();
  fillTemplateSelects(statusCache.templates || []);
  $("template-select").value = NEW_TEMPLATE_ID;
  renderTemplateEditor();
}

function fillTemplateSelects(templates) {
  const options = templates.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  const currentMonitor = $("monitor-template")?.value;
  if ($("monitor-template")) {
    $("monitor-template").innerHTML = options;
    if (currentMonitor) $("monitor-template").value = currentMonitor;
  }
  if ($("template-select")) {
    $("template-select").innerHTML = `<option value="${NEW_TEMPLATE_ID}">Nová šablona</option>${options}`;
    $("template-select").value = templateState?.id || NEW_TEMPLATE_ID;
  }
}

function fillMonitorForm(item) {
  $("monitor-id").value = item?.id || "";
  $("monitor-name").value = item?.name || "";
  $("monitor-url").value = item?.search_url || "";
  if ($("monitor-webhook")) $("monitor-webhook").value = item?.webhook_url || "";
  $("monitor-template").value = item?.template_id || "default";
  $("monitor-enabled").checked = item ? Boolean(item.enabled) : true;
  if ($("monitor-interval")) $("monitor-interval").value = item?.interval_sec || "";
  setMonitorPortals(item?.portals || "all");
  renderPortalUrlStack($("monitor-urls"), item?.search_targets, item?.search_url);
}

$("monitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const saved = await post(
    "/api/monitors",
    {
      id: $("monitor-id").value || undefined,
      name: $("monitor-name").value,
      search_url: $("generated-url")?.value || $("monitor-url").value,
      template_id: $("monitor-template").value,
      enabled: $("monitor-enabled").checked,
      interval_sec: $("monitor-interval")?.value || "",
      portals: selectedMonitorPortals(),
    },
    "Monitor uložen",
  );
  if (saved) {
    fillMonitorForm(null);
    showWatchHome();
  }
});

$("monitor-reset").addEventListener("click", () => {
  fillMonitorForm(null);
  toast("Formulář vyčištěn", "info");
});

$("monitor-list").addEventListener("change", async (event) => {
  const toggle = event.target.closest("[data-toggle-monitor]");
  if (!toggle) return;
  const item = (statusCache.monitors || []).find((row) => String(row.id) === String(toggle.dataset.toggleMonitor));
  if (!item) return;
  const saved = await post(
    "/api/monitors",
    {
      id: item.id,
      name: item.name,
      search_url: item.search_url,
      webhook_url: item.webhook_url,
      template_id: item.template_id,
      interval_sec: item.interval_sec || "",
      portals: item.portals || "all",
      enabled: toggle.checked,
    },
    toggle.checked ? "Monitor zapnutý" : "Monitor vypnutý",
  );
  if (!saved) toggle.checked = !toggle.checked;
});

$("monitor-modal").addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-monitor-edit]");
  if (!form) return;
  event.preventDefault();
  const draft = readMonitorDraft(form);
  const id = form.dataset.monitorEdit;
  closeMonitorEditor();
  const saved = await post("/api/monitors", { id, ...draft }, "Monitor upraven");
  if (!saved) openMonitorEditor(id, draft);
});

$("monitor-modal").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-modal-portals] [data-portals]");
  if (chip) {
    const row = chip.closest("[data-modal-portals]");
    row?.querySelectorAll(".chip").forEach((item) => item.classList.toggle("on", item === chip));
    const hidden = chip.closest("form")?.querySelector("[name='portals']");
    if (hidden) hidden.value = chip.dataset.portals;
    return;
  }
  if (event.target.id === "monitor-modal" || event.target.closest("[data-cancel-edit]")) {
    closeMonitorEditor();
    renderMonitors(statusCache.monitors || []);
  }
});

$("monitor-modal-close").addEventListener("click", () => {
  closeMonitorEditor();
  renderMonitors(statusCache.monitors || []);
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || $("monitor-modal").hidden) return;
  closeMonitorEditor();
  renderMonitors(statusCache.monitors || []);
});

$("monitor-list").addEventListener("click", async (event) => {
  const edit = event.target.closest("[data-edit-monitor]");
  const del = event.target.closest("[data-del-monitor]");
  const check = event.target.closest("[data-check-monitor]");
  const mirror = event.target.closest("[data-mirror-monitor]");
  const toggle = event.target.closest("[data-toggle-monitor]");
  if (edit) {
    const item = (statusCache.monitors || []).find((row) => String(row.id) === String(edit.dataset.editMonitor));
    if (item) await openWatchEditor(item);
  }
  if (toggle) {
    const item = (statusCache.monitors || []).find((row) => String(row.id) === String(toggle.dataset.toggleMonitor));
    if (!item) return;
    if (!item.enabled) {
      const limit = currentBilling().watch_limit;
      const enabled = (statusCache.monitors || []).filter((row) => row.enabled).length;
      if (limit != null && enabled >= limit) {
        toast("Limit aktivních hlídacích psů je naplněný. Upgradujte tarif.", "info");
        history.pushState(null, "", "/nastaveni/predplatne");
        applyRoute();
        return;
      }
    }
    await post(
      "/api/monitors",
      {
        id: item.id,
        name: item.name,
        search_url: item.search_url,
        webhook_url: item.webhook_url,
        template_id: item.template_id,
        interval_sec: item.interval_sec || "",
        portals: item.portals || "all",
        enabled: !item.enabled,
      },
      item.enabled ? "Hlídání pozastaveno" : "Hlídání obnoveno",
    );
  }
  if (del && confirm("Smazat hlídací profil i jeho uložená ID?")) {
    if (String(del.dataset.delMonitor) === String(editingMonitorId)) closeMonitorEditor();
    const response = await fetch(`/api/monitors/${del.dataset.delMonitor}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast(data.detail || "Profil se nepodařilo smazat", "error");
      return;
    }
    toast("Profil smazán");
    showWatchHome();
    await refresh();
  }
  if (check) await post("/api/monitor/check", { monitor_id: check.dataset.checkMonitor }, "Kontrola monitoru dokončena");
  if (mirror) {
    await openWatchEditor(null);
    await applyMirroredFilters(mirror.dataset.mirrorMonitor);
  }
});

function chipGroup(title, key, options, single = false) {
  const selected = new Set(filterState[key] || []);
  const known = new Map((options || []).map(([id, label]) => [String(id), label]));
  for (const id of selected) {
    if (!known.has(String(id))) known.set(String(id), String(id));
  }
  return `
    <div class="filter-group">
      <h3>${escapeHtml(title)}</h3>
      <div class="chip-row">
        ${[...known.entries()]
          .map(
            ([id, label]) =>
              `<button type="button" class="chip ${selected.has(id) ? "on" : ""}" data-filter="${key}" data-value="${escapeHtml(id)}" ${single ? "data-single='1'" : ""}>${escapeHtml(label)}</button>`,
          )
          .join("")}
      </div>
    </div>
  `;
}

function currentSource() {
  return filterState.source === "bezrealitky" ? "bezrealitky" : "sreality";
}

function localityMap() {
  return filterCatalog?.locality_map || {};
}

function shortLocalityLabel(label) {
  return String(label || "").split(",")[0].trim();
}

function localityFromParts(id, label, sreality) {
  const map = localityMap();
  const ident = String(id || "").trim();
  const name = String(label || map.osm_labels?.[ident] || ident).trim();
  return {
    id: ident,
    label: name,
    sreality: sreality || map.osm_to_sreality?.[ident] || "",
    osm_value: shortLocalityLabel(name),
  };
}

function hydrateLocalities() {
  if (Array.isArray(filterState.localities)) {
    filterState.localities = filterState.localities.map((item) =>
      localityFromParts(item.id, item.label, item.sreality),
    );
    return;
  }
  const map = localityMap();
  const osmValue = filterState.osm_value || "";
  filterState.localities = (filterState.districts || []).map((value) => {
    const ident = String(value);
    if (map.osm_to_sreality?.[ident] || /^R\d+$/i.test(ident)) {
      return localityFromParts(ident, map.osm_labels?.[ident] || osmValue || ident);
    }
    const osm = map.sreality_to_osm?.[ident];
    if (osm) return localityFromParts(osm, map.osm_labels?.[osm] || ident, ident);
    return localityFromParts(ident, osmValue || ident, ident);
  });
}

function applyLocalitiesToDistricts() {
  hydrateLocalities();
  const locs = filterState.localities || [];
  if (currentSource() === "bezrealitky") {
    filterState.districts = locs.map((item) => item.id).filter((id) => /^R\d+$/i.test(id));
    filterState.osm_value = locs[0]?.osm_value || shortLocalityLabel(locs[0]?.label) || "";
    if (locs.some((item) => item.id === "R51684")) {
      filterState.districts = ["R51684"];
      filterState.osm_value = "Česko";
    }
  } else {
    filterState.districts = [
      ...new Set(locs.flatMap((item) => String(item.sreality || "").split(",").map((part) => part.trim()).filter(Boolean))),
    ];
  }
}

function renderLocalityChips() {
  const host = $("f-locality-chips");
  if (!host) return;
  hydrateLocalities();
  const selected = filterState.localities || [];
  const selectedIds = new Set(selected.map((item) => item.id));
  const quick = localityMap().quick || filterCatalog?.sources?.bezrealitky?.catalog?.districts || [];
  const pin = typeof icon === "function" ? icon("pin") : "";
  const plus = typeof icon === "function" ? icon("plus") : "";
  const close = typeof icon === "function" ? icon("x") : "×";
  const chosen = selected
    .map(
      (item) =>
        `<button type="button" class="chip on locality-chip" data-remove-city="${escapeHtml(item.id)}">${pin}${escapeHtml(shortLocalityLabel(item.label))}<span class="locality-x" aria-hidden="true">${close}</span></button>`,
    )
    .join("");
  const extras = quick
    .filter(([id]) => !selectedIds.has(id))
    .map(
      ([id, label]) =>
        `<button type="button" class="chip locality-chip" data-add-city="${escapeHtml(id)}" data-label="${escapeHtml(label)}">${plus}${escapeHtml(label)}</button>`,
    )
    .join("");
  host.innerHTML = chosen + extras || `<p class="locality-hint">Vyberte město z nápovědy</p>`;
  if (typeof decorateIcons === "function") decorateIcons(host);
}

function isCzechCountry(id, label) {
  if (String(id || "") === "R51684") return true;
  const slug = shortLocalityLabel(label)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");
  return ["cesko", "ceska republika", "czech republic", "czechia"].includes(slug);
}

function addFilterLocality(id, label, sreality) {
  hydrateLocalities();
  if (!id || (filterState.localities || []).some((item) => item.id === id)) return;
  const next = localityFromParts(id, label, sreality);
  if (isCzechCountry(next.id, next.label)) {
    filterState.localities = [next];
  } else {
    filterState.localities = [...(filterState.localities || []).filter((item) => item.id !== "R51684"), next];
  }
  applyLocalitiesToDistricts();
  renderLocalityChips();
}

function removeFilterLocality(id) {
  hydrateLocalities();
  filterState.localities = (filterState.localities || []).filter((item) => item.id !== id);
  applyLocalitiesToDistricts();
  renderLocalityChips();
}

function applySourceCatalog(source) {
  const pack = filterCatalog?.sources?.[source];
  if (!pack) return;
  filterCatalog.catalog = pack.catalog;
  filterCatalog.defaults = pack.defaults;
  filterCatalog.sample = pack.sample;
}

function renderFilterGroups() {
  if (!filterCatalog) return;
  const source = currentSource();
  applySourceCatalog(source);
  document.querySelectorAll("#filter-source .chip").forEach((chip) => {
    chip.classList.toggle("on", chip.dataset.source === source);
  });
  document.querySelectorAll(".sreality-only").forEach((el) => {
    el.hidden = source === "bezrealitky";
  });
  document.querySelectorAll(".br-only").forEach((el) => {
    el.hidden = source !== "bezrealitky";
  });
  const cat = filterCatalog.catalog;
  const singles = new Set(cat.single_keys || []);
  const groups = [
    ["Typ nabídky", "offers", cat.offers],
    ["Nemovitost", "estates", cat.estates],
    ["Dispozice", "sizes", cat.sizes],
    ["Typ vlastnictví", "ownership", cat.ownership],
    ["Převod do osobního vlastnictví", "transfers", cat.transfers],
    ["Stav budovy", "conditions", cat.conditions],
    ["Konstrukce budovy", "buildings", cat.buildings],
    ["Vybavenost", "equipped", cat.equipped],
    ["Něco navíc", "extras", cat.extras],
    ["Spolubydlení", "roommate", cat.roommate],
    ["Další", "flags", cat.flags],
    ["Energetická náročnost", "energy", cat.energy],
    ["V okolí nemovitosti", "pois", cat.pois],
  ];
  $("filter-groups").innerHTML = groups
    .filter(([, , options]) => options && options.length)
    .map(([title, key, options]) => chipGroup(title, key, options, singles.has(key)))
    .join("");
  renderLocalityChips();
  $("f-price-from").value = filterState.price_from ?? "";
  $("f-price-to").value = filterState.price_to ?? "";
  $("f-area-from").value = filterState.area_from ?? "";
  $("f-area-to").value = filterState.area_to ?? "";
  $("f-floor-from").value = filterState.floor_from ?? "";
  $("f-floor-to").value = filterState.floor_to ?? "";
  $("f-poi-km").value = filterState.poi_distance ?? 2;
  const setVal = (id, value) => {
    if ($(id)) $(id).value = value ?? "";
  };
  setVal("f-annuity-from", filterState.annuity_from);
  setVal("f-annuity-to", filterState.annuity_to);
  setVal("f-balcony-from", filterState.balcony_from);
  setVal("f-balcony-to", filterState.balcony_to);
  setVal("f-loggia-from", filterState.loggia_from);
  setVal("f-loggia-to", filterState.loggia_to);
  setVal("f-cellar-from", filterState.cellar_from);
  setVal("f-cellar-to", filterState.cellar_to);
  setVal("f-terrace-from", filterState.terrace_from);
  setVal("f-terrace-to", filterState.terrace_to);
  setVal("f-garden-from", filterState.garden_from);
  setVal("f-garden-to", filterState.garden_to);
  setVal("f-br-currency", filterState.currency || "CZK");
  setVal("f-br-neighborhood", filterState.neighborhood || 0);
  setVal("f-br-available", filterState.available_from);
  setVal("f-br-id", filterState.advert_id);
}

function readRanges() {
  const num = (id) => {
    const value = $(id).value;
    return value === "" ? null : Number(value);
  };
  filterState.price_from = num("f-price-from");
  filterState.price_to = num("f-price-to");
  filterState.area_from = num("f-area-from");
  filterState.area_to = num("f-area-to");
  filterState.floor_from = num("f-floor-from");
  filterState.floor_to = num("f-floor-to");
  filterState.poi_distance = num("f-poi-km") || 2;
  if (currentSource() === "sreality") {
    filterState.category = "byty";
    filterState.sort = filterState.sort || "nejlevnejsi";
  } else {
    filterState.sort = filterState.sort || "TIMEORDER_DESC";
    filterState.annuity_from = num("f-annuity-from");
    filterState.annuity_to = num("f-annuity-to");
    filterState.balcony_from = num("f-balcony-from");
    filterState.balcony_to = num("f-balcony-to");
    filterState.loggia_from = num("f-loggia-from");
    filterState.loggia_to = num("f-loggia-to");
    filterState.cellar_from = num("f-cellar-from");
    filterState.cellar_to = num("f-cellar-to");
    filterState.terrace_from = num("f-terrace-from");
    filterState.terrace_to = num("f-terrace-to");
    filterState.garden_from = num("f-garden-from");
    filterState.garden_to = num("f-garden-to");
    filterState.currency = $("f-br-currency")?.value || "CZK";
    filterState.neighborhood = Number($("f-br-neighborhood")?.value || 0);
    filterState.available_from = $("f-br-available")?.value || null;
    filterState.advert_id = $("f-br-id")?.value || "";
  }
}

async function rebuildUrl() {
  readRanges();
  applyLocalitiesToDistricts();
  const response = await fetch("/api/filters/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filters: filterState, portals: selectedMonitorPortals() }),
  });
  const data = await response.json();
  $("generated-url").value = data.url;
  renderPortalUrlStack($("generated-urls"), data.targets, data.url);
}

$("filter-groups").addEventListener("click", async (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  const key = chip.dataset.filter;
  const value = chip.dataset.value;
  if (chip.dataset.single) {
    filterState[key] = (filterState[key] || [])[0] === value ? [] : [value];
    renderFilterGroups();
  } else {
    const current = new Set(filterState[key] || []);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    filterState[key] = [...current];
    chip.classList.toggle("on");
  }
  await rebuildUrl();
});

const rangeIds = [
  "f-price-from",
  "f-price-to",
  "f-area-from",
  "f-area-to",
  "f-floor-from",
  "f-floor-to",
  "f-poi-km",
  "f-annuity-from",
  "f-annuity-to",
  "f-balcony-from",
  "f-balcony-to",
  "f-loggia-from",
  "f-loggia-to",
  "f-cellar-from",
  "f-cellar-to",
  "f-terrace-from",
  "f-terrace-to",
  "f-garden-from",
  "f-garden-to",
  "f-br-currency",
  "f-br-neighborhood",
  "f-br-available",
  "f-br-id",
];
rangeIds.forEach((id) => {
  $(id)?.addEventListener("change", rebuildUrl);
});

let localitySuggestItems = [];
let localitySuggestTimer = 0;

function hideLocalitySuggest() {
  const box = $("f-locality-suggest");
  if (!box) return;
  box.hidden = true;
  box.innerHTML = "";
}

async function searchFilterLocality(q) {
  const box = $("f-locality-suggest");
  if (!box) return;
  if (q.length < 2) {
    hideLocalitySuggest();
    return;
  }
  const response = await fetch(`/api/filters/locality?q=${encodeURIComponent(q)}`);
  const data = await response.json().catch(() => ({ items: [] }));
  localitySuggestItems = data.items || [];
  const pin = typeof icon === "function" ? icon("pin") : "";
  box.hidden = false;
  if (!localitySuggestItems.length) {
    box.innerHTML = `<button type="button" disabled>Nic se nenašlo</button>`;
    return;
  }
  box.innerHTML = localitySuggestItems
    .map(
      (item, index) =>
        `<button type="button" data-suggest="${index}">${pin}<span>${escapeHtml(item.label)}</span></button>`,
    )
    .join("");
}

$("f-locality-q")?.addEventListener("input", () => {
  clearTimeout(localitySuggestTimer);
  const q = $("f-locality-q").value.trim();
  localitySuggestTimer = window.setTimeout(() => searchFilterLocality(q), 220);
});
$("f-locality-q")?.addEventListener("keydown", (event) => {
  if (event.key === "Escape") hideLocalitySuggest();
});
$("f-locality-suggest")?.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-suggest]");
  if (!btn) return;
  const item = localitySuggestItems[Number(btn.dataset.suggest)];
  if (!item) return;
  addFilterLocality(item.id, item.label, item.sreality);
  $("f-locality-q").value = "";
  hideLocalitySuggest();
  await rebuildUrl();
});
$("f-locality-chips")?.addEventListener("click", async (event) => {
  const remove = event.target.closest("[data-remove-city]");
  if (remove) {
    removeFilterLocality(remove.dataset.removeCity);
    await rebuildUrl();
    return;
  }
  const add = event.target.closest("[data-add-city]");
  if (!add) return;
  addFilterLocality(add.dataset.addCity, add.dataset.label);
  await rebuildUrl();
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".locality-picker")) hideLocalitySuggest();
});

$("f-br-paste")?.addEventListener("change", async () => {
  const url = $("f-br-paste").value.trim();
  if (!url) return;
  const response = await fetch("/api/filters/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await response.json();
  if (!response.ok) {
    toast(data.detail || "URL se nepodařilo načíst", "error");
    return;
  }
  filterState = data.filters;
  delete filterState.localities;
  applySourceCatalog("bezrealitky");
  renderFilterGroups();
  $("generated-url").value = data.url;
  toast("Filtry načtené z URL", "info");
});

$("monitor-portals")?.addEventListener("click", async (event) => {
  const chip = event.target.closest("[data-portals]");
  if (!chip) return;
  setMonitorPortals(chip.dataset.portals);
  await rebuildUrl();
});

$("filter-source").addEventListener("click", async (event) => {
  const chip = event.target.closest("[data-source]");
  if (!chip || chip.dataset.source === currentSource()) return;
  const kept = filterState.localities;
  applySourceCatalog(chip.dataset.source);
  filterState = structuredClone(filterCatalog.defaults);
  if (kept?.length) filterState.localities = kept;
  renderFilterGroups();
  await rebuildUrl();
});

$("filters-sample").addEventListener("click", async () => {
  filterState = structuredClone(filterCatalog.sample);
  renderFilterGroups();
  await rebuildUrl();
  toast("Načteny ukázkové filtry", "info");
});

$("filters-clear").addEventListener("click", async () => {
  filterState = structuredClone(filterCatalog.defaults);
  renderFilterGroups();
  await rebuildUrl();
  toast("Filtry vyčištěny", "info");
});

$("filters-to-monitor").addEventListener("click", () => {
  const url = $("generated-url").value;
  if ($("monitor-url")) $("monitor-url").value = url;
  if (filterSuggestedName && $("monitor-name") && !$("monitor-name").value) $("monitor-name").value = filterSuggestedName;
  toast("URL je vyplněná v profilu");
  $("monitor-form")?.scrollIntoView({ behavior: "smooth", block: "start" });
});

function currentTemplateConfig() {
  return {
    username: $("tpl-username").value,
    content_lines: [...document.querySelectorAll("#tpl-lines input")].map((input) => ({ text: input.value })),
    embed: {
      title: $("tpl-title").value,
      url: "{{url}}",
      color: $("tpl-color").value,
      show_image: $("tpl-image").checked,
      footer: $("tpl-footer").value,
      fields: [...document.querySelectorAll("#tpl-fields .field-row")].map((row) => ({
        name: row.querySelector("[data-fname]").value,
        value: row.querySelector("[data-fvalue]").value,
        inline: row.querySelector("[data-finline]").checked,
      })),
    },
  };
}

function renderTemplateEditor() {
  if (!templateState) return;
  const cfg = templateState.config;
  $("template-name").value = templateState.name || "";
  $("tpl-username").value = cfg.username || "Sreality Monitor";
  setEmbedColor(cfg.embed?.color || "#9fe870", false);
  $("tpl-title").value = cfg.embed?.title || "{{name}}";
  $("tpl-footer").value = cfg.embed?.footer || "Sreality monitor";
  $("tpl-image").checked = cfg.embed?.show_image !== false;
  $("tpl-lines").innerHTML = (cfg.content_lines || [])
    .map((line, index) => `<div class="line-row"><input value="${escapeHtml(line.text || "")}" /><button class="btn btn-ghost" type="button" data-del-line="${index}">×</button></div>`)
    .join("");
  $("tpl-fields").innerHTML = (cfg.embed?.fields || [])
    .map(
      (field, index) =>
        `<div class="field-row"><input data-fname value="${escapeHtml(field.name || "")}" /><input data-fvalue value="${escapeHtml(field.value || "")}" /><label class="check"><input type="checkbox" data-finline ${field.inline ? "checked" : ""} /> inline</label><button class="btn btn-ghost" type="button" data-del-field="${index}">×</button></div>`,
    )
    .join("");
  $("var-list").innerHTML = variables
    .map(([key, label]) => `<button class="var-chip" type="button" data-var="${key}" title="${escapeHtml(label)}">{{${key}}}</button>`)
    .join("");
  renderDiscordPreview();
}

function discordMd(text) {
  return escapeHtml(text)
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<u>$1</u>")
    .replace(/~~(.+?)~~/g, "<s>$1</s>")
    .replace(/(^|[^*])\*(?!\*)(.+?)\*(?!\*)/g, "$1<em>$2</em>");
}

function applyVars(text) {
  return String(text || "").replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, key) => sampleVars[key] ?? "");
}

function renderDiscordPreview() {
  const cfg = currentTemplateConfig();
  const content = (cfg.content_lines || []).map((line) => applyVars(line.text)).join("\n");
  const fields = (cfg.embed.fields || [])
    .map((field) => {
      const name = applyVars(field.name);
      const value = applyVars(field.value);
      if (!name || !value) return "";
      return `<div class="discord-field ${field.inline ? "" : "wide"}"><strong>${discordMd(name)}</strong><span>${discordMd(value)}</span></div>`;
    })
    .join("");
  $("discord-preview").innerHTML = `
    <div class="discord-name">${escapeHtml(applyVars(cfg.username))}</div>
    <div class="discord-text">${discordMd(content)}</div>
    <div class="discord-embed" style="border-left-color:${escapeHtml(cfg.embed.color || "#9fe870")}">
      <div class="discord-embed-body">
        <p class="discord-embed-title">${discordMd(applyVars(cfg.embed.title))}</p>
        <div class="discord-fields">${fields}</div>
        <div class="discord-footer">${discordMd(applyVars(cfg.embed.footer))}</div>
      </div>
      ${cfg.embed.show_image && sampleVars.image_url ? `<img src="${sampleVars.image_url}" alt="" />` : ""}
    </div>
  `;
}

$("tpl-lines").addEventListener("input", renderDiscordPreview);
$("tpl-fields").addEventListener("input", renderDiscordPreview);
function normalizeHex(value) {
  const raw = String(value || "").trim();
  const hex = raw.startsWith("#") ? raw : `#${raw}`;
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex.toLowerCase() : "";
}

function setEmbedColor(value, preview = true) {
  const hex = normalizeHex(value) || "#9fe870";
  $("tpl-color").value = hex;
  $("tpl-color-picker").value = hex;
  if (preview) renderDiscordPreview();
}

["tpl-username", "tpl-title", "tpl-footer", "tpl-image"].forEach((id) => {
  $(id).addEventListener("input", renderDiscordPreview);
  $(id).addEventListener("change", renderDiscordPreview);
});
$("tpl-color-picker").addEventListener("input", (event) => setEmbedColor(event.target.value));
$("tpl-color").addEventListener("input", () => {
  const hex = normalizeHex($("tpl-color").value);
  if (hex) $("tpl-color-picker").value = hex;
  renderDiscordPreview();
});
$("tpl-color").addEventListener("change", () => setEmbedColor($("tpl-color").value));

$("tpl-add-line").addEventListener("click", () => {
  templateState.config.content_lines.push({ text: "" });
  renderTemplateEditor();
});

$("tpl-add-field").addEventListener("click", () => {
  templateState.config.embed.fields.push({ name: "", value: "", inline: false });
  renderTemplateEditor();
});

$("tpl-lines").addEventListener("click", (event) => {
  const btn = event.target.closest("[data-del-line]");
  if (!btn) return;
  templateState.config = currentTemplateConfig();
  templateState.config.content_lines.splice(Number(btn.dataset.delLine), 1);
  renderTemplateEditor();
});

$("tpl-fields").addEventListener("click", (event) => {
  const btn = event.target.closest("[data-del-field]");
  if (!btn) return;
  templateState.config = currentTemplateConfig();
  templateState.config.embed.fields.splice(Number(btn.dataset.delField), 1);
  renderTemplateEditor();
});

$("var-list").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-var]");
  if (!chip) return;
  const token = `{{${chip.dataset.var}}}`;
  const active = document.activeElement;
  if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
    const start = active.selectionStart ?? active.value.length;
    active.value = active.value.slice(0, start) + token + active.value.slice(active.selectionEnd ?? start);
  } else if (lastInserted) {
    lastInserted.value += token;
  }
  renderDiscordPreview();
});

document.addEventListener("focusin", (event) => {
  if (event.target.matches("#view-message input, #view-message textarea")) lastInserted = event.target;
});

$("template-select").addEventListener("change", () => {
  const selected = $("template-select").value;
  if (selected === NEW_TEMPLATE_ID) {
    startNewTemplate();
    return;
  }
  const item = (statusCache.templates || []).find((row) => row.id === selected);
  if (item) {
    templateState = structuredClone(item);
    renderTemplateEditor();
  }
});

$("tpl-save").addEventListener("click", async () => {
  const creating = !templateState?.id;
  const saved = await post(
    "/api/templates",
    {
      id: templateState?.id || undefined,
      name: $("template-name").value || "Šablona",
      config: currentTemplateConfig(),
    },
    creating ? "Šablona uložena" : "Šablona upravena",
  );
  if (!saved?.id) return;
  templateState = structuredClone(saved);
  fillTemplateSelects(statusCache.templates || []);
  $("template-select").value = saved.id;
});

$("tpl-new").addEventListener("click", () => {
  startNewTemplate();
  toast("Nová šablona", "info");
});

let refreshBusy = false;

async function refresh() {
  if (refreshBusy) {
    // #region agent log
    dbg("B", "app.js:refresh", "skipped overlapping", { path: location.pathname });
    // #endregion
    return statusCache;
  }
  refreshBusy = true;
  const started = performance.now();
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    // #region agent log
    dbg("A", "app.js:refresh", "status fetched", {
      fetchMs: Math.round(performance.now() - started),
      bytes: JSON.stringify(status).length,
      checking: status.checking,
    });
    // #endregion
    renderStatus(status);
    if (!templateState) startNewTemplate();
    return status;
  } finally {
    refreshBusy = false;
  }
}

async function post(url, body, okMessage) {
  setBusy(true);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Požadavek selhal");
    if (data.status) renderStatus(data.status);
    else if (data.monitors || data.recent) renderStatus(data);
    else await refresh();
    if (okMessage) toast(okMessage);
    return data;
  } catch (err) {
    toast(err.message, "error");
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = err.message;
    }
    return null;
  } finally {
    setBusy(false);
  }
}

toggleBtn.addEventListener("click", async () => {
  const status = await refresh();
  await post(
    status.running ? "/api/monitor/stop" : "/api/monitor/start",
    undefined,
    status.running ? "Hlídání zastaveno" : "Hlídání spuštěno",
  );
});
checkBtn.addEventListener("click", () => post("/api/monitor/check", undefined, "Kontrola dokončena"));
testBtn.addEventListener("click", async () => {
  const data = await post("/api/discord/test");
  if (!data) return;
  if (data.push && data.discord && data.email) toast("Test odeslán na Discord, e-mail i push");
  else if (data.email && data.discord) toast("Test odeslán na Discord i e-mail");
  else if (data.email && data.push) toast("Test odeslán na e-mail i do push notifikací");
  else if (data.push && data.discord) toast("Test odeslán na Discord i do push notifikací");
  else if (data.email) toast("Test odeslán na e-mail");
  else if (data.push) toast("Test odeslán do push notifikací");
  else if ($("notify-push")?.checked) toast("Discord prošel, push se neodeslal. Zkontrolujte oprávnění oznámení v macOS.", "error");
  else toast("Test odeslán na Discord");
});

async function checkUpdates() {
  try {
    const info = await fetch("/api/version").then((res) => res.json());
    if ($("app-version") && info.version) $("app-version").textContent = `v${info.version}`;
    const btn = $("update-btn");
    if (!btn) return info;
    if (info.update_available) {
      btn.hidden = false;
      setLabeled(btn, `Aktualizovat na ${info.latest}`, "download");
      toast(`Je nová verze ${info.latest}`, "info");
    } else {
      btn.hidden = true;
    }
    return info;
  } catch {
    return null;
  }
}

$("update-btn")?.addEventListener("click", async () => {
  toast("Stahuji aktualizaci…", "info");
  const result = await post("/api/update", undefined, "Aktualizace se instaluje, aplikace se restartuje");
  if (result?.restarting) toast("Za chvíli se okno zavře a spustí nová verze", "info");
});

function downloadBackup(url, label) {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
  toast(label);
}

$("export-pack")?.addEventListener("click", () => downloadBackup("/api/backup/pack", "Stahuji zip balíček"));
$("export-json")?.addEventListener("click", () => downloadBackup("/api/backup/json", "Stahuji JSON"));
function commutePlaceholder(index) {
  return index === 0 ? "Práce" : "Metro";
}

function commuteAddressLabel(point) {
  if (point.address) return point.address;
  if (point.lat !== "" && point.lat != null && point.lon !== "" && point.lon != null) {
    return `${point.lat}, ${point.lon}`;
  }
  return "";
}

function renderCommutePoints(points) {
  const host = $("commute-points");
  if (!host) return;
  const rows = (points || []).slice(0, 2);
  if (!rows.length) rows.push({ name: "Práce", address: "", lat: "", lon: "" });
  host.innerHTML = rows
    .map((point, index) => {
      const address = commuteAddressLabel(point);
      const picked = point.lat !== "" && point.lat != null && point.lon !== "" && point.lon != null;
      return `
      <div class="commute-row" data-commute="${index}">
        <label>Název<input data-cname value="${escapeHtml(point.name || commutePlaceholder(index))}" placeholder="${commutePlaceholder(index)}" /></label>
        <label class="commute-search">Adresa
          <input data-caddress value="${escapeHtml(address)}" placeholder="Ulice, číslo, Praha" autocomplete="off" />
          <div class="commute-suggest" hidden></div>
          <input type="hidden" data-clat value="${point.lat ?? ""}" />
          <input type="hidden" data-clon value="${point.lon ?? ""}" />
        </label>
        <button class="btn btn-ghost commute-remove" type="button" data-clear-commute data-icon="x" aria-label="Odebrat bod"></button>
        <p class="commute-picked"${picked ? "" : " hidden"}>${picked ? `Vybraná adresa: ${escapeHtml(address)}` : ""}</p>
      </div>
    `;
    })
    .join("");
  decorateIcons(host);
  if ($("commute-add")) $("commute-add").hidden = host.querySelectorAll(".commute-row").length >= 2;
}

function readCommutePoints() {
  return [...document.querySelectorAll(".commute-row")]
    .map((row, index) => ({
      id: String(index + 1),
      name: row.querySelector("[data-cname]")?.value.trim() || commutePlaceholder(index),
      address: row.querySelector("[data-caddress]")?.value.trim() || "",
      lat: row.querySelector("[data-clat]")?.value,
      lon: row.querySelector("[data-clon]")?.value,
    }))
    .filter((point) => point.lat !== "" && point.lon !== "");
}

function closeCommuteSuggest(row) {
  const box = row?.querySelector(".commute-suggest");
  if (box) {
    box.hidden = true;
    box.innerHTML = "";
  }
}

function pickCommuteAddress(row, item) {
  if (!row || !item) return;
  const input = row.querySelector("[data-caddress]");
  const lat = row.querySelector("[data-clat]");
  const lon = row.querySelector("[data-clon]");
  const picked = row.querySelector(".commute-picked");
  if (input) input.value = item.label || "";
  if (lat) lat.value = item.lat;
  if (lon) lon.value = item.lon;
  if (picked) {
    picked.hidden = false;
    picked.textContent = `Vybraná adresa: ${item.label}`;
  }
  closeCommuteSuggest(row);
  persistCommutePoints("Adresa uložena");
}

async function persistCommutePoints(okMessage) {
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        digest_hour: $("digest-hour")?.value || 8,
        commute_points: readCommutePoints(),
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Uložení selhalo");
    window.dispatchEvent(new CustomEvent("app-settings", { detail: data }));
    if (okMessage) toast(okMessage);
    return data;
  } catch (err) {
    toast(err.message || "Uložení selhalo", "error");
    return null;
  }
}

let commuteTimer = null;
let commuteSaveTimer = null;
let commuteFocus = -1;

async function searchCommuteAddress(row) {
  const input = row.querySelector("[data-caddress]");
  const box = row.querySelector(".commute-suggest");
  const q = input?.value.trim() || "";
  if (!box) return;
  if (q.length < 3) {
    closeCommuteSuggest(row);
    return;
  }
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`);
  const data = await response.json().catch(() => ({}));
  const items = data.items || [];
  commuteFocus = -1;
  if (!response.ok) {
    box.innerHTML = `<p class="empty">Hledání adres teď neběží. Restartuj appku a zkus znovu.</p>`;
    box.hidden = false;
    return;
  }
  if (!items.length) {
    box.innerHTML = `<p class="empty">Nic se nenašlo. Zkus ulici i s Prahou, třeba „Prelova Praha“.</p>`;
    box.hidden = false;
    return;
  }
  box.innerHTML = items
    .map(
      (item) =>
        `<button type="button" data-lat="${escapeHtml(item.lat)}" data-lon="${escapeHtml(item.lon)}">${escapeHtml(item.label)}</button>`,
    )
    .join("");
  box.hidden = false;
}

const PROFILE_KEY = "realitify-profile";
const NOTIFY_KEY = "realitify-notify";

function readStored(key, fallback) {
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(key) || "{}") };
  } catch {
    return fallback;
  }
}

function emptyProfile() {
  return { first: "", last: "", email: "", phone: "", email_verified: false };
}

function initialsFromProfile(profile) {
  const first = (profile.first || "").trim();
  const last = (profile.last || "").trim();
  const letters = `${first.charAt(0)}${last.charAt(0)}`.toUpperCase();
  return letters || ((profile.email || "?").charAt(0).toUpperCase());
}

function shortProfileName(profile) {
  const first = (profile.first || "").trim();
  const last = (profile.last || "").trim();
  if (first && last) return `${first} ${last.charAt(0).toUpperCase()}.`;
  return first || last || profile.email || "Účet";
}

function applyProfile(profile) {
  if ($("profile-first")) $("profile-first").value = profile.first || "";
  if ($("profile-last")) $("profile-last").value = profile.last || "";
  if ($("profile-email")) {
    $("profile-email").value = profile.email || "";
    $("profile-email").readOnly = true;
  }
  if ($("profile-phone")) $("profile-phone").value = profile.phone || "";
  const name = `${profile.first || ""} ${profile.last || ""}`.trim();
  if ($("set-user-name")) $("set-user-name").textContent = name;
  if ($("set-user-mail")) $("set-user-mail").textContent = profile.email || "";
  const initials = initialsFromProfile(profile);
  if ($("set-avatar")) $("set-avatar").textContent = initials;
  if ($("nav-avatar")) $("nav-avatar").textContent = initials;
  if ($("nav-user-name")) $("nav-user-name").textContent = shortProfileName(profile);
  if ($("profile-verified")) $("profile-verified").hidden = !profile.email_verified;
  updateEmailHint();
}

async function loadAccountProfile() {
  const response = await fetch("/api/auth/me");
  if (response.status === 401) {
    location.href = "/prihlaseni";
    throw new Error("unauthenticated");
  }
  const profile = await response.json().catch(() => emptyProfile());
  localStorage.removeItem(PROFILE_KEY);
  applyProfile(profile);
  return profile;
}

function applyNotifyPrefs(prefs) {
  const set = (id, value) => {
    if ($(id)) $(id).checked = Boolean(value);
  };
  set("notify-discord", prefs.discord !== false);
  set("notify-email", prefs.email !== false);
  set("notify-push", prefs.push);
  set("notify-instant", prefs.instant !== false);
  set("notify-quiet", prefs.quiet);
  set("nt-new", prefs.ntNew !== false);
  set("nt-price", prefs.ntPrice !== false);
  set("nt-expire", prefs.ntExpire !== false);
  set("nt-digest", prefs.ntDigest !== false);
  set("nt-tips", prefs.ntTips);
  if ($("quiet-from")) $("quiet-from").value = prefs.quietFrom || "22:00";
  if ($("quiet-to")) $("quiet-to").value = prefs.quietTo || "07:00";
  updatePushHint();
  updateEmailHint();
}

function collectNotifyPrefs() {
  return {
    discord: $("notify-discord")?.checked !== false,
    email: $("notify-email")?.checked !== false,
    push: Boolean($("notify-push")?.checked),
    instant: $("notify-instant")?.checked !== false,
    quiet: Boolean($("notify-quiet")?.checked),
    quietFrom: $("quiet-from")?.value || "22:00",
    quietTo: $("quiet-to")?.value || "07:00",
    ntNew: $("nt-new")?.checked !== false,
    ntPrice: $("nt-price")?.checked !== false,
    ntExpire: $("nt-expire")?.checked !== false,
    ntDigest: $("nt-digest")?.checked !== false,
    ntTips: Boolean($("nt-tips")?.checked),
  };
}

function updateEmailHint() {
  const hint = $("email-hint");
  if (!hint) return;
  const mail = $("profile-email")?.value.trim() || $("set-user-mail")?.textContent.trim() || "";
  hint.textContent = mail
    ? `Zprávy půjdou na ${mail}.`
    : "E-mail bereme z registrace. Doplňte ho v profilu.";
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function isIosDevice() {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
}

function isStandaloneApp() {
  return window.matchMedia("(display-mode: standalone)").matches || Boolean(navigator.standalone);
}

function urlBase64ToUint8Array(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const output = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) output[i] = raw.charCodeAt(i);
  return output;
}

function updatePushHint() {
  const hint = $("push-hint");
  if (!hint) return;
  if (!pushSupported()) {
    hint.textContent = "Tento prohlížeč webové push notifikace nepodporuje.";
    return;
  }
  if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    hint.textContent = "Prohlížeče povolí Push jen na HTTPS (nebo localhost).";
    return;
  }
  if (isIosDevice() && !isStandaloneApp()) {
    hint.textContent = "Na iPhonu/iPadu: Sdílet → Přidat na plochu, pak otevřete Realitify z ikony a zapněte Push.";
    return;
  }
  if ($("notify-push")?.checked) {
    hint.textContent = "Push je zapnutý na tomto zařízení. Stejně ho zapněte v každém prohlížeči, kde chcete upozornění.";
    return;
  }
  hint.textContent = "Zapnutím povolíte oznámení v tomto prohlížeči. Funguje v Chrome, Edge, Firefox, Safari 16+ i v aplikaci na ploše.";
}

async function enablePush() {
  if (!pushSupported()) throw new Error("Tento prohlížeč push nepodporuje");
  if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    throw new Error("Push jde zapnout jen na HTTPS nebo localhost");
  }
  if (isIosDevice() && !isStandaloneApp()) {
    throw new Error("Na iPhonu přidejte web na plochu a otevřete ho z ikony");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Prohlížeč neschválil oznámení");
  const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const vapid = await fetch("/api/push/vapid").then((res) => res.json());
  if (!vapid.publicKey) throw new Error("Chybí VAPID klíč serveru");
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapid.publicKey),
    });
  }
  const response = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subscription.toJSON()),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Odběr se nepodařilo uložit");
}

async function disablePush() {
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription) {
    await fetch("/api/push/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
  }
}

function stopDiscordPoll() {
  if (!discordPoll) return;
  clearInterval(discordPoll);
  discordPoll = null;
}

function renderDiscordLink(data) {
  const hint = $("discord-link-hint");
  const setup = $("discord-link-setup");
  const ok = $("discord-link-ok");
  if (!hint || !setup || !ok) return;
  if (!data?.bot_ready) {
    hint.hidden = false;
    hint.textContent = "Doplňte DISCORD_BOT_TOKEN a DISCORD_GUILD_ID v .env, pozvěte bota na server a restartujte appku.";
    setup.hidden = true;
    ok.hidden = true;
    return;
  }
  if (data.linked) {
    hint.hidden = true;
    setup.hidden = true;
    ok.hidden = false;
    if ($("discord-channel-name")) {
      $("discord-channel-name").textContent = data.channel_name ? `#${data.channel_name}` : "Discord";
    }
    stopDiscordPoll();
    return;
  }
  hint.hidden = false;
  const minutes = Math.max(1, Math.ceil((data.pending_expires_in || 0) / 60));
  hint.textContent = data.pending_code
    ? `Kód platí ještě cca ${minutes} min. Na serveru musíte být přihlášení stejným Discord účtem.`
    : "Vygenerujte kód a na Discord serveru zadejte /link.";
  setup.hidden = false;
  ok.hidden = true;
  if ($("discord-link-cmd")) {
    $("discord-link-cmd").textContent = data.pending_code ? `/link ${data.pending_code}` : "/link …";
  }
  const invite = $("discord-invite");
  if (invite) {
    invite.hidden = !data.server_invite;
    if (data.server_invite) invite.href = data.server_invite;
  }
}

async function loadDiscordLink() {
  const data = await fetch("/api/discord/status").then((res) => (res.ok ? res.json() : null)).catch(() => null);
  if (data) renderDiscordLink(data);
  return data;
}

function startDiscordPoll() {
  stopDiscordPoll();
  discordPoll = setInterval(async () => {
    if (typeof settingsPanel === "function" && settingsPanel() !== "notify") {
      stopDiscordPoll();
      return;
    }
    const data = await loadDiscordLink();
    if (data?.linked) stopDiscordPoll();
  }, 3000);
}

$("discord-link-copy")?.addEventListener("click", async () => {
  const text = $("discord-link-cmd")?.textContent?.trim() || "";
  if (!text || text.endsWith("…")) return;
  try {
    await navigator.clipboard.writeText(text);
    toast("Příkaz zkopírován");
  } catch {
    toast("Kopírování selhalo", "error");
  }
});

$("discord-link-refresh")?.addEventListener("click", async () => {
  const data = await post("/api/discord/link-code", {}, "Kód je připravený");
  if (data) {
    renderDiscordLink(data);
    startDiscordPoll();
  }
});

$("discord-unlink")?.addEventListener("click", async () => {
  const data = await post("/api/discord/unlink", {}, "Discord odpojen");
  if (data) renderDiscordLink(data);
});

async function loadAppSettings() {
  await loadAccountProfile();
  const localNotify = readStored(NOTIFY_KEY, {});
  applyNotifyPrefs(localNotify);
  const data = await fetch("/api/settings").then((res) => res.json()).catch(() => null);
  if (!data) return;
  if (data.notify) applyNotifyPrefs(data.notify);
  else if (Object.keys(localNotify).length) {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notify: localNotify }),
    }).catch(() => null);
  }
  if ($("digest-hour")) {
    const hour = String(data.digest_hour ?? 8);
    const sel = $("digest-hour");
    if (![...sel.options].some((opt) => opt.value === hour)) sel.append(new Option(`${hour}:00`, hour));
    sel.value = hour;
  }
  renderCommutePoints(data.commute_points || []);
  if (data.notify?.push && Notification.permission === "granted") {
    enablePush().catch(() => {});
  }
  updatePushHint();
}

$("catalog-sync-now")?.addEventListener("click", () =>
  post("/api/catalog/sync", {}, "Denní sync katalogu běží na pozadí"),
);

$("digest-test")?.addEventListener("click", async () => {
  const data = await post(
    "/api/digest/test",
    {},
    $("notify-push")?.checked ? "Test odeslán (Discord i Push, pokud jsou zapnuté)" : "Test ranního souhrnu odeslán",
  );
  if (data?.push) toast("Zkontrolujte push oznámení v prohlížeči", "info");
});

$("settings-save")?.addEventListener("click", async () => {
  const notify = collectNotifyPrefs();
  localStorage.setItem(NOTIFY_KEY, JSON.stringify(notify));
  if (notify.push) {
    try {
      await enablePush();
    } catch (err) {
      toast(err.message, "error");
      if ($("notify-push")) $("notify-push").checked = false;
      notify.push = false;
    }
  }
  const saved = await post(
    "/api/settings",
    {
      digest_hour: $("digest-hour")?.value || 8,
      commute_points: readCommutePoints(),
      notify,
    },
    "Nastavení uloženo",
  );
  if (saved) {
    renderCommutePoints(saved.commute_points || []);
    if (saved.notify) applyNotifyPrefs(saved.notify);
  }
  updatePushHint();
  updateEmailHint();
});

$("notify-push")?.addEventListener("change", async () => {
  const on = Boolean($("notify-push")?.checked);
  try {
    if (on) await enablePush();
    else await disablePush();
    localStorage.setItem(NOTIFY_KEY, JSON.stringify(collectNotifyPrefs()));
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notify: collectNotifyPrefs() }),
    });
    toast(on ? "Push notifikace jsou zapnuté" : "Push notifikace jsou vypnuté");
  } catch (err) {
    if ($("notify-push")) $("notify-push").checked = false;
    toast(err.message, "error");
  }
  updatePushHint();
});

$("notify-email")?.addEventListener("change", async () => {
  const on = Boolean($("notify-email")?.checked);
  try {
    localStorage.setItem(NOTIFY_KEY, JSON.stringify(collectNotifyPrefs()));
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notify: collectNotifyPrefs() }),
    });
    if (on) {
      const sent = await post("/api/email/test", {}, "Test šel na e-mail");
      if (!sent) toast("E-mail je zapnutý. Test se neodeslal — nastavte SMTP v .env.", "info");
    } else {
      toast("E-mailové notifikace jsou vypnuté");
    }
  } catch (err) {
    toast(err.message, "error");
  }
});

$("commute-add")?.addEventListener("click", () => {
  const host = $("commute-points");
  const rows = [...(host?.querySelectorAll(".commute-row") || [])];
  if (rows.length >= 2) {
    toast("Jdou jen dva body", "info");
    return;
  }
  const current = rows.map((row, index) => ({
    name: row.querySelector("[data-cname]")?.value.trim() || commutePlaceholder(index),
    address: row.querySelector("[data-caddress]")?.value.trim() || "",
    lat: row.querySelector("[data-clat]")?.value,
    lon: row.querySelector("[data-clon]")?.value,
  }));
  current.push({ name: commutePlaceholder(current.length), address: "", lat: "", lon: "" });
  renderCommutePoints(current);
});

$("commute-points")?.addEventListener("input", (event) => {
  const name = event.target.closest("[data-cname]");
  if (name) {
    clearTimeout(commuteSaveTimer);
    commuteSaveTimer = setTimeout(() => persistCommutePoints(), 400);
    return;
  }
  const input = event.target.closest("[data-caddress]");
  if (!input) return;
  const row = input.closest(".commute-row");
  const lat = row?.querySelector("[data-clat]");
  const lon = row?.querySelector("[data-clon]");
  const picked = row?.querySelector(".commute-picked");
  if (lat) lat.value = "";
  if (lon) lon.value = "";
  if (picked) picked.hidden = true;
  clearTimeout(commuteTimer);
  commuteTimer = setTimeout(() => searchCommuteAddress(row), 280);
});

$("commute-points")?.addEventListener("keydown", (event) => {
  const row = event.target.closest(".commute-row");
  const box = row?.querySelector(".commute-suggest");
  const options = [...(box?.querySelectorAll("button") || [])];
  if (!row || !box || box.hidden) return;
  if (event.key === "Escape") {
    closeCommuteSuggest(row);
    return;
  }
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!options.length) return;
    commuteFocus = (commuteFocus + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options.forEach((btn, index) => btn.classList.toggle("on", index === commuteFocus));
    options[commuteFocus]?.scrollIntoView({ block: "nearest" });
    return;
  }
  if (event.key === "Enter" && commuteFocus >= 0 && options[commuteFocus]) {
    event.preventDefault();
    const btn = options[commuteFocus];
    pickCommuteAddress(row, { label: btn.textContent, lat: btn.dataset.lat, lon: btn.dataset.lon });
  }
});

$("commute-points")?.addEventListener("click", (event) => {
  const row = event.target.closest(".commute-row");
  if (!row) return;
  if (event.target.closest("[data-clear-commute]")) {
    const host = $("commute-points");
    const left = [...(host?.querySelectorAll(".commute-row") || [])]
      .filter((item) => item !== row)
      .map((item, index) => ({
        name: item.querySelector("[data-cname]")?.value.trim() || commutePlaceholder(index),
        address: item.querySelector("[data-caddress]")?.value.trim() || "",
        lat: item.querySelector("[data-clat]")?.value,
        lon: item.querySelector("[data-clon]")?.value,
      }));
    renderCommutePoints(left);
    persistCommutePoints("Bod smazán");
    return;
  }
  const btn = event.target.closest(".commute-suggest button");
  if (!btn) return;
  pickCommuteAddress(row, { label: btn.textContent, lat: btn.dataset.lat, lon: btn.dataset.lon });
});

document.addEventListener("click", (event) => {
  if (event.target.closest(".commute-search")) return;
  document.querySelectorAll(".commute-suggest").forEach((box) => {
    const row = box.closest(".commute-row");
    if (row) closeCommuteSuggest(row);
  });
});

$("export-db")?.addEventListener("click", () => downloadBackup("/api/backup/sqlite", "Stahuji databázi"));

async function importBackup(file) {
  if (!file) return;
  if (!confirm("Nahrát zálohu? Přepíše monitory a případně databázi na tomto počítači.")) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const response = await fetch("/api/backup/import", { method: "POST", body });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Import selhal");
    if (data.status) renderStatus(data.status);
    toast("Záloha je nahraná");
  } catch (err) {
    toast(err.message, "error");
  }
}

$("import-file")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  importBackup(file);
  event.target.value = "";
});

const drop = $("import-drop");
if (drop) {
  ["dragenter", "dragover"].forEach((type) => {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.remove("over");
    });
  });
  drop.addEventListener("drop", (event) => importBackup(event.dataTransfer?.files?.[0]));
}

$("watch-create")?.addEventListener("click", () => {
  const limit = currentBilling().watch_limit;
  const enabled = (statusCache.monitors || []).filter((item) => item.enabled).length;
  if (limit != null && enabled >= limit) {
    toast(`Limit tarifu ${currentBilling().label} je ${limit} aktivních psů. Upgradujte plán.`, "info");
    history.pushState(null, "", "/nastaveni/predplatne");
    applyRoute();
    return;
  }
  openWatchEditor(null);
});
$("watch-back")?.addEventListener("click", () => showWatchHome());
$("profile-save")?.addEventListener("click", async () => {
  const saved = await post(
    "/api/auth/profile",
    {
      first: $("profile-first")?.value.trim() || "",
      last: $("profile-last")?.value.trim() || "",
      phone: $("profile-phone")?.value.trim() || "",
    },
    "Údaje účtu uloženy",
  );
  if (saved) applyProfile(saved);
});
$("profile-wipe")?.addEventListener("click", () => {
  toast("Smazání účtu zatím není k dispozici. Odhlaste se, pokud chcete odejít.", "info");
});
$("set-logout")?.addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method: "POST" });
  localStorage.removeItem(PROFILE_KEY);
  location.href = "/prihlaseni";
});

function passwordScore(value) {
  const rules = {
    len: value.length >= 8,
    upper: /[A-ZÁ-Ž]/.test(value),
    num: /\d/.test(value),
    special: /[^A-Za-zÁ-ž0-9]/.test(value),
  };
  const score = Object.values(rules).filter(Boolean).length;
  return { rules, score };
}

$("pw-new")?.addEventListener("input", () => {
  const { rules, score } = passwordScore($("pw-new").value);
  const meter = $("pw-meter");
  if (meter) meter.dataset.score = String(score);
  if ($("pw-meter-label")) $("pw-meter-label").textContent = score >= 3 ? "Silné heslo" : score ? "Slabé heslo" : "";
  document.querySelectorAll("#pw-reqs [data-rule]").forEach((el) => el.classList.toggle("on", Boolean(rules[el.dataset.rule])));
});
$("pw-save")?.addEventListener("click", async () => {
  const next = $("pw-new")?.value || "";
  if (next !== ($("pw-confirm")?.value || "")) {
    toast("Nové heslo se neshoduje", "error");
    return;
  }
  if (passwordScore(next).score < 3) {
    toast("Heslo je příliš slabé", "error");
    return;
  }
  await post(
    "/api/auth/password",
    { current: $("pw-current")?.value || "", new: next },
    "Heslo je změněné",
  );
});
[
  ["sec-2fa", "2FA v lokální aplikaci zatím není"],
  ["sec-logout-all", "Žádná další zařízení nejsou přihlášená"],
].forEach(([id, message]) => {
  $(id)?.addEventListener("click", () => toast(message, "info"));
});
document.querySelectorAll("[data-billing]").forEach((btn) => {
  btn.addEventListener("click", () => pickBillingPlan(btn.dataset.billing));
});
$("billing-cancel")?.addEventListener("click", () => pickBillingPlan("free"));
$("billing-change")?.addEventListener("click", () => openBillingPortal());

async function boot() {
  const started = performance.now();
  applyRoute();
  const catalogStarted = performance.now();
  const catalogRes = await fetch("/api/filters/catalog");
  filterCatalog = await catalogRes.json();
  // #region agent log
  dbg("C", "app.js:boot", "filters catalog", { ms: Math.round(performance.now() - catalogStarted), bytes: JSON.stringify(filterCatalog).length });
  // #endregion
  filterState = structuredClone(filterCatalog.defaults);
  renderFilterGroups();
  const buildStarted = performance.now();
  if (settingsPanel() === "watch") {
    await rebuildUrl();
  }
  // #region agent log
  dbg("C", "app.js:boot", "rebuildUrl", { ms: Math.round(performance.now() - buildStarted), ran: settingsPanel() === "watch" });
  // #endregion
  const tpl = await fetch("/api/templates").then((res) => res.json());
  variables = tpl.variables || [];
  sampleVars = tpl.sample || {};
  await refresh();
  await loadAppSettings();
  if (settingsPanel() === "billing") await loadBillingInvoices();
  const billingParams = new URLSearchParams(location.search);
  if (billingParams.get("billing") === "success" && billingParams.get("session_id")) {
    await fetch("/api/billing/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: billingParams.get("session_id") }),
    })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "sync failed");
        renderBilling(data);
        toast(`Tarif ${data.label || ""} je aktivní`);
      })
      .catch(() => toast("Platba v Stripe prošla, tarif se teď doplní z účtu", "info"));
    history.replaceState(null, "", "/nastaveni/predplatne");
    applyRoute();
    await refresh();
    await loadBillingInvoices();
  } else if (billingParams.get("billing") === "cancel") {
    toast("Objednávka byla zrušena", "info");
    history.replaceState(null, "", "/nastaveni/predplatne");
    applyRoute();
  }
  const wantedPlan = billingParams.get("checkout");
    if (wantedPlan === "start" || wantedPlan === "pro") {
    history.replaceState(null, "", "/nastaveni/predplatne");
    applyRoute();
    if (wantedPlan !== (currentBilling().plan || "free")) await pickBillingPlan(wantedPlan);
  }
  checkUpdates();
  // #region agent log
  dbg("C", "app.js:boot", "boot done", { ms: Math.round(performance.now() - started), path: location.pathname });
  // #endregion
  const params = new URLSearchParams(location.search);
  const drafted = params.get("url");
  const draftedName = params.get("name");
  if (settingsPanel() === "watch" && drafted) {
    await openWatchEditor({
      name: draftedName || (drafted.includes("bezrealitky") ? "Bezrealitky hledání" : "Nové hledání"),
      search_url: drafted,
      template_id: "default",
      enabled: true,
    });
  }
  const mirrorId = params.get("mirror");
  if (settingsPanel() === "watch" && mirrorId) {
    await openWatchEditor(null);
    await applyMirroredFilters(mirrorId);
  }
}

async function applyMirroredFilters(monitorId) {
  const response = await fetch(`/api/monitors/${encodeURIComponent(monitorId)}/convert`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    toast(data.detail || "Filtry se nepodařilo převést", "error");
    return;
  }
  filterState = structuredClone(data.filters || {});
  filterState.source = data.target === "Bezrealitky" ? "bezrealitky" : "sreality";
  applySourceCatalog(filterState.source);
  filterSuggestedName = data.suggested_name || "";
  renderFilterGroups();
  $("generated-url").value = data.url || "";
  await rebuildUrl();
  toast(`Filtry z ${data.source} na ${data.target}. Upravte je a uložte profil.`, "info");
  if (data.skipped?.length) toast(`Nepřešlo: ${data.skipped.join("; ")}`, "info");
  if (data.notes?.length) toast(`Přibližně: ${data.notes.join("; ")}`, "info");
}

boot();
setInterval(refresh, 4000);
