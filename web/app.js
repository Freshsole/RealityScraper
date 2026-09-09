const $ = (id) => document.getElementById(id);

const livePill = $("live-pill");
const liveLabel = $("live-label");
const toggleBtn = $("toggle");
const checkBtn = $("check");
const testBtn = $("test");
const errorEl = $("error");
const grid = $("grid");
const empty = $("empty");

let hitsMap = null;
let hitsLayer = null;
let lastMapKey = "";
let statusCache = { monitors: [], templates: [] };
let filterCatalog = null;
let filterState = {};
let filterSuggestedName = "";
let templateState = null;
let variables = [];
let sampleVars = {};
let lastInserted = null;

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
  "/": "overview",
  "/prehled": "overview",
  "/nabidka": "catalog",
  "/monitory": "monitors",
  "/filtry": "filters",
  "/zprava": "message",
  "/nastaveni": "settings",
};

const PAGE_TITLES = {
  overview: "Přehled",
  catalog: "Nabídka",
  monitors: "Monitory",
  filters: "Filtry",
  message: "Zpráva",
  settings: "Nastavení",
};

function currentPage() {
  const path = location.pathname.replace(/\/$/, "") || "/";
  return ROUTES[path] || "overview";
}

function applyRoute() {
  const page = currentPage();
  document.querySelectorAll(".tab").forEach((link) => {
    const href = link.getAttribute("href");
    link.classList.toggle("on", ROUTES[href] === page);
  });
  ["overview", "catalog", "monitors", "filters", "message", "settings"].forEach((name) => {
    const view = $(`view-${name}`);
    if (view) view.hidden = name !== page;
  });
  document.title = `${PAGE_TITLES[page]} · Sreality monitor`;
  if (page === "overview" && hitsMap) setTimeout(() => hitsMap.invalidateSize(), 80);
  if (page === "catalog") window.dispatchEvent(new Event("catalog-show"));
}

applyRoute();

function renderStatus(status) {
  statusCache = status;
  const running = Boolean(status.running);
  livePill.dataset.state = status.last_error ? "error" : running ? "on" : "off";
  liveLabel.textContent = status.last_error ? "Chyba" : running ? "Hlídám" : "Zastaveno";
  toggleBtn.textContent = running ? "Zastavit hlídání" : "Spustit hlídání";
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
    ? "Monitory běží. Na Discord jdou nové byty a staré se změnou ceny."
    : "První běh každého monitoru si uloží aktuální nabídku potichu.";
  if (status.last_error) {
    errorEl.hidden = false;
    errorEl.textContent = status.last_error;
  } else {
    errorEl.hidden = true;
    errorEl.textContent = "";
  }

  const recent = status.recent || [];
  grid.innerHTML = "";
  empty.hidden = recent.length > 0;
  for (const item of recent) {
    const area = item.area_m2 ? `${item.area_m2} m²` : "rozloha neuvedena";
    const card = document.createElement("a");
    card.className = "listing";
    card.href = item.url;
    card.target = "_blank";
    card.rel = "noreferrer";
    const kindLabel = { changed: "Změna", refresh: "Obnoveno", new: "Nový" }[item.last_kind] || "Nový";
    card.innerHTML = `
      ${item.image_url ? `<img src="${item.image_url}" alt="" />` : ""}
      <div class="listing-body">
        <h3>${escapeHtml(item.name)}</h3>
        <div class="price">${escapeHtml(item.price_label || "")}</div>
        <div class="meta">${escapeHtml(item.monitor_name || "")} · ${escapeHtml(kindLabel)} · ${escapeHtml(item.disposition)} · ${escapeHtml(area)}<br />${escapeHtml(item.locality || "")}</div>
      </div>
    `;
    grid.appendChild(card);
  }
  renderMap(recent);
  renderMonitors(status.monitors || []);
  fillTemplateSelects(status.templates || []);
}

function ensureMap() {
  if (hitsMap || typeof L === "undefined") return;
  hitsMap = L.map("hits-map", { scrollWheelZoom: false }).setView([50.08, 14.44], 12);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap",
    maxZoom: 19,
  }).addTo(hitsMap);
  hitsLayer = L.layerGroup().addTo(hitsMap);
}

function renderMap(items) {
  ensureMap();
  if (!hitsMap || !hitsLayer) return;
  const mappable = items.filter((item) => item.lat != null && item.lon != null);
  const nextKey = mappable.map((item) => `${item.id}:${item.lat}:${item.lon}:${item.image_url || ""}`).join("|");
  if (nextKey === lastMapKey) {
    setTimeout(() => hitsMap.invalidateSize(), 80);
    return;
  }
  lastMapKey = nextKey;
  hitsLayer.clearLayers();
  const points = [];
  for (const item of mappable) {
    const photo = item.image_url ? `<img src="${item.image_url}" alt="" />` : `<span class="photo-pin-fallback"></span>`;
    const pin = L.divIcon({
      className: "photo-pin-wrap",
      html: `<div class="photo-pin">${photo}<i></i></div>`,
      iconSize: [56, 68],
      iconAnchor: [28, 64],
      popupAnchor: [0, -56],
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
    hitsLayer.addLayer(marker);
    points.push([item.lat, item.lon]);
  }
  if (points.length === 1) hitsMap.setView(points[0], 14);
  else if (points.length > 1) hitsMap.fitBounds(points, { padding: [48, 48], maxZoom: 14 });
  setTimeout(() => hitsMap.invalidateSize(), 80);
}

function portalLabel(url) {
  return String(url || "").includes("bezrealitky") ? "Bezrealitky" : "Sreality";
}

function portalIcon(url) {
  return String(url || "").includes("bezrealitky")
    ? "/static/icons/bezrealitky.svg"
    : "/static/icons/sreality.svg";
}

function renderMonitors(items) {
  const list = $("monitor-list");
  if (!list) return;
  list.innerHTML = items
    .map((item) => {
      const portal = portalLabel(item.search_url);
      return `
      <article class="monitor-card">
        <div class="monitor-main">
          <img class="monitor-logo" src="${portalIcon(item.search_url)}" alt="${escapeHtml(portal)}" />
          <div class="monitor-copy">
            <strong>${escapeHtml(item.name)}</strong>
            <p>${escapeHtml(portal)} · ${escapeHtml(item.search_url)}</p>
            <p>${item.enabled ? "Zapnutý" : "Vypnutý"} · ${item.seeded ? "nasazený" : "čeká na první běh"} · ${item.tracked} ID · šablona ${escapeHtml(item.template_id || "default")}</p>
            <div class="actions">
              <button class="btn btn-ghost" type="button" data-edit-monitor="${item.id}">Upravit</button>
              <button class="btn btn-ghost" type="button" data-check-monitor="${item.id}">Zkontrolovat</button>
              <button class="btn btn-ghost" type="button" data-mirror-monitor="${item.id}">Zkopírovat na ${portal === "Bezrealitky" ? "Sreality" : "Bezrealitky"}</button>
              <button class="btn btn-ghost" type="button" data-del-monitor="${item.id}">Smazat</button>
            </div>
          </div>
        </div>
      </article>
    `;
    })
    .join("");
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
  $("monitor-webhook").value = item?.webhook_url || "";
  $("monitor-template").value = item?.template_id || "default";
  $("monitor-enabled").checked = item ? Boolean(item.enabled) : true;
}

$("monitor-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const editing = Boolean($("monitor-id").value);
  const saved = await post(
    "/api/monitors",
    {
      id: $("monitor-id").value || undefined,
      name: $("monitor-name").value,
      search_url: $("monitor-url").value,
      webhook_url: $("monitor-webhook").value,
      template_id: $("monitor-template").value,
      enabled: $("monitor-enabled").checked,
    },
    editing ? "Monitor upraven" : "Monitor uložen",
  );
  if (saved) fillMonitorForm(null);
});

$("monitor-reset").addEventListener("click", () => {
  fillMonitorForm(null);
  toast("Formulář vyčištěn", "info");
});

$("monitor-list").addEventListener("click", async (event) => {
  const edit = event.target.closest("[data-edit-monitor]");
  const del = event.target.closest("[data-del-monitor]");
  const check = event.target.closest("[data-check-monitor]");
  const mirror = event.target.closest("[data-mirror-monitor]");
  if (edit) {
    const item = (statusCache.monitors || []).find((row) => row.id === edit.dataset.editMonitor);
    fillMonitorForm(item);
    toast(`Upravuješ ${item?.name || "monitor"}`, "info");
    $("monitor-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (del && confirm("Smazat monitor i jeho uložená ID?")) {
    const response = await fetch(`/api/monitors/${del.dataset.delMonitor}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast(data.detail || "Monitor se nepodařilo smazat", "error");
      return;
    }
    toast("Monitor smazán");
    await refresh();
  }
  if (check) await post("/api/monitor/check", { monitor_id: check.dataset.checkMonitor }, "Kontrola monitoru dokončena");
  if (mirror) {
    location.assign(`/filtry?mirror=${encodeURIComponent(mirror.dataset.mirrorMonitor)}`);
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
    ["Lokalita", "districts", cat.districts],
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
  const response = await fetch("/api/filters/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filters: filterState }),
  });
  const data = await response.json();
  $("generated-url").value = data.url;
}

$("filter-groups").addEventListener("click", async (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  const key = chip.dataset.filter;
  const value = chip.dataset.value;
  if (chip.dataset.single) {
    filterState[key] = (filterState[key] || [])[0] === value ? [] : [value];
    if (key === "districts") {
      const label = chip.textContent.trim();
      filterState.osm_value = filterState[key][0] ? label : "";
    }
    renderFilterGroups();
  } else {
    const current = new Set(filterState[key] || []);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    filterState[key] = [...current];
    chip.classList.toggle("on");
    if (key === "districts") {
      const first = filterState.districts?.[0];
      filterState.osm_value = first ? chip.closest(".filter-group").querySelector(".chip.on")?.textContent.trim() || first : "";
    }
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

async function searchLocality() {
  const q = $("f-br-locality-q")?.value.trim();
  const host = $("f-br-locality-hits");
  if (!q || !host) return;
  const response = await fetch(`/api/filters/locality?q=${encodeURIComponent(q)}`);
  const data = await response.json();
  host.innerHTML = (data.items || [])
    .map((item) => `<button type="button" class="chip" data-osm="${escapeHtml(item.id)}" data-label="${escapeHtml(item.label)}">${escapeHtml(item.label)}</button>`)
    .join("") || `<p class="empty">Nic se nenašlo</p>`;
}

$("f-br-locality-btn")?.addEventListener("click", searchLocality);
$("f-br-locality-q")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    searchLocality();
  }
});
$("f-br-locality-hits")?.addEventListener("click", async (event) => {
  const chip = event.target.closest("[data-osm]");
  if (!chip) return;
  const current = new Set(filterState.districts || []);
  current.add(chip.dataset.osm);
  filterState.districts = [...current];
  filterState.osm_value = chip.dataset.label;
  renderFilterGroups();
  await rebuildUrl();
  toast(`Přidaná lokalita ${chip.dataset.label}`, "info");
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
  applySourceCatalog("bezrealitky");
  renderFilterGroups();
  $("generated-url").value = data.url;
  toast("Filtry načtené z URL", "info");
});

$("filter-source").addEventListener("click", async (event) => {
  const chip = event.target.closest("[data-source]");
  if (!chip || chip.dataset.source === currentSource()) return;
  applySourceCatalog(chip.dataset.source);
  filterState = structuredClone(filterCatalog.defaults);
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
  const params = new URLSearchParams({ url });
  if (filterSuggestedName) params.set("name", filterSuggestedName);
  toast("URL přenesena do nového monitoru");
  location.assign(`/monitory?${params}`);
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

async function refresh() {
  const response = await fetch("/api/status");
  const status = await response.json();
  renderStatus(status);
  if (!templateState) startNewTemplate();
  return status;
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
testBtn.addEventListener("click", () => post("/api/discord/test", undefined, "Test odeslán na Discord"));

async function checkUpdates() {
  try {
    const info = await fetch("/api/version").then((res) => res.json());
    if ($("app-version") && info.version) $("app-version").textContent = `v${info.version}`;
    const btn = $("update-btn");
    if (!btn) return info;
    if (info.update_available) {
      btn.hidden = false;
      btn.textContent = `Aktualizovat na ${info.latest}`;
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

async function boot() {
  applyRoute();
  const catalogRes = await fetch("/api/filters/catalog");
  filterCatalog = await catalogRes.json();
  filterState = structuredClone(filterCatalog.defaults);
  renderFilterGroups();
  await rebuildUrl();
  const tpl = await fetch("/api/templates").then((res) => res.json());
  variables = tpl.variables || [];
  sampleVars = tpl.sample || {};
  await refresh();
  checkUpdates();
  const params = new URLSearchParams(location.search);
  const drafted = params.get("url");
  const draftedName = params.get("name");
  if (currentPage() === "monitors" && drafted) {
    fillMonitorForm({
      name: draftedName || (drafted.includes("bezrealitky") ? "Bezrealitky hledání" : "Nové hledání"),
      search_url: drafted,
      template_id: "default",
      enabled: true,
    });
  }
  const mirrorId = params.get("mirror");
  if (currentPage() === "filters" && mirrorId) {
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
  toast(`Filtry z ${data.source} na ${data.target}. Uprav je a pak vytvoř monitor.`, "info");
  if (data.skipped?.length) toast(`Nepřešlo: ${data.skipped.join("; ")}`, "info");
  if (data.notes?.length) toast(`Přibližně: ${data.notes.join("; ")}`, "info");
}

boot();
setInterval(refresh, 4000);
