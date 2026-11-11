(() => {
  const $ = (id) => document.getElementById(id);
  const listEl = $("catalog-list");
  if (!listEl) return;

  const LIMIT = 36;
  const STREET_ZOOM = 16;
  const STACK_PX = 28; // piny blíž než tolik px = jedno místo (centroid Bazoš)


  function addBaseTiles(map) {
    if (window.RFMapBasemap) window.RFMapBasemap.addTo(map);
    else L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", { maxZoom: 20, subdomains: "abcd" }).addTo(map);
  }

  let catalogMap = null;
  let catalogLayer = null;
  let pinItems = [];
  let circleLayer = null;
  let placeLayer = null;
  let drawingCircle = false;
  let circleFilter = null;
  let selectedPlaces = [];
  let watchProfiles = [];
  let facetMonitors = [];
  let placeSuggestItems = [];
  let placeSuggestTimer = 0;
  let placeSuggestFocus = -1;
  let placeSearchAbort = null;
  let placeSearchSeq = 0;
  let catalogSeq = 0;
  let catalogAbort = null;
  let radiusTimer = null;
  let mapReloadTimer = 0;
  let lastMapQueryKey = "";
  let ignoreMapMove = 0;
  let needPlaceFit = false;
  const PRAHA_BOUNDS = { south: "49.94", north: "50.177", west: "14.22", east: "14.71" };
  const DISTRICT_OSM = {
    "praha-1": { id: "R15107966", label: "Praha 1" },
    "praha-2": { id: "R19999122", label: "Praha 2" },
    "praha-3": { id: "R19999121", label: "Praha 3" },
    "praha-4": { id: "R19999068", label: "Praha 4" },
    "praha-5": { id: "R19999086", label: "Praha 5" },
    "praha-6": { id: "R19999115", label: "Praha 6" },
    "praha-7": { id: "R19999114", label: "Praha 7" },
    "praha-8": { id: "R19999109", label: "Praha 8" },
    "praha-9": { id: "R19999082", label: "Praha 9" },
    "praha-10": { id: "R19999075", label: "Praha 10" },
  };
  let markerByKey = new Map();
  let clusterByKey = new Map();
  let hoverLayer = null;
  let hoverMarker = null;
  let hoverKey = null;
  let lastItems = [];
  let lastCatalogQuery = location.search;
  let total = 0;
  let pinned = 0; // offers that have a pin on the map (== total whenever the map area restricts the list)
  let offset = 0;
  let loaded = false;
  let lightboxPhotos = [];
  let lightboxIndex = 0;
  let detailMap = null;
  const selected = {
    portal: "",
    dispositions: new Set(),
    amenities: new Set(),
    offers: new Set(["pronajem"]),
    estates: new Set(),
    districts: new Set(),
    ownership: new Set(),
    conditions: new Set(),
    buildings: new Set(),
    equipped: new Set(),
    roommate: "",
    pets: "",
    short_term: "",
    monitor: "",
    sort: "newest",
    status: "",
    discounted: false,
    hits: "",
  };
  let focusedIndex = -1;
  let compareUrls = [];
  let appSettings = { digest_hour: 8, commute_points: [] };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function uniqueOffers(items) {
    const seen = new Set();
    return (items || []).filter((item) => {
      const key = item.canonical_key || item.listing_key || item.url || `${item.monitor_id}:${item.id}`;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function listingLinks(item) {
    const links = (item?.links || []).filter((row) => row?.url && !row.gone);
    if (links.length) return links;
    return item?.url ? [{ url: item.url, label: item.portal, portal: item.portal, agency: "" }] : [];
  }

  function portalIcon(portal) {
    if (typeof window.portalIconOf === "function") return window.portalIconOf(portal);
    const key = String(portal || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    if (key.includes("idnes")) return "/static/icons/idnes.svg";
    if (key.includes("bazos")) return "/static/icons/bazos.svg";
    if (key.includes("bezrealitky")) return "/static/icons/bezrealitky.svg";
    return "/static/icons/sreality.svg";
  }

  function portalIcons(item) {
    const seen = new Set();
    const icons = [];
    const links = listingLinks(item);
    for (const link of links) {
      const key = String(link.portal || link.label || "").toLowerCase() || link.url;
      if (seen.has(key)) continue;
      seen.add(key);
      const label = link.label || link.portal || item.portal || "Web";
      icons.push(`<img class="offer-portal" src="${portalIcon(link.portal || label)}" alt="${escapeHtml(label)}" />`);
    }
    if (!icons.length) icons.push(`<img class="offer-portal" src="${portalIcon(item.portal)}" alt="${escapeHtml(item.portal || "")}" />`);
    return `<div class="offer-portals">${icons.join("")}</div>`;
  }

  function offerSourceLine(item) {
    const parts = listingLinks(item).map((link) => {
      const agency = link.agency ? ` · ${link.agency}` : "";
      return `${link.label || link.portal || "Web"}${agency}`;
    });
    const unique = [...new Set(parts.filter(Boolean))];
    if (unique.length <= 1) return "";
    return `<p class="offer-sources">${escapeHtml(unique.join(" · "))}</p>`;
  }

  function formatTime(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString("cs-CZ", {
      day: "numeric",
      month: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatKcDelta(n) {
    const abs = formatKc(Math.abs(n));
    if (n > 0) return `+${abs}`;
    if (n < 0) return `−${abs}`;
    return formatKc(0);
  }

  function snippet(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  const ICON_SAVE = icon("bookmark");
  const ICON_HIDE = icon("eyeOff");
  const ICON_SHOW = icon("eye");

  function detailActionButton(id, label, icon, on) {
    return `<button type="button" class="detail-action${on ? " on" : ""}" id="${id}">${icon}<span>${escapeHtml(label)}</span></button>`;
  }

  function setDetailAction(id, label, on, icon) {
    const btn = $(id);
    if (!btn) return;
    const span = btn.querySelector("span");
    if (span) span.textContent = label;
    if (icon) {
      const svg = btn.querySelector("svg");
      if (svg) svg.outerHTML = icon;
    }
    btn.classList.toggle("on", Boolean(on));
  }

  // Exactly the area the user can see: the list, the total and the pins all use it.
  function mapBoundsParams() {
    const skip = Boolean(selectedPlaces.length || circleFilter || selected.monitor);
    if (skip) return {};
    ensureMap();
    const bounds = catalogMap?.getBounds?.();
    if (!bounds || !bounds.isValid?.()) return { ...PRAHA_BOUNDS };
    return {
      south: String(bounds.getSouth()),
      north: String(bounds.getNorth()),
      west: String(bounds.getWest()),
      east: String(bounds.getEast()),
    };
  }

  function filters() {
    return {
      q: $("cat-q") ? $("cat-q").value : "",
      portal: selected.portal,
      disposition: [...selected.dispositions].join(","),
      price_from: $("cat-price-from").value,
      price_to: $("cat-price-to").value,
      area_from: $("cat-area-from").value,
      area_to: $("cat-area-to").value,
      monitor_id: selected.monitor,
      amenities: [...selected.amenities].join(","),
      offer: [...selected.offers].join(","),
      estate: [...selected.estates].join(","),
      district: [...selected.districts].join(","),
      places: selectedPlaces.map(placeQueryToken).join(","),
      ownership: [...selected.ownership].join(","),
      condition: [...selected.conditions].join(","),
      building: [...selected.buildings].join(","),
      equipped: [...selected.equipped].join(","),
      roommate: selected.roommate,
      pets: selected.pets,
      short_term: selected.short_term,
      lat: circleFilter ? String(circleFilter.lat) : "",
      lon: circleFilter ? String(circleFilter.lon) : "",
      radius_m: circleFilter ? String(circleFilter.radius_m) : "",
      sort: selected.sort,
      status: selected.status,
      discounted: selected.discounted ? "1" : "",
      hits: selected.hits,
      ...mapBoundsParams(),
    };
  }

  const FILTER_STATE_KEY = "nabidka-state";
  let restoredFilterState = false;

  function filterStateParams() {
    const params = new URLSearchParams();
    const skipUrl = new Set(["south", "north", "west", "east", "include_pins"]);
    for (const [key, value] of Object.entries(filters())) {
      if (value && !skipUrl.has(key)) params.set(key, String(value));
    }
    return params;
  }

  function persistFilterState(params) {
    try {
      localStorage.setItem(FILTER_STATE_KEY, params.toString());
    } catch {
      /* ignore quota */
    }
  }

  function writeUrlState() {
    if (location.pathname.replace(/\/$/, "") !== "/nabidka") return;
    const params = filterStateParams();
    persistFilterState(params);
    const query = params.toString();
    history.replaceState(null, "", query ? `/nabidka?${query}` : "/nabidka");
  }

  // Odkaz z homepage / hero hledání nese jen název místa (?q=Brno). Změníme ho na skutečný filtr
  // lokality, aby seznam i mapa ukazovaly právě to místo (a ne výchozí Prahu).
  let pendingQueryPlace = "";
  async function resolveQueryPlace() {
    const q = pendingQueryPlace;
    pendingQueryPlace = "";
    if (q.length < 2 || selectedPlaces.length) return;
    try {
      const response = await fetch(`/api/places/search?q=${encodeURIComponent(q)}`, { signal: AbortSignal.timeout(6000) });
      const data = await response.json();
      const hit = (data.items || []).find((row) => row.id && row.kind !== "street");
      if (!hit) return;
      const place = { id: hit.id, label: hit.label || hit.id, kind: "area", lat: hit.lat, lon: hit.lon, buffer_m: 0 };
      if (Array.isArray(hit.extent) && hit.extent.length >= 4) place.extent = hit.extent;
      if (hit.geojson && hit.geojson.type !== "Point") place.geojson = hit.geojson;
      selectedPlaces.push(place);
      const slug = slugForPlace(hit.id);
      if (slug) selected.districts.add(slug);
      if ($("cat-q")) $("cat-q").value = "";
      needPlaceFit = true;
      renderPlaceChips();
      syncFilterUi();
    } catch {
      /* zůstane textové hledání */
    }
  }

  function readUrlState(params) {
    if (!(params instanceof URLSearchParams)) params = new URLSearchParams(location.search);
    selected.portal = params.get("portal") || "";
    selected.status = params.get("status") || "";
    selected.discounted = params.get("discounted") === "1";
    selected.hits = params.get("hits") || "";
    selected.monitor = params.get("monitor_id") || "";
    selected.sort = params.get("sort") || "newest";
    selected.dispositions = new Set((params.get("disposition") || "").split(",").filter(Boolean));
    selected.amenities = new Set((params.get("amenities") || "").split(",").filter(Boolean));
    selected.offers = new Set(
      (params.get("offer") || (params.get("monitor_id") ? "" : "pronajem")).split(",").filter(Boolean),
    );
    selected.estates = new Set((params.get("estate") || "").split(",").filter(Boolean));
    selected.districts = new Set((params.get("district") || "").split(",").filter(Boolean));
    selected.ownership = new Set((params.get("ownership") || "").split(",").filter(Boolean));
    selected.conditions = new Set((params.get("condition") || "").split(",").filter(Boolean));
    selected.buildings = new Set((params.get("building") || "").split(",").filter(Boolean));
    selected.equipped = new Set((params.get("equipped") || "").split(",").filter(Boolean));
    selected.roommate = params.get("roommate") || "";
    selected.pets = params.get("pets") || "";
    selected.short_term = params.get("short_term") || "";
    if ($("cat-q")) $("cat-q").value = params.get("q") || "";
    pendingQueryPlace =
      !params.get("places") && !params.get("district") && !params.get("lat") ? (params.get("q") || "").trim() : "";
    if ($("cat-price-from")) $("cat-price-from").value = params.get("price_from") || "";
    if ($("cat-price-to")) $("cat-price-to").value = params.get("price_to") || "";
    if ($("cat-area-from")) $("cat-area-from").value = params.get("area_from") || "";
    if ($("cat-area-to")) $("cat-area-to").value = params.get("area_to") || "";
    selectedPlaces = (params.get("places") || "")
      .split(",")
      .map((token) => token.trim())
      .filter(Boolean)
      .map(placeFromToken);
    for (const place of selectedPlaces) {
      const slug = slugForPlace(place.id);
      if (slug) selected.districts.add(slug);
    }
    syncPlacesFromDistricts();
    renderPlaceChips();
    if (params.get("lat") && params.get("lon") && params.get("radius_m")) {
      circleFilter = {
        lat: Number(params.get("lat")),
        lon: Number(params.get("lon")),
        radius_m: Number(params.get("radius_m")),
      };
    }
    $("cat-portals")?.querySelectorAll(".chip").forEach((chip) => {
      chip.classList.toggle("on", (chip.dataset.portal || "") === selected.portal);
    });
    syncStatusChips();
    syncFilterUi();
  }

  function syncStatusChips() {
    $("cat-status")?.querySelectorAll("[data-status]").forEach((chip) => {
      const active = !selected.discounted && (chip.dataset.status || "") === selected.status;
      chip.classList.toggle("on", active);
    });
    $("cat-status")?.querySelector("[data-discounted]")?.classList.toggle("on", selected.discounted);
    $("cat-hits")?.querySelectorAll("[data-hits]").forEach((chip) => {
      chip.classList.toggle("on", (chip.dataset.hits || "") === selected.hits);
    });
    const kicker = document.querySelector("#view-catalog .kicker");
    if (kicker) {
      if (selected.hits === "today") kicker.textContent = "Zásahy z dneška · můžeš vybrat monitor a přidat další filtry";
      else if (selected.hits === "notified") kicker.textContent = "Všechny zásahy z monitorů · můžeš filtrovat dál";
      else kicker.textContent = document.body.classList.contains("guest") ? "Aktuální nabídky ze všech portálů" : "";
    }
  }

  function savedViews() {
    try {
      return JSON.parse(localStorage.getItem("nabidka-views") || "[]");
    } catch {
      return [];
    }
  }

  function slugForPlace(id) {
    return Object.keys(DISTRICT_OSM).find((slug) => DISTRICT_OSM[slug].id === id) || "";
  }

  function placeQueryToken(place) {
    const lat = Number(place.lat);
    const lon = Number(place.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return `${place.id}~${lat}~${lon}`;
    return place.id;
  }

  function placeFromToken(token) {
    const parts = String(token || "").split("~");
    const id = parts[0];
    const lat = parts.length >= 3 ? Number(parts[1]) : NaN;
    const lon = parts.length >= 3 ? Number(parts[2]) : NaN;
    const mapped = Object.values(DISTRICT_OSM).find((row) => row.id === id);
    const kind = mapped ? "area" : id.startsWith("W") ? "street" : "area";
    let label = mapped?.label || id;
    if (parts.length >= 4) {
      try {
        const decoded = decodeURIComponent(parts.slice(3).join("~"));
        if (decoded) label = decoded;
      } catch {
        /* keep label */
      }
    }
    const place = { id, label, kind };
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      place.lat = lat;
      place.lon = lon;
      place.buffer_m = kind === "street" ? 45 : 0;
    }
    return place;
  }

  function placeLatLon(place) {
    const lat = Number(place.lat);
    const lon = Number(place.lon);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return { lat, lon };
    const coords = place.geojson?.coordinates;
    if (place.geojson?.type === "Point" && Array.isArray(coords) && coords.length >= 2) {
      return { lat: Number(coords[1]), lon: Number(coords[0]) };
    }
    return null;
  }

  function syncPlacesFromDistricts() {
    for (const slug of selected.districts) {
      const mapped = DISTRICT_OSM[slug];
      if (mapped && !selectedPlaces.some((row) => row.id === mapped.id)) {
        selectedPlaces.push({ id: mapped.id, label: mapped.label, kind: "area" });
      }
    }
    selectedPlaces = selectedPlaces.filter((place) => {
      const slug = slugForPlace(place.id);
      return !slug || selected.districts.has(slug);
    });
    renderPlaceChips();
    if (selectedPlaces.length) needPlaceFit = true;
  }

  function renderPlaceChips() {
    const host = $("cat-place-chips");
    if (!host) return;
    host.innerHTML = selectedPlaces
      .map(
        (place) =>
          `<span class="chip on">${escapeHtml(place.label || place.id)}<button type="button" class="chip-x" data-del-place="${escapeHtml(place.id)}" aria-label="Odebrat místo">×</button></span>`,
      )
      .join("");
  }

  async function ensurePlaceGeoms() {
    const missing = selectedPlaces.filter((row) => !row.geojson || row.geojson.type === "Point");
    if (!missing.length) return;
    const response = await fetch(`/api/places/geometry?ids=${encodeURIComponent(missing.map(placeQueryToken).join(","))}`, {
      signal: AbortSignal.timeout(20000),
    });
    const data = await response.json().catch(() => ({ items: [] }));
    applyPlaceGeoms(data.items);
  }

  async function addPlace(item) {
    if (!item?.id) return;
    if (selectedPlaces.some((row) => row.id === item.id)) {
      hidePlaceSuggest();
      if ($("cat-q")) $("cat-q").value = "";
      drawPlaceLayer(false);
      goToSelection();
      return;
    }
    const kind = item.kind || (String(item.id).startsWith("W") ? "street" : "area");
    const place = {
      id: item.id,
      label: item.label || item.id,
      kind,
      lat: item.lat,
      lon: item.lon,
      buffer_m: item.buffer_m || (kind === "street" ? 45 : 0),
    };
    if (Array.isArray(item.extent) && item.extent.length >= 4) place.extent = item.extent;
    if (item.geojson && item.geojson.type !== "Point") place.geojson = item.geojson;
    selectedPlaces.push(place);
    const slug = slugForPlace(item.id);
    if (slug) selected.districts.add(slug);
    renderPlaceChips();
    syncFilterUi();
    hidePlaceSuggest();
    if ($("cat-q")) $("cat-q").value = "";
    needPlaceFit = true;
    const count = $("catalog-count");
    if (count) count.textContent = "Načítám…";
    loadCatalog();
  }

  function removePlace(id) {
    selectedPlaces = selectedPlaces.filter((row) => row.id !== id);
    const slug = slugForPlace(id);
    if (slug) selected.districts.delete(slug);
    renderPlaceChips();
    syncFilterUi();
    needPlaceFit = Boolean(selectedPlaces.length);
    renderMapPins([]);
    drawPlaceLayer(needPlaceFit);
    // Drop any in-flight place-scoped request so reload with map bounds can start.
    catalogAbort?.abort();
    catalogBusy = false;
    catalogQueued = false;
    loadCatalog();
  }

  function hidePlaceSuggest() {
    const box = $("cat-place-suggest");
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
    placeSuggestFocus = -1;
  }

  function paintPlaceSuggest() {
    const box = $("cat-place-suggest");
    if (!box) return;
    box.querySelectorAll("[data-place-suggest]").forEach((btn, index) => {
      btn.classList.toggle("is-active", index === placeSuggestFocus);
      btn.setAttribute("aria-selected", index === placeSuggestFocus ? "true" : "false");
    });
  }

  async function searchPlaces(q) {
    const box = $("cat-place-suggest");
    if (!box) return;
    if (q.length < 2) {
      hidePlaceSuggest();
      return;
    }
    const seq = ++placeSearchSeq;
    placeSearchAbort?.abort();
    const ac = new AbortController();
    placeSearchAbort = ac;
    let data = { items: [] };
    try {
      const response = await fetch(`/api/places/search?q=${encodeURIComponent(q)}`, { signal: ac.signal });
      data = await response.json().catch(() => ({ items: [] }));
    } catch (err) {
      if (err?.name === "AbortError") return;
    }
    if (seq !== placeSearchSeq) return;
    placeSuggestItems = data.items || [];
    placeSuggestFocus = -1;
    box.hidden = false;
    if (!placeSuggestItems.length) {
      box.innerHTML = `<button type="button" disabled>Nic se nenašlo</button>`;
      return;
    }
    box.innerHTML = placeSuggestItems
      .map((item, index) => {
        const kind = item.kind === "street" ? "ulice" : "oblast";
        return `<button type="button" data-place-suggest="${index}"><span>${escapeHtml(item.label)}</span><small>${kind}</small></button>`;
      })
      .join("");
  }

  function watchChipItems() {
    const items = [...watchProfiles];
    if (selected.monitor && !items.some((item) => item.id === selected.monitor)) {
      const extra = facetMonitors.find((item) => item.id === selected.monitor);
      items.push(extra || { id: selected.monitor, name: "Monitor" });
    }
    return items;
  }

  function renderViews() {
    const host = $("catalog-views");
    if (!host) return;
    const views = savedViews();
    const watches = watchChipItems()
      .map(
        (item) =>
          `<button type="button" class="chip${selected.monitor === item.id ? " on" : ""}${item.enabled === false ? " is-off" : ""}" data-watch="${escapeHtml(item.id)}">${escapeHtml(item.name)}</button>`,
      )
      .join("");
    host.innerHTML = `
      ${watches}
      ${views
        .map(
          (view) =>
            `<button type="button" class="chip" data-view="${escapeHtml(view.id)}">${escapeHtml(view.name)}</button>
             <button type="button" class="chip-x" data-del-view="${escapeHtml(view.id)}" aria-label="Smazat pohled">×</button>`,
        )
        .join("")}
      <button type="button" class="chip" id="view-save" data-icon="bookmark">Uložit pohled</button>
    `;
    decorateIcons(host);
  }

  function extraFilterCount() {
    const offerCount = selected.offers.size === 1 && selected.offers.has("pronajem") ? 0 : selected.offers.size;
    return (
      selected.dispositions.size +
      selected.amenities.size +
      offerCount +
      selected.estates.size +
      selected.districts.size +
      selected.ownership.size +
      selected.conditions.size +
      selected.buildings.size +
      selected.equipped.size +
      (selected.roommate ? 1 : 0) +
      (selected.pets ? 1 : 0) +
      (selected.short_term ? 1 : 0) +
      (circleFilter ? 1 : 0) +
      selectedPlaces.length +
      (selected.monitor ? 1 : 0) +
      (selected.sort && selected.sort !== "newest" ? 1 : 0)
    );
  }

  function updateFilterToggle() {
    const count = extraFilterCount();
    const button = $("cat-toggle");
    if (!button) return;
    setLabeled(button, count ? `Všechny filtry · ${count}` : "Všechny filtry", "filter");
    button.classList.toggle("on", count > 0);
  }

  function chipValues(root, attr) {
    return new Set(
      [...(root?.querySelectorAll(".chip.on") || [])].map((chip) => chip.dataset[attr]).filter(Boolean),
    );
  }

  const COMMON_DISPOSITIONS = ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1", "4+kk", "4+1"];

  function dispositionValues() {
    return new Set([
      ...chipValues($("cat-dispositions"), "disposition"),
      ...chipValues($("cat-dispositions-more"), "disposition"),
    ]);
  }

  function syncChipGroup(root, attr, values) {
    root?.querySelectorAll(".chip").forEach((chip) => {
      chip.classList.toggle("on", values.has(chip.dataset[attr]));
    });
  }

  function triValue(name) {
    return document.querySelector(`[data-tri="${name}"] .chip.on`)?.dataset.value || "";
  }

  function syncTri(name, value) {
    document.querySelectorAll(`[data-tri="${name}"] .chip`).forEach((chip) => {
      chip.classList.toggle("on", chip.dataset.value === value);
    });
  }

  function syncFilterUi() {
    syncChipGroup($("cat-dispositions"), "disposition", selected.dispositions);
    syncChipGroup($("cat-dispositions-more"), "disposition", selected.dispositions);
    syncChipGroup($("cat-amenities"), "flag", selected.amenities);
    syncChipGroup(document.querySelector('[data-filter="offer"]'), "value", selected.offers);
    syncChipGroup(document.querySelector('[data-filter="estate"]'), "value", selected.estates);
    syncChipGroup(document.querySelector('[data-filter="district"]'), "value", selected.districts);
    syncChipGroup(document.querySelector('[data-filter="ownership"]'), "value", selected.ownership);
    syncChipGroup(document.querySelector('[data-filter="condition"]'), "value", selected.conditions);
    syncChipGroup(document.querySelector('[data-filter="building"]'), "value", selected.buildings);
    syncChipGroup(document.querySelector('[data-filter="equipped"]'), "value", selected.equipped);
    syncTri("roommate", selected.roommate);
    syncTri("pets", selected.pets);
    syncTri("short_term", selected.short_term);
    if ($("cat-monitor")) $("cat-monitor").value = selected.monitor;
    if ($("cat-sort")) $("cat-sort").value = selected.sort || "newest";
  }

  function readFilterUi() {
    selected.dispositions = dispositionValues();
    selected.amenities = chipValues($("cat-amenities"), "flag");
    selected.offers = chipValues(document.querySelector('[data-filter="offer"]'), "value");
    selected.estates = chipValues(document.querySelector('[data-filter="estate"]'), "value");
    selected.districts = chipValues(document.querySelector('[data-filter="district"]'), "value");
    selected.ownership = chipValues(document.querySelector('[data-filter="ownership"]'), "value");
    selected.conditions = chipValues(document.querySelector('[data-filter="condition"]'), "value");
    selected.buildings = chipValues(document.querySelector('[data-filter="building"]'), "value");
    selected.equipped = chipValues(document.querySelector('[data-filter="equipped"]'), "value");
    selected.roommate = triValue("roommate");
    selected.pets = triValue("pets");
    selected.short_term = triValue("short_term");
    selected.monitor = $("cat-monitor")?.value || "";
    selected.sort = $("cat-sort")?.value || "newest";
  }

  function clearFilterUi() {
    $("filters-modal")?.querySelectorAll(".chip.on").forEach((chip) => chip.classList.remove("on"));
    $("cat-dispositions")?.querySelectorAll(".chip.on").forEach((chip) => chip.classList.remove("on"));
    $("cat-dispositions-more")?.querySelectorAll(".chip.on").forEach((chip) => chip.classList.remove("on"));
    $("cat-offer")?.querySelectorAll(".chip.on").forEach((chip) => chip.classList.remove("on"));
    document.querySelector('[data-filter="offer"] [data-value="pronajem"]')?.classList.add("on");
    $("cat-portals")?.querySelectorAll(".chip").forEach((chip) => {
      chip.classList.toggle("on", (chip.dataset.portal || "") === selected.portal);
    });
    if ($("cat-monitor")) $("cat-monitor").value = "";
    if ($("cat-sort")) $("cat-sort").value = "newest";
    ["cat-price-from", "cat-price-to", "cat-area-from", "cat-area-to"].forEach((id) => {
      if ($(id)) $(id).value = "";
    });
    selectedPlaces = [];
    selected.districts = new Set();
    selected.dispositions = new Set();
    selected.amenities = new Set();
    selected.estates = new Set();
    selected.ownership = new Set();
    selected.conditions = new Set();
    selected.buildings = new Set();
    selected.equipped = new Set();
    selected.roommate = "";
    selected.pets = "";
    selected.short_term = "";
    selected.monitor = "";
    renderPlaceChips();
    readFilterUi();
  }

  function openFilters() {
    syncFilterUi();
    $("filters-modal").hidden = false;
    document.body.classList.add("filters-modal-open");
  }

  function closeFilters() {
    $("filters-modal").hidden = true;
    document.body.classList.remove("filters-modal-open");
  }

  function queryString(extra = {}) {
    const params = new URLSearchParams({ ...filters(), ...extra });
    for (const [key, value] of [...params.entries()]) {
      if (!value) params.delete(key);
    }
    return params.toString();
  }

  function renderFacets(facets) {
    facetMonitors = facets?.monitors || [];
    const host = $("cat-dispositions");
    const current = selected.dispositions;
    const chip = (item) => `<button type="button" class="chip${current.has(item) ? " on" : ""}" data-disposition="${escapeHtml(item)}">${escapeHtml(item)}</button>`;
    host.innerHTML = COMMON_DISPOSITIONS.map(chip).join("");
    const more = $("cat-dispositions-more");
    if (more) {
      more.innerHTML = (facets?.dispositions || []).filter((item) => !COMMON_DISPOSITIONS.includes(item)).map(chip).join("");
    }
    const monitor = $("cat-monitor");
    const previous = selected.monitor;
    if (monitor) {
      monitor.innerHTML = ['<option value="">Všechny monitory</option>']
        .concat((facets?.monitors || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`))
        .join("");
      selected.monitor = previous;
      monitor.value = previous;
    }
    renderViews();
  }

  function formatKc(value) {
    if (value == null || value === "") return "";
    return `${Number(value).toLocaleString("cs-CZ")} Kč`;
  }

  function offerKicker(item) {
    const extras = item.extras || {};
    const offer = extras.offer || (/měsíc/i.test(item.price_label || "") ? "Pronájem" : "Prodej");
    const estate = String(extras.estate || "byt").toLowerCase();
    const word = estate.startsWith("byt") ? "bytu" : estate;
    return `${offer} ${word}`;
  }

  function amenityLine(item) {
    const extras = item.extras || {};
    const parts = [];
    const furnished = (extras.specs || []).find((row) => row.label === "Vybavení");
    if (furnished && furnished.value && !/nevybaven/i.test(furnished.value)) {
      parts.push(/částečně/i.test(furnished.value) ? "Částečně vybaveno" : "Vybaveno");
    }
    const names = {
      lift: "Výtah",
      parking: "Parkování",
      cellar: "Sklep",
      balcony: "Balkon",
      loggia: "Lodžie",
      terrace: "Terasa",
      garage: "Garáž",
      pets: "Mazlíčci",
    };
    for (const flag of item.flags || extras.flags || []) {
      if (names[flag]) parts.push(names[flag]);
    }
    return [...new Set(parts)].slice(0, 2).join(" • ");
  }

  function isFresh(item) {
    if (!item.first_seen) return false;
    return Date.now() - new Date(item.first_seen).getTime() < 3 * 24 * 60 * 60 * 1000;
  }

  // Fotka se nenačetla (mrtvý odkaz, hotlink blok) -> ukaž psa místo rozbité ikony.
  const PHOTO_SEL = ".offer-photo, .detail-gallery, .compare-col, .listing";
  document.addEventListener("error", (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || img.classList.contains("offer-portal")) return;
    const box = img.closest(PHOTO_SEL);
    if (!box) return;
    // Hromadné načítání karet občas shodí první fotku (limit CDN) -> 1x zopakovat, pak zkusit další fotku z galerie.
    const tries = Number(img.dataset.tries || 0);
    if (tries < 3) {
      img.dataset.tries = String(tries + 1);
      const failed = img.getAttribute("src") || "";
      let list = [];
      try { list = JSON.parse(box.dataset.photos || "[]"); } catch (_) { list = []; }
      img.referrerPolicy = "no-referrer";
      if (tries === 0 || list.length < 2) {
        setTimeout(() => { if (img.isConnected) img.src = failed; }, 700);
      } else {
        const idx = Math.max(0, list.indexOf(failed));
        const next = list[(idx + tries) % list.length];
        setTimeout(() => { if (img.isConnected && next) img.src = next; }, 200);
      }
      return;
    }
    // Fotky ze seznamu mohou být zastaralé (portál je přečísloval) -> detail má čerstvé, zkusit ty.
    const card = box.closest("[data-open-offer]");
    if (card && box.classList.contains("offer-photo") && !box.dataset.refetched && typeof fetchCatalogItem === "function") {
      box.dataset.refetched = "1";
      const [monitorId, ...rest] = String(card.dataset.openOffer || "").split(":");
      fetchCatalogItem({ monitor_id: monitorId, id: rest.join(":"), listing_key: "", url: card.dataset.url || "" })
        .then((response) => response.json())
        .then((fresh) => {
          const photos = Array.isArray(fresh?.photos) ? fresh.photos.filter(Boolean) : [];
          if (!photos.length || !img.isConnected) throw new Error("no photos");
          box.dataset.photos = JSON.stringify(photos);
          img.dataset.tries = "0";
          img.src = photos[0];
          box.dataset.index = "0";
          const count = box.querySelector(".offer-count");
          if (count) count.textContent = `1 / ${photos.length}`;
        })
        .catch(() => box.classList.add("photo-failed"));
      return;
    }
    box.classList.add("photo-failed");
  }, true);
  document.addEventListener("load", (event) => {
    const img = event.target;
    if (!(img instanceof HTMLImageElement) || img.classList.contains("offer-portal")) return;
    img.dataset.tries = "0";
    img.closest(PHOTO_SEL)?.classList.remove("photo-failed");
  }, true);

  function valueBadge(item) {
    const pct = Number(item.value_pct);
    if (!Number.isFinite(pct) || pct < 3) return "";
    const tip = `Cena za m² je o ${Math.round(pct)} % nižší než medián ${Number(item.value_median_m2 || 0).toLocaleString("cs-CZ")} Kč/m² u ${item.value_comps} srovnatelných nabídek v okolí`;
    return `<span class="value-badge${pct >= 15 ? " is-top" : ""}" title="${escapeHtml(tip)}">${Math.round(pct)} % pod okolím</span>`;
  }

  function ageLabel(item) {
    if (!item.first_seen) return "";
    const ts = new Date(item.first_seen).getTime();
    if (!Number.isFinite(ts)) return "";
    const mins = Math.max(0, Math.floor((Date.now() - ts) / 60000));
    if (mins < 60) return `${Math.max(1, mins)} min`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours} h`;
    const days = Math.floor(hours / 24);
    if (days < 31) return `${days} ${days === 1 ? "den" : days < 5 ? "dny" : "dní"}`;
    const months = Math.floor(days / 30);
    if (months < 12) return `${months} měs`;
    return `${Math.floor(days / 365)} r`;
  }

  function ageBadge(item) {
    const label = ageLabel(item);
    if (!label) return "";
    return `<span class="offer-age${isFresh(item) ? " is-new" : ""}" title="Stáří inzerátu"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>${label}</span>`;
  }

  function priceBlock(item) {
    const extras = item.extras || {};
    const mainRaw = item.price_czk != null ? formatKc(item.price_czk) : (item.price_label || "").replace(/\/měsíc.*/, "").replace(/\s*\(.*/, "").trim();
    const main = mainRaw || "Cena na dotaz";
    let chargesN = extras.charges_czk;
    if (!chargesN) {
      const match = String(item.price_label || "").match(/\(\+\s*([\d\s]+)\s*Kč\)/);
      if (match) chargesN = Number(match[1].replace(/\s/g, ""));
    }
    const charges = chargesN ? `+ ${formatKc(chargesN)}` : "";
    const per = item.price_czk && item.area_m2 ? `(${formatKc(Math.round(item.price_czk / item.area_m2))} / m²)` : "";
    return `
      <p class="offer-price">
        <strong>${escapeHtml(main)}</strong>
        ${charges ? `<span class="offer-charges">${escapeHtml(charges)}</span>` : ""}
        ${per ? `<span class="offer-unit">${escapeHtml(per)}</span>` : ""}
      </p>
    `;
  }

  function cardHtml(item) {
    const photos = item.photos?.length ? item.photos : item.image_url ? [item.image_url] : [];
    const amenities = amenityLine(item);
    const dots = photos
      .slice(0, Math.min(photos.length, 5))
      .map((_, index) => `<i data-dot="${index}" class="${index === 0 ? "on" : ""}"></i>`)
      .join("");
    const monitors = (item.monitors || []).map((row) => row.name).filter(Boolean);
    const badges = [
      item.discount_czk ? `<span class="offer-badge offer-badge-sale">−${formatKc(item.discount_czk)}</span>` : "",
      item.gone ? `<span class="offer-badge offer-badge-gone">Prodáno</span>` : "",
      item.status === "saved" ? `<span class="offer-badge">Uložené</span>` : "",
    ]
      .filter(Boolean)
      .join("");
    return `
      <article class="offer-card${item.status === "hidden" ? " is-hidden" : ""}" data-open-offer="${item.monitor_id}:${item.id}" data-url="${escapeHtml(item.url || "")}">
        <div class="offer-photo" data-index="0" data-photos="${escapeHtml(JSON.stringify(photos))}">
          ${photos[0] ? `<img src="${escapeHtml(photos[0])}" alt="" />` : `<div class="offer-photo-empty"></div>`}
          ${
            photos.length > 1 && photos.length <= 7
              ? `<button class="offer-nav prev" type="button" aria-label="Předchozí fotka">‹</button>
                 <button class="offer-nav next" type="button" aria-label="Další fotka">›</button>
                 <div class="offer-dots">${dots}</div>`
              : photos.length > 7
                ? `<button class="offer-nav prev" type="button" aria-label="Předchozí fotka">‹</button>
                   <button class="offer-nav next" type="button" aria-label="Další fotka">›</button>
                   <p class="offer-count">1 / ${photos.length}</p>`
                : ""
          }
          ${ageBadge(item)}
          ${valueBadge(item)}
          ${portalIcons(item)}
        </div>
        <div class="offer-body">
          <div class="offer-kicker-row">
            <p class="offer-kicker">${escapeHtml(offerKicker(item))}${monitors.length ? ` · ${escapeHtml(monitors.join(" · "))}` : ""}</p>
            ${badges}
          </div>
          <h3>${escapeHtml(item.extras?.address || item.locality || item.name)}</h3>
          <div class="offer-specs">
            <span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z"/></svg>
              ${escapeHtml(item.disposition || "—")}
            </span>
            <span>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v14H5V5zm2 2v10h10V7H7z"/></svg>
              ${item.area_m2 ? `${item.area_m2} m²` : "rozloha neuvedena"}
            </span>
          </div>
          ${amenities ? `<p class="offer-amenities">${escapeHtml(amenities)}</p>` : ""}
          ${offerSourceLine(item)}
          <div class="offer-foot">
            ${priceBlock(item)}
            <div class="offer-tools">
              <button type="button" class="offer-tool${item.status === "saved" ? " on" : ""}" data-save-url="${escapeHtml(item.url || "")}" title="Uložit" aria-label="Uložit">${icon("bookmark")}<span>Uložit</span></button>
              <button type="button" class="offer-tool" data-hide-url="${escapeHtml(item.url || "")}" title="Skrýt" aria-label="Skrýt">${icon("eyeOff")}<span>Skrýt</span></button>
              <button type="button" class="offer-tool${compareUrls.includes(item.url) ? " on" : ""}" data-compare-url="${escapeHtml(item.url || "")}" title="Porovnat" aria-label="Porovnat">${icon("columns")}<span>Porovnat</span></button>
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function flagLabel(flag) {
    return ({
      balcony: "balkon",
      loggia: "lodžie",
      terrace: "terasa",
      cellar: "sklep",
      lift: "výtah",
      parking: "parking",
      garage: "garáž",
      pets: "mazlíčci",
      barrier_free: "bezbariérový",
      garden: "zahrada",
      roommate: "spolubydlení",
      short_term: "krátkodobý",
      discounted: "zlevněné",
    })[flag] || flag;
  }

  const FACT_ICONS = {
    ruler: '<path d="M4 15.5 15.5 4 20 8.5 8.5 20z"/><path d="M8 12l2 2M11 9l2 2M14 6l2 2"/>',
    layout: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.2"/>',
    floor: '<rect x="6" y="3.5" width="12" height="17" rx="1.5"/><path d="M9.5 8h1M13.5 8h1M9.5 12h1M13.5 12h1M10.5 20.5v-3h3v3"/>',
    wrench: '<path d="M14.5 6.2a4 4 0 0 0 4.9 5l-1.7-1.6-2.4.4-.4-2.4L16.600 5.900a4 4 0 0 0-2.100.300z"/><path d="M14 11 5 20l-1-1 9-9"/>',
    landmark: '<path d="M4 9.5 12 4l8 5.500M5.500 10v8M9.500 10v8M14.500 10v8M18.500 10v8M3.500 20.500h17"/>',
    key: '<circle cx="8" cy="15" r="3.6"/><path d="m10.6 12.4 8-8M16 7l2.4 2.4M13.4 9.6l1.8 1.8"/>',
    sofa: '<path d="M6 11V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v3"/><path d="M4 12.500a1.500 1.500 0 0 1 3 0V15h10v-2.500a1.500 1.500 0 0 1 3 0V18a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1zM7 19v1.500M17 19v1.500"/>',
    zap: '<path d="M13 3.500 5.500 13H12l-1 7.500L18.500 11H12z"/>',
    calendar: '<rect x="4" y="5.500" width="16" height="14.500" rx="2"/><path d="M4 10h16M8.500 3.500v4M15.500 3.500v4"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.500V12l3 2"/>',
    eye: '<path d="M3 12s3.500-6 9-6 9 6 9 6-3.500 6-9 6-9-6-9-6z"/><circle cx="12" cy="12" r="2.400"/>',
    wallet: '<rect x="3.500" y="6" width="17" height="13" rx="2.500"/><path d="M3.500 10h17M15.500 14.500h2"/>',
    tag: '<path d="M4 12.500V5a1 1 0 0 1 1-1h7.500l7.500 7.500-8.500 8.500z"/><circle cx="8.500" cy="8.500" r="1.200"/>',
    lift: '<rect x="5" y="3.500" width="14" height="17" rx="2"/><path d="m9 10 3-3 3 3M9 14l3 3 3-3"/>',
    car: '<path d="M5 16V12l1.800-4.500A1.500 1.500 0 0 1 8.200 6.500h7.600a1.500 1.500 0 0 1 1.400 1L19 12v4M5 12h14M5 16v2.500M19 16v2.500M5 16h14"/><circle cx="8.500" cy="14" r=".6"/><circle cx="15.500" cy="14" r=".6"/>',
    paw: '<circle cx="7" cy="10" r="1.800"/><circle cx="11" cy="6.600" r="1.800"/><circle cx="16" cy="7.400" r="1.800"/><circle cx="18.200" cy="12" r="1.700"/><path d="M12 12.500c-3 0-5 2.200-5 4.200 0 1.600 1.600 2.300 3 2 1.200-.3 1.600-.5 2-.5s.8.200 2 .5c1.400.3 3-.4 3-2 0-2-2-4.200-5-4.200z"/>',
    tree: '<path d="M12 21v-6M12 15c-3.500 0-6-2.200-6-5 0-2 1.400-3.500 3.200-3.800C9.800 4.600 10.800 3.500 12 3.500s2.200 1.100 2.800 2.700C16.600 6.500 18 8 18 10c0 2.800-2.500 5-6 5z"/>',
    box: '<path d="M4 8 12 4l8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8"/>',
    sun: '<circle cx="12" cy="12" r="3.600"/><path d="M12 3.500v2M12 18.500v2M3.500 12h2M18.500 12h2M6 6l1.400 1.400M16.600 16.600 18 18M6 18l1.400-1.400M16.600 7.400 18 6"/>',
    access: '<circle cx="12" cy="5" r="1.600"/><path d="M12 8.500v5h4l2 4.500M12 11h4M10 14a4 4 0 1 0 5 5"/>',
    users: '<circle cx="9" cy="8.500" r="3"/><path d="M3.500 19a5.500 5.500 0 0 1 11 0M16 5.800a3 3 0 0 1 0 5.400M17.500 14a5 5 0 0 1 3 5"/>',
    hash: '<path d="M9 4 7.500 20M16.500 4 15 20M4.500 9h15.500M4 15h15.500"/>',
    info: '<circle cx="12" cy="12" r="8.500"/><path d="M12 11v5M12 8h.01"/>',
  };

  function factIcon(name) {
    return `<svg class="fact-ico" viewBox="0 0 24 24" aria-hidden="true">${FACT_ICONS[name] || FACT_ICONS.info}</svg>`;
  }

  const FACT_RULES = [
    [/^(užitná\s+)?plocha/i, "ruler"],
    [/dispozic/i, "layout"],
    [/podlaží budovy|počet podlaží/i, "floor"],
    [/patro|podlaží/i, "floor"],
    [/stav/i, "wrench"],
    [/konstrukc/i, "landmark"],
    [/vlastnictv/i, "key"],
    [/vybaven/i, "sofa"],
    [/energet|penb/i, "zap"],
    [/přidáno|volné od|dostupn/i, "calendar"],
    [/poplatk|kauce|náklady|energie|cena/i, "wallet"],
    [/zobrazen/i, "eye"],
    [/výtah/i, "lift"],
    [/parkov|garáž/i, "car"],
    [/mazlí/i, "paw"],
    [/zahrad/i, "tree"],
    [/sklep/i, "box"],
    [/balkon|lodžie|terasa/i, "sun"],
    [/bezbariér/i, "access"],
    [/spolubydlen/i, "users"],
    [/krátkodob/i, "clock"],
    [/zakázk|číslo/i, "hash"],
    [/typ|nabídka|druh/i, "tag"],
  ];

  function factIconFor(label) {
    for (const [re, name] of FACT_RULES) if (re.test(label)) return name;
    return "info";
  }

  function fmtDate(value) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric" });
  }

  function fxNote(item) {
    const fx = item.extras?.fx;
    if (!fx || !fx.amount) return "";
    const sym = { EUR: "€", USD: "$", GBP: "£", PLN: "zł" }[fx.currency] || fx.currency;
    const orig = `${Number(fx.amount).toLocaleString("cs-CZ")} ${sym}`;
    return `<p class="detail-fx">Přepočteno z ${escapeHtml(fx.currency)} · původně <b>${escapeHtml(orig)}</b></p>`;
  }

  function detailFacts(item) {
    const extras = item.extras || {};
    const specs = (extras.specs || []).filter((row) => row && row.label && row.value);
    const facts = [];
    const seen = new Set();
    const add = (label, value, iconName) => {
      const key = String(label).toLowerCase().replace(/^vybaven(í|ost)$/, "vybavenost");
      if (/^undefined$|^null$/i.test(String(value).trim())) return;
      if (value == null || value === "" || seen.has(key)) return;
      seen.add(key);
      facts.push({ label, value: String(value), icon: iconName || factIconFor(label) });
    };
    const spec = (re) => specs.find((row) => re.test(row.label));
    const typ = [extras.offer, extras.estate].filter(Boolean).join(" · ");
    add("Plocha", item.area_m2 ? `${item.area_m2} m²` : "", "ruler");
    add("Dispozice", item.disposition, "layout");
    add("Typ", typ, "tag");
    const floor = spec(/^podlaží$|^patro$/i);
    if (floor) add("Patro", floor.value, "floor");
    for (const [label, re] of [["Stav", /^stav( bytu)?$/i], ["Konstrukce", /^konstrukce/i], ["Vlastnictví", /^vlastnictv/i], ["Vybavenost", /^vybaven/i], ["Energetická třída", /energet|penb/i]]) {
      const row = spec(re);
      if (row) add(label, row.value);
    }
    if (extras.charges_czk) add("Poplatky", formatKc(extras.charges_czk), "wallet");
    const free = spec(/volné od/i);
    if (free) add("Volné od", free.value, "calendar");
    if (item.first_seen) add("Přidáno", fmtDate(item.first_seen), "calendar");
    if (item.views != null) add("Zobrazení", item.views, "eye");
    for (const row of specs) {
      if (/^(cena|číslo zakázky|užitná plocha)$/i.test(row.label)) continue;
      add(row.label, row.value);
    }
    for (const flag of item.flags || extras.flags || []) {
      if (flag === "discounted") continue;
      const label = flagLabel(flag);
      add(label.charAt(0).toUpperCase() + label.slice(1), "ano", factIconFor(label));
    }
    return facts;
  }

  function factsHtml(item) {
    const facts = detailFacts(item);
    if (!facts.length) return "";
    return `<div class="detail-facts">${facts
      .map((f) => `<div class="fact">${factIcon(f.icon)}<span class="fact-label">${escapeHtml(f.label)}</span><strong class="fact-value">${escapeHtml(f.value)}</strong></div>`)
      .join("")}</div>`;
  }

  const PRO_FEATURES = [
    "Neomezeně hlídacích psů",
    "Všechny agregované portály (9), notifikace do 30 s",
    "Email + Push + Discord (10 SMS/měs)",
    "AI filtry a MCP (ChatGPT, Claude, Grok)",
    "Telefon + chat, Excel/CSV neomezeně",
  ];

  function proPlan() {
    let billing = null;
    try {
      billing = typeof statusCache !== "undefined" ? statusCache?.billing : null;
    } catch {
      billing = null;
    }
    const plan = billing?.plan || "free";
    const pro = (billing?.plans || []).find((item) => item.id === "pro");
    return {
      owned: plan === "pro" || plan === "individual",
      guest: document.body.classList.contains("guest") || !billing,
      price: pro?.price_czk || 349,
      features: pro?.features?.length ? pro.features : PRO_FEATURES,
    };
  }

  function upsellHtml() {
    const pro = proPlan();
    if (pro.owned) return "";
    const href = pro.guest ? "/registrace" : "/nastaveni/predplatne";
    const cta = pro.guest ? "Vyzkoušet PRO zdarma" : "Upgradovat na PRO";
    return `
      <article class="upsell-card" data-upsell="pro">
        <div class="upsell-main">
          <p class="upsell-kicker">Realitify PRO</p>
          <h3>Upgrade na PRO a nepřijdete o žádný byt</h3>
          <ul class="upsell-list">${pro.features.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
        </div>
        <div class="upsell-side">
          <p class="upsell-price"><strong>${formatKc(pro.price)}</strong> <span>/ měsíc</span></p>
          <p class="upsell-note">3 dny zdarma, zrušíte kdykoli</p>
          <a class="upsell-cta" href="${href}" target="_top">${cta}</a>
        </div>
      </article>
    `;
  }

  // Na mapě se jednou za den (při prvním doscrollování na 20. nabídku) ukáže nabídka Start / PRO.
  const MAP_PROMO_AT = 20;
  const MAP_PROMO_KEY = "rf-map-promo-day";
  let mapPromoObserver = null;

  function mapPromoSeenToday() {
    try {
      return localStorage.getItem(MAP_PROMO_KEY) === new Date().toLocaleDateString("sv-SE");
    } catch {
      return false;
    }
  }

  function showMapPromo() {
    const wrap = document.querySelector(".catalog-map-wrap");
    if (!wrap || wrap.querySelector(".map-promo")) return;
    const pro = proPlan();
    const href = pro.guest ? "/registrace" : "/nastaveni/predplatne";
    const box = document.createElement("div");
    box.className = "map-promo";
    box.innerHTML = `
      <div class="map-promo-card" role="dialog" aria-label="Start a PRO">
        <button type="button" class="map-promo-close" aria-label="Zavřít">×</button>
        <p class="upsell-kicker">Start / PRO</p>
        <h3>Nechcete ručně procházet nabídky?</h3>
        <p>Se Startem nebo PRO vám žádná nabídka neuteče. Hlídací pes vám nové byty pošle sám, dřív než ostatním.</p>
        <a class="upsell-cta" href="${href}" target="_top">${pro.guest ? "Vyzkoušet zdarma" : "Zobrazit tarify"}</a>
      </div>
    `;
    box.addEventListener("click", (event) => {
      if (event.target === box || event.target.closest(".map-promo-close")) box.remove();
    });
    wrap.appendChild(box);
  }

  function armMapPromo() {
    if (mapPromoObserver) mapPromoObserver.disconnect();
    mapPromoObserver = null;
    if (mapPromoSeenToday() || proPlan().owned || !("IntersectionObserver" in window)) return;
    const target = listEl.querySelectorAll(".offer-card")[MAP_PROMO_AT - 1];
    if (!target) return;
    mapPromoObserver = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting) || mapPromoSeenToday()) return;
      try {
        localStorage.setItem(MAP_PROMO_KEY, new Date().toLocaleDateString("sv-SE"));
      } catch {}
      mapPromoObserver.disconnect();
      mapPromoObserver = null;
      showMapPromo();
    });
    mapPromoObserver.observe(target);
  }

  function renderList(items, append) {
    const cards = items.map(cardHtml);
    // Třetí slot v nabídce patří upgradu na PRO (jen na první stránce výsledků).
    if (!append && cards.length) {
      const promo = upsellHtml();
      if (promo) cards.splice(Math.min(2, cards.length), 0, promo);
    }
    const html = cards.join("");
    if (append) listEl.insertAdjacentHTML("beforeend", html);
    else listEl.innerHTML = html;
    armMapPromo();
  }

  function galleryPhotos(gallery) {
    try {
      return JSON.parse(gallery.dataset.photos || "[]");
    } catch {
      return [];
    }
  }

  function showGallery(gallery, next) {
    const photos = galleryPhotos(gallery);
    if (photos.length < 2) return;
    const index = (next + photos.length) % photos.length;
    gallery.dataset.index = String(index);
    const img = gallery.querySelector("img:not(.offer-portal)");
    if (img) img.src = photos[index];
    gallery.querySelectorAll("[data-dot]").forEach((dot) => {
      dot.classList.toggle("on", Number(dot.dataset.dot) === index);
    });
    const count = gallery.querySelector(".offer-count");
    if (count) count.textContent = `${index + 1} / ${photos.length}`;
    const ahead = photos[(index + 1) % photos.length];
    if (ahead) {
      const preload = new Image();
      preload.src = ahead;
    }
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

  function radiusLabel(meters) {
    return meters >= 1000 ? `${(meters / 1000).toFixed(meters % 1000 ? 1 : 0)} km` : `${meters} m`;
  }

  function saveCircle() {
    if (circleFilter) localStorage.setItem("nabidka-circle", JSON.stringify(circleFilter));
    else localStorage.removeItem("nabidka-circle");
  }

  function syncCircleUi() {
    const has = Boolean(circleFilter);
    $("map-radius-wrap").hidden = !has;
    $("map-circle-clear").hidden = !has;
    $("map-draw-hint").hidden = !drawingCircle;
    $("map-circle-btn").classList.toggle("on", drawingCircle || has);
    if (has && $("map-radius")) {
      $("map-radius").value = String(circleFilter.radius_m);
      $("map-radius-label").textContent = radiusLabel(circleFilter.radius_m);
    }
  }

  function applyPlaceGeoms(items) {
    for (const item of items || []) {
      const row = selectedPlaces.find((place) => place.id === item.id);
      if (!row) continue;
      const next = item.geojson;
      if (next?.type === "Point") {
        row.lat = item.lat ?? row.lat;
        row.lon = item.lon ?? row.lon;
        continue;
      }
      if (row.geojson && /LineString|Polygon/i.test(row.geojson.type || "") && next?.type === "Point") continue;
      if (next) row.geojson = next;
      row.kind = item.kind || row.kind;
      if (item.label && !/^[RWN]\d+$/i.test(item.label)) row.label = item.label;
      row.lat = item.lat ?? row.lat;
      row.lon = item.lon ?? row.lon;
      row.buffer_m = item.buffer_m || row.buffer_m;
    }
    renderPlaceChips();
  }

  function lineLatLngs(geojson) {
    if (!geojson) return [];
    if (geojson.type === "LineString") {
      return [geojson.coordinates.map(([lon, lat]) => [lat, lon])];
    }
    if (geojson.type === "MultiLineString") {
      return geojson.coordinates.map((line) => line.map(([lon, lat]) => [lat, lon]));
    }
    return [];
  }

  function metersToStrokePx(meters, lat) {
    const zoom = catalogMap?.getZoom?.() || 16;
    const mpp = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
    return Math.max(10, (Number(meters) * 2) / mpp);
  }

  function padBoundsMeters(bounds, meters) {
    const lat = bounds.getCenter().lat;
    const dLat = meters / 111320;
    const dLon = meters / (111320 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
    return L.latLngBounds(
      [bounds.getSouth() - dLat, bounds.getWest() - dLon],
      [bounds.getNorth() + dLat, bounds.getEast() + dLon],
    );
  }

  function finiteCoord(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function isUsableLatLon(lat, lon) {
    return lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon) && !(Math.abs(lat) < 1e-6 && Math.abs(lon) < 1e-6);
  }

  function boundsAreUsable(bounds) {
    if (!bounds?.isValid?.()) return false;
    const center = bounds.getCenter();
    if (!isUsableLatLon(center.lat, center.lng)) return false;
    const span = Math.abs(bounds.getNorth() - bounds.getSouth()) + Math.abs(bounds.getEast() - bounds.getWest());
    return span > 0 && span < 80;
  }

  function pinBounds() {
    let bounds = null;
    for (const item of pinItems) {
      const lat = finiteCoord(item.lat);
      const lon = finiteCoord(item.lon);
      if (!isUsableLatLon(lat, lon)) continue;
      const point = L.latLng(lat, lon);
      bounds = bounds ? bounds.extend(point) : L.latLngBounds(point, point);
    }
    return bounds;
  }

  function extentBounds(place) {
    const extent = place?.extent;
    if (!Array.isArray(extent) || extent.length < 4) return null;
    const west = finiteCoord(extent[0]);
    const north = finiteCoord(extent[1]);
    const east = finiteCoord(extent[2]);
    const south = finiteCoord(extent[3]);
    if (west == null || north == null || east == null || south == null) return null;
    const bounds = L.latLngBounds([south, west], [north, east]);
    return boundsAreUsable(bounds) ? bounds : null;
  }

  function selectionBounds() {
    if (placeLayer?.getLayers?.().length) {
      // L.layerGroup has no getBounds — only FeatureGroup does.
      let drawn = null;
      if (typeof placeLayer.getBounds === "function") {
        try {
          drawn = placeLayer.getBounds();
        } catch {
          drawn = null;
        }
      } else {
        for (const layer of placeLayer.getLayers()) {
          try {
            if (typeof layer.getBounds === "function") {
              const part = layer.getBounds();
              drawn = drawn ? drawn.extend(part) : part;
            } else if (typeof layer.getLatLng === "function") {
              const point = layer.getLatLng();
              drawn = drawn ? drawn.extend(point) : L.latLngBounds(point, point);
            }
          } catch {
            /* ignore bad layer */
          }
        }
      }
      if (boundsAreUsable(drawn)) return drawn;
    }
    let bounds = null;
    for (const place of selectedPlaces) {
      const fromExtent = extentBounds(place);
      if (fromExtent) {
        bounds = bounds ? bounds.extend(fromExtent) : fromExtent;
        continue;
      }
      const lat = finiteCoord(place.lat);
      const lon = finiteCoord(place.lon);
      if (!isUsableLatLon(lat, lon)) continue;
      const pad = place.kind === "street" ? 0.004 : 0.03;
      const part = L.latLngBounds([lat - pad, lon - pad * 1.4], [lat + pad, lon + pad * 1.4]);
      bounds = bounds ? bounds.extend(part) : part;
    }
    return boundsAreUsable(bounds) ? bounds : null;
  }

  function goToSelection() {
    ensureMap();
    if (!catalogMap) return;
    let bounds = selectionBounds() || pinBounds();
    if (!boundsAreUsable(bounds)) return;
    ignoreMapMove += 1;
    const streetOnly =
      selectedPlaces.length > 0 &&
      selectedPlaces.every((row) => row.kind === "street" || /LineString/i.test(row.geojson?.type || ""));
    if (streetOnly) {
      const meters = Number(selectedPlaces[0]?.buffer_m) || 45;
      bounds = padBoundsMeters(bounds, meters);
    }
    try {
      catalogMap.invalidateSize();
      catalogMap.fitBounds(bounds, {
        padding: [40, 40],
        maxZoom: streetOnly ? 16 : 14,
        animate: false,
      });
    } catch {
      const center = bounds.getCenter();
      catalogMap.setView(center, streetOnly ? 15 : 12, { animate: false });
    }
    window.setTimeout(() => {
      ignoreMapMove = Math.max(0, ignoreMapMove - 1);
    }, 1600);
  }

  function drawPlaceLayer(fit) {
    ensureMap();
    if (!catalogMap) return;
    if (!placeLayer) {
      placeLayer = L.featureGroup({ pane: "placePane" }).addTo(catalogMap);
    }
    placeLayer.clearLayers();
    for (const place of selectedPlaces) {
      const street = place.kind === "street" || /LineString/i.test(place.geojson?.type || "");
      if (street) {
        const lines = lineLatLngs(place.geojson);
        if (!lines.length) continue;
        const meters = Number(place.buffer_m) || 45;
        for (const latlngs of lines) {
          const lat = latlngs[0][0];
          L.polyline(latlngs, {
            color: "#5ba3d0",
            weight: metersToStrokePx(meters, lat),
            opacity: 0.35,
            lineCap: "round",
            lineJoin: "round",
          }).addTo(placeLayer);
          L.polyline(latlngs, {
            color: "#2b6a96",
            weight: 3,
            opacity: 0.95,
            lineCap: "round",
            lineJoin: "round",
          }).addTo(placeLayer);
        }
        continue;
      }
      if (!place.geojson || place.geojson.type === "Point") continue;
            L.geoJSON(place.geojson, {
        pane: "placePane",
        style: {
          color: "#163300",
          weight: 2,
          fillColor: "#9fe870",
          fillOpacity: 0.12,
        },
      }).addTo(placeLayer);
    }
    if (fit) goToSelection();
  }

  function drawCircleLayer() {
    if (!catalogMap || !circleFilter) return;
    const latlng = [circleFilter.lat, circleFilter.lon];
    if (circleLayer) {
      circleLayer.setLatLng(latlng);
      circleLayer.setRadius(circleFilter.radius_m);
    } else {
      circleLayer = L.circle(latlng, {
        radius: circleFilter.radius_m,
        color: "#163300",
        weight: 2,
        fillColor: "#9fe870",
        fillOpacity: 0.16,
      }).addTo(catalogMap);
    }
    ignoreMapMove += 1;
    catalogMap.fitBounds(circleLayer.getBounds(), { padding: [28, 28], maxZoom: 15 });
    window.setTimeout(() => {
      ignoreMapMove = Math.max(0, ignoreMapMove - 1);
    }, 400);
  }

  function placeCircle(lat, lon, radiusM) {
    circleFilter = { lat, lon, radius_m: radiusM || circleFilter?.radius_m || 2000 };
    drawingCircle = false;
    if (catalogMap) catalogMap.getContainer().style.cursor = "";
    saveCircle();
    syncCircleUi();
    drawCircleLayer();
    loadCatalog();
  }

  function clearCircle() {
    circleFilter = null;
    drawingCircle = false;
    if (circleLayer) {
      catalogMap?.removeLayer(circleLayer);
      circleLayer = null;
    }
    if (catalogMap) catalogMap.getContainer().style.cursor = "";
    saveCircle();
    syncCircleUi();
    catalogAbort?.abort();
    catalogBusy = false;
    catalogQueued = false;
    loadCatalog();
  }

  function startCircleDraw() {
    ensureMap();
    drawingCircle = true;
    if (catalogMap) catalogMap.getContainer().style.cursor = "crosshair";
    syncCircleUi();
  }

  function pinWeight(item) {
    const n = Number(item?.count);
    return Number.isFinite(n) && n > 0 ? n : 1;
  }

  function clusterCell(zoom) {
    if (zoom >= STREET_ZOOM) return 0;
    if (zoom >= 14) return 0.004;
    if (zoom >= 13) return 0.008;
    if (zoom >= 12) return 0.016;
    if (zoom >= 11) return 0.03;
    if (zoom >= 10) return 0.08;
    if (zoom >= 9) return 0.2;
    if (zoom >= 8) return 0.45;
    if (zoom >= 7) return 0.9;
    return 1.5;
  }

  function groupedPins(items, zoom) {
    const cell = clusterCell(zoom);
    const raw = [];
    if (!cell) {
      // I na street zoomu nejdřív slouč stejné/centroid souřadnice — ať nevznikne vějíř cenovek.
      for (const item of items) {
        raw.push({ items: [item], lat: item.lat, lon: item.lon, count: pinWeight(item) });
      }
      return collapseCoincidentPins(raw);
    }
    const groups = new Map();
    for (const item of items) {
      // Approx dump nesmí pohltit přesné piny ve stejné mapové buňce.
      const key = `${Math.round(item.lat / cell)}:${Math.round(item.lon / cell)}:${item.approx ? "a" : "e"}`;
      const group = groups.get(key) || { items: [], lat: 0, lon: 0, count: 0 };
      const weight = pinWeight(item);
      group.items.push(item);
      group.lat += item.lat * weight;
      group.lon += item.lon * weight;
      group.count += weight;
      groups.set(key, group);
    }
    for (const group of groups.values()) {
      raw.push({
        items: group.items,
        lat: group.lat / group.count,
        lon: group.lon / group.count,
        count: group.count,
      });
    }
    if (raw.some((group) => group.count > 40)) return raw;
    return mergeNearbyPinGroups(raw);
  }

  function pinApproxFlag(item) {
    return Boolean(item?.approx);
  }

  function groupApproxOnly(items) {
    return items.length > 0 && items.every((item) => pinApproxFlag(item));
  }

  function collapseCoincidentPins(groups) {
    if (!catalogMap || groups.length < 2) return groups;
    const buckets = [];
    for (const group of groups) {
      const point = catalogMap.latLngToLayerPoint([group.lat, group.lon]);
      const approx = groupApproxOnly(group.items);
      const hit = buckets.find(
        (bucket) =>
          bucket.approx === approx && point.distanceTo(bucket.point) < STACK_PX,
      );
      if (hit) {
        hit.items.push(...group.items);
        hit.count += group.count || group.items.length;
        continue;
      }
      buckets.push({
        point,
        approx,
        items: group.items.slice(),
        lat: group.lat,
        lon: group.lon,
        count: group.count || group.items.length,
      });
    }
    return buckets.map((bucket) => ({
      items: bucket.items,
      lat: bucket.lat,
      lon: bucket.lon,
      count: bucket.count,
      coincident: bucket.items.length > 1,
    }));
  }

  function groupNeedsApproxTip(group) {
    const items = group?.items || [];
    if (!items.length) return false;
    // Jen když jsou všechny piny approx — smíchaný cluster nesmí tvrdit, že neznáme ulici.
    return groupApproxOnly(items);
  }

  function offsetAround(lat, lon, index, count, px = 36) {
    if (!catalogMap || count <= 1) return [lat, lon];
    const origin = catalogMap.latLngToLayerPoint([lat, lon]);
    const angle = (2 * Math.PI * index) / count - Math.PI / 2;
    const shifted = catalogMap.layerPointToLatLng(
      L.point(origin.x + px * Math.cos(angle), origin.y + px * Math.sin(angle)),
    );
    return [shifted.lat, shifted.lng];
  }

  function mergeNearbyPinGroups(groups) {
    if (!catalogMap || groups.length < 2) return groups;
    const remaining = groups.map((group) => ({
      ...group,
      items: group.items.slice(),
      approx: groupApproxOnly(group.items),
    }));
    let merged = true;
    while (merged) {
      merged = false;
      remaining.sort((a, b) => b.count - a.count);
      outer: for (let i = 0; i < remaining.length; i += 1) {
        const a = remaining[i];
        const pa = catalogMap.latLngToLayerPoint([a.lat, a.lon]);
        for (let j = i + 1; j < remaining.length; j += 1) {
          const b = remaining[j];
          if (a.approx !== b.approx) continue;
          const pb = catalogMap.latLngToLayerPoint([b.lat, b.lon]);
          if (pa.distanceTo(pb) >= 56) continue;
          const count = a.count + b.count;
          a.lat = (a.lat * a.count + b.lat * b.count) / count;
          a.lon = (a.lon * a.count + b.lon * b.count) / count;
          a.count = count;
          a.items.push(...b.items);
          remaining.splice(j, 1);
          merged = true;
          break outer;
        }
      }
    }
    return remaining;
  }

  function approxLocalityTip(count = 1) {
    const n = Number(count) || 1;
    if (n > 1) {
      return "Přesná lokalita u těchto inzerátů není známá — původní stránka ji neuvedla, proto jsou na přibližném místě.";
    }
    return "Přesná lokalita u tohoto inzerátu není známá — původní stránka ji neuvedla, proto je na přibližném místě.";
  }

  function bindApproxTip(marker, count = 1) {
    marker.bindTooltip(approxLocalityTip(count), {
      direction: "top",
      opacity: 0.96,
      className: "map-approx-tip",
      offset: [0, -8],
    });
  }

  function priceMarker(item, lat = item.lat, lon = item.lon) {
    const pin = L.divIcon({
      className: "price-pin-wrap",
      html: `<div class="price-pin${item.approx ? " is-approx" : ""}">${escapeHtml(pinPrice(item))}</div>`,
      iconSize: [88, 32],
      iconAnchor: [44, 16],
      popupAnchor: [0, -18],
    });
    const marker = L.marker([lat, lon], { icon: pin, riseOnHover: true, pane: "pinPane" });
    marker.bindPopup(`<strong>${escapeHtml(pinPrice(item))}</strong><br />${escapeHtml(item.locality || item.name)}`);
    if (item.approx) bindApproxTip(marker, 1);
    marker.on("click", () => {
      if (drawingCircle) {
        placeCircle(item.lat, item.lon);
        return;
      }
      openDetail(item.monitor_id, item.id, item);
    });
    // Mobile: no hover-driven scrolling; a direct tap on a price scrolls to its listing.
    marker.on("mouseover", () => { if (!touchMap.matches) highlightCard(listingKey(item)); });
    marker.on("mouseout", () => { if (!touchMap.matches) clearCardHighlight(); });
    return marker;
  }

  let pinRefreshTimer = 0;
  let pinRefreshAbort = null;
  let lastPinViewKey = "";

  function schedulePinRefresh() {
    window.clearTimeout(pinRefreshTimer);
    pinRefreshTimer = window.setTimeout(async () => {
      if (!catalogMap || catalogBusy) return;
      const b = catalogMap.getBounds();
      const key = [catalogMap.getZoom(), b.getSouth().toFixed(4), b.getNorth().toFixed(4), b.getWest().toFixed(4), b.getEast().toFixed(4)].join("|");
      if (key === lastPinViewKey) return;
      lastPinViewKey = key;
      pinRefreshAbort?.abort();
      const ac = new AbortController();
      pinRefreshAbort = ac;
      try {
        const query = queryString({
          limit: 1,
          south: String(b.getSouth()),
          north: String(b.getNorth()),
          west: String(b.getWest()),
          east: String(b.getEast()),
        });
        const response = await fetch(`/api/catalog/pins?${query}`, { signal: ac.signal });
        if (!response.ok) return;
        const data = await response.json();
        if (ac.signal.aborted || !catalogMap) return;
        pinItems = uniqueOffers(data.items || []);
        drawPinLayer();
      } catch (_) {
        /* superseded or offline — keep the pins we have */
      }
    }, 250);
  }

  function ensureMap() {
    if (catalogMap || typeof L === "undefined") return;
    catalogMap = L.map("catalog-map", { scrollWheelZoom: true, attributionControl: false }).setView([50.08, 14.44], 12);
    catalogMap.createPane("placePane");
    catalogMap.getPane("placePane").style.zIndex = 450;
    catalogMap.getPane("placePane").style.pointerEvents = "none";
    catalogMap.createPane("pinPane");
    catalogMap.getPane("pinPane").style.zIndex = 650;
    const popupPane = catalogMap.getPane("popupPane");
    if (popupPane) popupPane.style.zIndex = 1400;
    addBaseTiles(catalogMap);
    placeLayer = L.featureGroup({ pane: "placePane" }).addTo(catalogMap);
    catalogLayer = L.layerGroup({ pane: "pinPane" }).addTo(catalogMap);
    hoverLayer = L.layerGroup({ pane: "pinPane" }).addTo(catalogMap);
    catalogMap.on("click", (event) => {
      if (!drawingCircle) return;
      placeCircle(event.latlng.lat, event.latlng.lng);
    });
    catalogMap.on("zoom zoomend moveend", () => {
      drawPinLayer();
    });
    catalogMap.on("moveend", () => {
      if (ignoreMapMove || drawingCircle) return;
      // A place / circle limits the list; only the pins follow the viewport (so zooming splits clusters).
      if (selectedPlaces.length || circleFilter) {
        if (!circleFilter && !selected.monitor) schedulePinRefresh();
        return;
      }
      window.clearTimeout(mapReloadTimer);
      mapReloadTimer = window.setTimeout(() => {
        const b = catalogMap.getBounds();
        const key = [
          catalogMap.getZoom(),
          b.getSouth().toFixed(5),
          b.getNorth().toFixed(5),
          b.getWest().toFixed(5),
          b.getEast().toFixed(5),
        ].join("|");
        if (key === lastMapQueryKey) return;
        lastMapQueryKey = key;
        loadCatalog();
      }, 250);
    });
  }

  function listingKey(item) {
    return `${item.monitor_id}:${item.id}`;
  }

  const touchMap = window.matchMedia ? window.matchMedia("(hover: none), (max-width: 900px)") : { matches: false };

  function highlightCard(key, { scroll = true } = {}) {
    listEl.querySelectorAll(".offer-card.is-hot").forEach((el) => el.classList.remove("is-hot"));
    const card = listEl.querySelector(`[data-open-offer="${CSS.escape(String(key))}"]`);
    if (!card) return;
    card.classList.add("is-hot");
    if (scroll) card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function clearCardHighlight() {
    listEl.querySelectorAll(".offer-card.is-hot").forEach((el) => el.classList.remove("is-hot"));
  }

  function stackPlaceLabel(items) {
    const counts = new Map();
    for (const item of items || []) {
      const loc = String(item.locality || "").trim();
      if (!loc) continue;
      const praha = loc.match(/Praha\s*\d+/i);
      let label = "";
      if (praha) {
        label = praha[0].replace(/\s+/g, " ");
        label = label.replace(/^(praha)(\s*)(\d+)/i, (_, p, _s, n) => `Praha ${n}`);
      } else {
        // "Brno 602 00", "Brandýs nad Labem …" — first segment before comma / en-dash
        const head = loc.split(/[,–-]/)[0].trim().replace(/\s+\d{3}\s*\d{2}\s*$/, "").trim();
        if (head && head.length >= 3 && !/^\d+$/.test(head)) label = head;
      }
      if (!label) continue;
      const key = label.toLowerCase();
      const prev = counts.get(key);
      if (prev) prev.n += 1;
      else counts.set(key, { label, n: 1 });
    }
    if (!counts.size) return "";
    let best = null;
    for (const row of counts.values()) {
      if (!best || row.n > best.n) best = row;
    }
    // Require a clear majority so mixed cities don't get a wrong title.
    if (!best || best.n < Math.ceil((items?.length || 0) * 0.5)) return "";
    return best.label;
  }

  function stackPopupHtml(group) {
    const all = group.items || [];
    const approx = groupNeedsApproxTip(group);
    const tip = approx ? approxLocalityTip(all.length) : "";
    const place = stackPlaceLabel(all);
    const title = approx
      ? place
        ? `${all.length} nabídek v ${place}`
        : `${all.length} nabídek na přibližném místě`
      : place
        ? `${all.length} nabídek v ${place}`
        : `${all.length} nabídek na stejném místě`;
    const rows = all.slice(0, 8).map((item) => {
      const key = listingKey(item);
      const label = escapeHtml(pinPrice(item));
      const placeText = escapeHtml(item.locality || item.name || "");
      return `<button type="button" class="map-stack-item" data-stack-key="${escapeHtml(key)}" data-mid="${escapeHtml(item.monitor_id)}" data-id="${escapeHtml(String(item.id))}"><strong>${label}</strong><span>${placeText}</span></button>`;
    });
    const more = all.length > 8 ? `<p class="map-stack-more">+${all.length - 8} dalších v seznamu vpravo</p>` : "";
    const tipHtml = tip ? `<p>${escapeHtml(tip)}</p>` : "";
    return `<div class="map-stack-pop"><strong>${escapeHtml(title)}</strong>${tipHtml}<div class="map-stack-list">${rows.join("")}</div>${more}</div>`;
  }

  function openStackPopup(marker, group) {
    hoverLayer?.clearLayers();
    marker.unbindPopup();
    marker.bindPopup(stackPopupHtml(group), { className: "map-popup map-stack-popup", maxWidth: 300, closeButton: true });
    marker.openPopup();
    const first = group.items.find((item) => listEl.querySelector(`[data-open-offer="${CSS.escape(listingKey(item))}"]`));
    if (first) highlightCard(listingKey(first));
    const box = marker.getPopup()?.getElement();
    box?.querySelectorAll(".map-stack-item").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const mid = btn.getAttribute("data-mid");
        const id = btn.getAttribute("data-id");
        const key = btn.getAttribute("data-stack-key");
        const item = group.items.find((row) => listingKey(row) === key) || { monitor_id: mid, id: Number(id) };
        if (key) highlightCard(key);
        openDetail(item.monitor_id, item.id, item);
      });
    });
  }

  function clearListingHover() {
    document.querySelectorAll("#catalog-map .leaflet-marker-icon.is-open").forEach((el) => {
      el.classList.remove("is-open");
    });
    hoverLayer?.clearLayers();
    hoverMarker = null;
    hoverKey = null;
  }

  function highlightListing(key) {
    if (!key || !catalogMap) return;
    const keep = key;
    clearListingHover();
    hoverKey = keep;
    const solo = markerByKey.get(keep);
    if (solo) {
      solo.getElement()?.classList.add("is-open");
      solo.setZIndexOffset(1200);
      return;
    }
    const clustered = clusterByKey.get(keep);
    if (!clustered) return;
    clustered.marker.getElement()?.classList.add("is-open");
    const item = clustered.item;
    if (item.lat == null || item.lon == null || !hoverLayer) return;
    hoverMarker = priceMarker(item);
    hoverMarker.setZIndexOffset(2000);
    hoverLayer.addLayer(hoverMarker);
    requestAnimationFrame(() => {
      hoverMarker?.getElement()?.classList.add("is-open");
    });
  }

  function drawPinLayer() {
    if (!catalogMap || !catalogLayer) return;
    const keepHover = hoverKey;
    const items = pinItems.filter((row) => row.lat != null && row.lon != null);
    catalogLayer.clearLayers();
    markerByKey.clear();
    clusterByKey.clear();
    hoverLayer?.clearLayers();
    hoverMarker = null;
    const zoom = catalogMap.getZoom();
    const groups = groupedPins(items, zoom);
    for (const group of groups) {
      const weight = group.count || group.items.reduce((sum, item) => sum + pinWeight(item), 0);
      const approxTip = groupNeedsApproxTip(group);
      if (weight === 1 && group.items.length === 1) {
        const item = group.items[0];
        const marker = priceMarker(item, group.lat, group.lon);
        catalogLayer.addLayer(marker);
        markerByKey.set(listingKey(item), marker);
        continue;
      }
      // Jedno číslo na místě (jako konkurence); rozbalení až po kliku — ne stack cenovek.
      const size = weight > 99 ? 48 : weight > 9 ? 42 : 36;
      const marker = L.marker([group.lat, group.lon], {
        pane: "pinPane",
        icon: L.divIcon({
          className: "price-cluster-wrap",
          html: `<div class="price-cluster${approxTip ? " is-approx" : ""}">${weight}</div>`,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }),
        zIndexOffset: 200,
      });
      if (approxTip) bindApproxTip(marker, weight);
      marker.on("click", () => {
        if (drawingCircle) {
          placeCircle(group.lat, group.lon);
          return;
        }
        // Nikdy nerozbaluj ceny na mapu — popup + seznam vpravo.
        const serverCluster = group.items.some((item) => item.cluster);
        if (!serverCluster && (groupNeedsApproxTip(group) || catalogMap.getZoom() >= 14)) {
          openStackPopup(marker, group);
          return;
        }
        catalogMap.setView([group.lat, group.lon], Math.min(catalogMap.getZoom() + 2, 16));
      });
      marker.on("mouseover", () => {
        if (touchMap.matches) return; // mobile: grouping never scrolls the list
        const first = group.items.find((item) => listEl.querySelector(`[data-open-offer="${CSS.escape(listingKey(item))}"]`));
        if (first) highlightCard(listingKey(first));
      });
      marker.on("mouseout", () => { if (!touchMap.matches) clearCardHighlight(); });
      catalogLayer.addLayer(marker);
      for (const item of group.items) clusterByKey.set(listingKey(item), { marker, item });
    }
    if (keepHover) highlightListing(keepHover);
  }

  function updateCountLabel() {
    const el = $("catalog-count");
    if (!el) return;
    const unmapped = total - pinned;
    el.textContent = unmapped > 0 ? `${total} nemovitostí · ${pinned} na mapě` : `${total} nemovitostí`;
  }

  function renderMapPins(items) {
    pinItems = items || [];
    updateCountLabel();
    ensureMap();
    if (!catalogMap || !catalogLayer) return;
    drawPinLayer();
  }

  let priceChart = null;
  let priceChartData = null;

  function priceSeries(history) {
    const pts = (history || [])
      .map((row) => ({ t: new Date(row.seen_at).getTime(), p: Number(row.price_czk) }))
      .filter((pt) => Number.isFinite(pt.t) && Number.isFinite(pt.p))
      .sort((a, b) => a.t - b.t);
    // Zachovat první, každou změnu a poslední záznam (kontroly bez změny jen zahušťují graf).
    const out = [];
    pts.forEach((pt, index) => {
      const prev = out.length ? out[out.length - 1] : null;
      if (!prev || prev.p !== pt.p || index === pts.length - 1) out.push(pt);
    });
    if (out.length === 1) out.push({ t: Math.max(Date.now(), out[0].t + 3600e3), p: out[0].p });
    return out;
  }

  function historyChart(history) {
    if (!history?.length) return `<p class="empty">Cena se začne ukládat při další kontrole monitoru.</p>`;
    const series = priceSeries(history);
    if (!series.length) return `<p class="empty">U této nabídky zatím není číselná cena.</p>`;
    priceChartData = series;
    const first = series[0].p;
    const last = series[series.length - 1].p;
    const diff = last - first;
    const pct = first ? (diff / first) * 100 : 0;
    const dir = diff < 0 ? "down" : diff > 0 ? "up" : "flat";
    const arrow = dir === "down" ? "▼" : dir === "up" ? "▲" : "●";
    const pctText = `${Math.abs(pct) < 10 ? Math.abs(pct).toFixed(1).replace(".", ",") : Math.round(Math.abs(pct))} %`;
    const head =
      dir === "flat"
        ? `<span class="ph-chip flat">${arrow} Cena beze změny</span><span class="ph-sub">od ${escapeHtml(new Date(series[0].t).toLocaleDateString("cs-CZ"))}</span>`
        : `<span class="ph-chip ${dir}">${arrow} ${escapeHtml(formatKcDelta(diff))} (${diff < 0 ? "−" : "+"}${pctText})</span><span class="ph-sub">od první ceny ${escapeHtml(formatKc(first))}</span>`;
    const changes = [];
    for (let k = series.length - 1; k > 0; k--) {
      const d = series[k].p - series[k - 1].p;
      if (d) changes.push(`<li><span>${escapeHtml(formatTime(series[k].t))}</span><strong>${escapeHtml(formatKc(series[k].p))}<em class="${d < 0 ? "down" : "up"}">${d < 0 ? "▼" : "▲"} ${escapeHtml(formatKcDelta(d))}</em></strong></li>`);
    }
    return `
      <div class="price-history">
        <div class="ph-head">${head}</div>
        <div class="ph-canvas"><canvas id="price-chart-canvas" aria-label="Vývoj ceny"></canvas></div>
        ${changes.length ? `<ul class="price-log">${changes.join("")}</ul>` : ""}
      </div>
    `;
  }

  function loadChartJs() {
    if (window.Chart) return Promise.resolve();
    if (window.__chartJsLoading) return window.__chartJsLoading;
    window.__chartJsLoading = new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = "https://unpkg.com/chart.js@4.4.1/dist/chart.umd.js";
      el.integrity = "sha384-dug+JxfBvklEQdJ4AYuBBAIScUz0bVN73xpy273gcAwHjb3qI0fXmuYNaNfdyYJG";
      el.crossOrigin = "anonymous";
      el.onload = resolve;
      el.onerror = reject;
      document.head.appendChild(el);
    });
    return window.__chartJsLoading;
  }

  async function mountPriceChart() {
    priceChart?.destroy();
    priceChart = null;
    const canvas = $("price-chart-canvas");
    const series = priceChartData;
    if (!canvas || !series?.length) return;
    try {
      await loadChartJs();
    } catch (_) {
      canvas.parentElement.innerHTML = `<p class="empty">Graf se nepodařilo načíst.</p>`;
      return;
    }
    if (!canvas.isConnected) return;
    const prices = series.map((pt) => pt.p);
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    // Dynamické osy: i při malém pohybu je rozdíl na grafu čitelný.
    const range = hi - lo;
    const pad = Math.max(range * 0.6, hi * 0.01, 50);
    const yMin = Math.max(0, Math.floor((lo - pad) / 50) * 50);
    const yMax = Math.ceil((hi + pad) / 50) * 50;
    const spanMs = series[series.length - 1].t - series[0].t;
    const fmtTick = (v) =>
      new Date(v).toLocaleDateString("cs-CZ", spanMs > 2 * 86400e3 ? { day: "numeric", month: "numeric" } : { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
    const green = "#2f8f2f";
    const red = "#cf2929";
    const ink = "#163300";
    const deltaLabels = {
      id: "deltaLabels",
      afterDatasetsDraw(chart) {
        const { ctx } = chart;
        const meta = chart.getDatasetMeta(0);
        ctx.save();
        ctx.font = "700 11px Inter, sans-serif";
        ctx.textAlign = "center";
        series.forEach((pt, k) => {
          if (!k) return;
          const d = pt.p - series[k - 1].p;
          if (!d) return;
          const el = meta.data[k];
          ctx.fillStyle = d < 0 ? green : red;
          const text = `${d < 0 ? "−" : "+"}${Math.abs(d).toLocaleString("cs-CZ")}`;
          const half = ctx.measureText(text).width / 2;
          const x = Math.min(Math.max(el.x, chart.chartArea.left + half), chart.width - half - 2);
          ctx.fillText(text, x, el.y + (d < 0 ? 20 : -12));
        });
        ctx.restore();
      },
    };
    priceChart = new window.Chart(canvas, {
      type: "line",
      data: {
        datasets: [
          {
            data: series.map((pt) => ({ x: pt.t, y: pt.p })),
            stepped: "after",
            borderColor: ink,
            borderWidth: 2.5,
            backgroundColor: (ctx) => {
              const { chart } = ctx;
              if (!chart.chartArea) return "rgba(159,232,112,0.2)";
              const g = chart.ctx.createLinearGradient(0, chart.chartArea.top, 0, chart.chartArea.bottom);
              g.addColorStop(0, "rgba(159,232,112,0.35)");
              g.addColorStop(1, "rgba(159,232,112,0)");
              return g;
            },
            fill: true,
            clip: false,
            pointRadius: (ctx) => (ctx.dataIndex === 0 || series[ctx.dataIndex].p !== series[ctx.dataIndex - 1]?.p || ctx.dataIndex === series.length - 1 ? 4 : 0),
            pointHoverRadius: 6,
            pointBackgroundColor: "#fff",
            pointBorderColor: ink,
            pointBorderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: { top: 18, right: 22, bottom: 0, left: 8 } },
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: {
            type: "linear",
            min: series[0].t,
            max: series[series.length - 1].t,
            offset: false,
            grid: { display: false },
            border: { display: false },
            ticks: { maxTicksLimit: 4, color: "#7a8178", font: { size: 11, weight: 600 }, callback: fmtTick, align: "inner" },
          },
          y: {
            min: yMin,
            max: yMax,
            grid: { color: "rgba(14,15,12,0.07)" },
            border: { display: false },
            ticks: {
              maxTicksLimit: 5,
              color: "#7a8178",
              font: { size: 11, weight: 600 },
              callback: (v) => Number(v).toLocaleString("cs-CZ"),
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            backgroundColor: "#0e0f0c",
            padding: 10,
            cornerRadius: 10,
            titleFont: { size: 11, weight: 600 },
            bodyFont: { size: 13, weight: 800 },
            callbacks: {
              title: (items) => formatTime(items[0].parsed.x),
              label: (item) => formatKc(item.parsed.y),
            },
          },
        },
      },
      plugins: [deltaLabels],
    });
  }

  function closeLightbox() {
    $("catalog-lightbox").hidden = true;
    document.body.classList.remove("catalog-lightbox-open");
  }

  function showLightbox(index) {
    if (!lightboxPhotos.length) return;
    lightboxIndex = (index + lightboxPhotos.length) % lightboxPhotos.length;
    $("lightbox-photo").src = lightboxPhotos[lightboxIndex];
    $("lightbox-count").textContent = `${lightboxIndex + 1} / ${lightboxPhotos.length}`;
    $("catalog-lightbox").hidden = false;
    document.body.classList.add("catalog-lightbox-open");
  }

  function destroyDetailMap() {
    exitDetailMapFullscreen();
    if (detailMap) {
      detailMap.remove();
      detailMap = null;
    }
  }

  function exitDetailMapFullscreen() {
    $("detail-map-wrap")?.classList.remove("is-full");
    document.body.classList.remove("detail-map-full");
    const btn = $("detail-map-full");
    if (btn) setLabeled(btn, "Celá obrazovka", "maximize");
    if (detailMap) setTimeout(() => detailMap.invalidateSize(), 80);
  }

  function toggleDetailMapFullscreen() {
    const wrap = $("detail-map-wrap");
    if (!wrap || !detailMap) return;
    const on = wrap.classList.toggle("is-full");
    document.body.classList.toggle("detail-map-full", on);
    setLabeled($("detail-map-full"), on ? "Zavřít" : "Celá obrazovka", on ? "x" : "maximize");
    setTimeout(() => detailMap.invalidateSize(), 80);
  }

  function mountDetailMap(item) {
    destroyDetailMap();
    const el = $("detail-map");
    const lat = Number(item.lat);
    const lon = Number(item.lon);
    if (!el || !Number.isFinite(lat) || !Number.isFinite(lon) || typeof L === "undefined") return;
    detailMap = L.map(el, { scrollWheelZoom: true, zoomControl: true, attributionControl: false }).setView([lat, lon], 15);
    addBaseTiles(detailMap);
    const marker = L.marker([lat, lon], {
      icon: L.divIcon({
        className: "price-pin-wrap",
        html: `<div class="price-pin">${escapeHtml(pinPrice(item))}</div>`,
        iconSize: [88, 32],
        iconAnchor: [44, 16],
      }),
      zIndexOffset: 1200,
    }).addTo(detailMap);
    requestAnimationFrame(() => {
      detailMap?.invalidateSize();
      marker.getElement()?.classList.add("is-open");
    });
  }

  function closeModal() {
    closeLightbox();
    destroyDetailMap();
    $("catalog-modal").hidden = true;
    document.body.classList.remove("catalog-modal-open");
  }

  async function fetchCatalogItem(item) {
    const params = new URLSearchParams();
    if (item?.monitor_id) params.set("monitor_id", item.monitor_id);
    if (item?.id != null) params.set("id", String(item.id));
    if (item?.listing_key) params.set("listing_key", item.listing_key);
    if (item?.url) params.set("url", item.url);
    return fetch(`/api/catalog/item?${params}`);
  }

  // AI summary is fetched after the detail is shown. A 404 (AI off) or any error just leaves the box hidden.
  async function loadAiSummary(item) {
    const box = $("detail-ai");
    const text = $("detail-ai-text");
    if (!box || !text || item.id == null) return;
    try {
      const response = await fetch(`/api/listings/${encodeURIComponent(item.id)}/summary`, { credentials: "same-origin" });
      if (!response.ok) return;
      const data = await response.json();
      if (!data.summary || !box.isConnected) return;
      text.textContent = data.summary;
      box.hidden = false;
    } catch {}
  }

  async function openDetail(monitorId, listingId, extra = {}) {
    const modal = $("catalog-modal");
    const host = $("catalog-detail");
    catalogMap?.closePopup();
    modal.hidden = false;
    document.body.classList.add("catalog-modal-open");
    host.innerHTML = `<p class="empty">Načítám detail…</p>`;
    let item;
    try {
      const response = await fetchCatalogItem({
        monitor_id: monitorId,
        id: listingId,
        listing_key: extra.listing_key || "",
        url: extra.url || "",
      });
      item = await response.json().catch(() => ({}));
      if (!response.ok) {
        window.reportClientError?.(String(item.detail || "Detail inzerátu se nenačetl"), "catalog-detail");
        host.innerHTML = "";
        return;
      }
    } catch {
      window.reportClientError?.("Detail inzerátu se nenačetl", "catalog-detail");
      host.innerHTML = "";
      return;
    }
    const photos = item.photos?.length ? item.photos : item.image_url ? [item.image_url] : [];
    lightboxPhotos = photos;
    lightboxIndex = 0;
    const extras = item.extras || {};
    const specs = extras.specs || [];
    const flags = item.flags || extras.flags || [];
    const description = snippet(item.description);
    destroyDetailMap();
    host.innerHTML = `
      <div class="detail-gallery" data-index="0">
        ${photos[0] ? `<img id="detail-photo" src="${escapeHtml(photos[0])}" alt="" />` : `<div class="no-photo detail-no-photo" aria-hidden="true"></div>`}
        ${
          photos.length > 1
            ? `<button class="gallery-nav prev" type="button">‹</button><button class="gallery-nav next" type="button">›</button>`
            : ""
        }
        ${photos[0] ? `<p class="gallery-count">1 / ${photos.length}</p><p class="gallery-zoom">Celá obrazovka</p>` : ""}
      </div>
      <div class="detail-grid">
        <div class="detail-copy">
          <p class="offer-kicker">${escapeHtml((item.portal || listingLinks(item).map((row) => row.label || row.portal).join(" · ")) || "")} · ${escapeHtml((item.monitors || []).map((row) => row.name).join(" · ") || item.monitor_name || "")}</p>
          <h2>${escapeHtml(item.name || item.locality)}</h2>
          <p>${escapeHtml(item.extras?.address || item.locality || "")}</p>
          ${item.gone ? `<p class="offer-badge offer-badge-gone">Prodáno</p>` : ""}
          ${
            listingLinks(item).length
              ? `<div class="offer-link-list">${listingLinks(item)
                  .map((link) => {
                    const agency = link.agency ? ` · ${escapeHtml(link.agency)}` : "";
                    return `<a href="${escapeHtml(link.url)}" target="_blank" rel="noreferrer">${escapeHtml(link.label || link.portal || "Web")}${agency}</a>`;
                  })
                  .join("")}</div>`
              : ""
          }
          ${factsHtml(item)}
          <section class="detail-ai" id="detail-ai" hidden>
            <p class="detail-ai-label">AI shrnutí</p>
            <p class="detail-ai-text" id="detail-ai-text"></p>
            <p class="detail-ai-note">Vygenerováno AI, může obsahovat nepřesnosti.</p>
          </section>
          ${description ? `<div class="detail-desc-wrap"><p class="detail-desc" id="detail-desc">${escapeHtml(item.description)}</p><div class="detail-desc-toggle" id="detail-desc-toggle" hidden><button type="button" class="detail-desc-more" id="detail-desc-more" aria-expanded="false"><span>Zobrazit více</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button></div></div>` : `<p class="empty">Portál u této nabídky zatím neposlal popis.</p>`}
          <section class="detail-note">
            <label class="detail-note-head" for="detail-note">
              <span class="detail-note-title"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.6 5.6 18.4 10.4 8 20.8H3.2V16z"/><path d="M11.8 7.4 16.6 12.2"/></svg>Moje poznámka</span>
              <span class="detail-note-hint" id="detail-note-hint">${item.note ? "Uloženo" : "Vidíš jen ty"}</span>
            </label>
            <textarea id="detail-note" rows="2" placeholder="Termín prohlídky, dojem, co ještě ověřit…">${escapeHtml(item.note || "")}</textarea>
          </section>
          <h3>Historie cen</h3>
          ${historyChart(item.price_history)}
        </div>
        <aside class="detail-side">
          <div class="detail-price">
            <p>Cena</p>
            <strong>${escapeHtml(item.price_label || "Cena na dotaz")}</strong>
            ${fxNote(item)}
            ${item.discount_czk ? `<p>Sleva ${escapeHtml(formatKc(item.discount_czk))}${item.discount_pct ? ` · ${item.discount_pct} %` : ""}</p>` : ""}
            ${listingLinks(item)
              .map(
                (link) =>
                  `<a class="btn" href="${escapeHtml(link.url)}" target="_blank" rel="noreferrer">${icon("external")}<span>Otevřít na ${escapeHtml(link.label || link.portal || item.portal || "webu")}</span></a>`,
              )
              .join("")}
            ${item.maps_url ? `<a class="btn btn-ghost" href="${escapeHtml(item.maps_url)}" target="_blank" rel="noreferrer">${icon("pin")}<span>Google Maps</span></a>` : ""}
          </div>
          <div id="detail-commute" class="detail-commute" hidden></div>
          <div class="detail-actions">
            ${detailActionButton("detail-save", item.status === "saved" ? "Uložené" : "Uložit", ICON_SAVE, item.status === "saved")}
            ${detailActionButton("detail-hide", item.status === "hidden" ? "Odkrýt" : "Skrýt", item.status === "hidden" ? ICON_SHOW : ICON_HIDE, item.status === "hidden")}
          </div>
          ${
            Number.isFinite(Number(item.lat)) && Number.isFinite(Number(item.lon))
              ? `<div class="detail-map-wrap" id="detail-map-wrap">
                  <div id="detail-map" class="detail-map"></div>
                  <button class="detail-map-full" id="detail-map-full" type="button">${icon("maximize")}<span>Celá obrazovka</span></button>
                </div>`
              : ""
          }
        </aside>
      </div>
    `;
    const gallery = host.querySelector(".detail-gallery");
    const img = $("detail-photo");
    const count = host.querySelector(".gallery-count");
    const show = (next) => {
      if (!photos.length || !img) return;
      lightboxIndex = (next + photos.length) % photos.length;
      img.src = photos[lightboxIndex];
      if (count) count.textContent = `${lightboxIndex + 1} / ${photos.length}`;
    };
    if (gallery && photos.length) {
      gallery.addEventListener("click", (event) => {
        if (event.target.closest(".gallery-nav")) return;
        showLightbox(lightboxIndex);
      });
      host.querySelector(".prev")?.addEventListener("click", (event) => {
        event.stopPropagation();
        show(lightboxIndex - 1);
      });
      host.querySelector(".next")?.addEventListener("click", (event) => {
        event.stopPropagation();
        show(lightboxIndex + 1);
      });
    }
    const descEl = $("detail-desc");
    const descMore = $("detail-desc-more");
    const descToggle = $("detail-desc-toggle");
    if (descEl && descMore && descToggle) {
      const wrap = descEl.parentElement;
      let collapsedPx = 0;
      const measure = () => {
        const lh = parseFloat(getComputedStyle(descEl).lineHeight) || 24;
        collapsedPx = Math.round(lh * 10);
      };
      requestAnimationFrame(() => {
        measure();
        if (descEl.scrollHeight > collapsedPx + 4) {
          descEl.style.maxHeight = `${collapsedPx}px`;
          descToggle.hidden = false;
          wrap.classList.add("is-collapsed");
        }
      });
      descMore.addEventListener("click", () => {
        const opening = descMore.getAttribute("aria-expanded") !== "true";
        descMore.setAttribute("aria-expanded", String(opening));
        descMore.querySelector("span").textContent = opening ? "Zobrazit méně" : "Zobrazit více";
        wrap.classList.toggle("is-collapsed", !opening);
        if (opening) {
          descEl.style.maxHeight = `${descEl.scrollHeight}px`;
        } else {
          descEl.style.maxHeight = `${descEl.scrollHeight}px`;
          void descEl.offsetHeight;
          descEl.style.maxHeight = `${collapsedPx}px`;
        }
      });
    }
    mountDetailMap(item);
    mountPriceChart();
    loadAiSummary(item);
    $("detail-map-full")?.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleDetailMapFullscreen();
    });
    $("detail-note")?.addEventListener("change", () => {
      if (item.url) setListingUser(item.url, item.status || "", $("detail-note").value);
    });
    let noteTimer;
    const noteEl = $("detail-note");
    const growNote = () => { if (noteEl) { noteEl.style.height = "auto"; noteEl.style.height = `${Math.max(64, noteEl.scrollHeight)}px`; } };
    growNote();
    $("detail-note")?.addEventListener("input", () => {
      growNote();
      const hint = $("detail-note-hint");
      if (hint) hint.textContent = "Ukládám…";
      clearTimeout(noteTimer);
      noteTimer = setTimeout(() => {
        if (!item.url) return;
        setListingUser(item.url, item.status || "", $("detail-note").value);
        if (hint) hint.textContent = "Uloženo";
      }, 400);
    });
    $("detail-save")?.addEventListener("click", () => {
      setListingUser(item.url, item.status === "saved" ? "" : "saved", $("detail-note")?.value || item.note || "");
      item.status = item.status === "saved" ? "" : "saved";
      setDetailAction("detail-save", item.status === "saved" ? "Uložené" : "Uložit", item.status === "saved", ICON_SAVE);
    });
    $("detail-hide")?.addEventListener("click", () => {
      setListingUser(item.url, item.status === "hidden" ? "" : "hidden", $("detail-note")?.value || item.note || "");
      item.status = item.status === "hidden" ? "" : "hidden";
      setDetailAction(
        "detail-hide",
        item.status === "hidden" ? "Odkrýt" : "Skrýt",
        item.status === "hidden",
        item.status === "hidden" ? ICON_SHOW : ICON_HIDE,
      );
    });
    fillCommute(item);
  }

  function formatCommute(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "—";
    const minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 60) return `${minutes} min`;
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  }

  async function fillCommute(item) {
    const host = $("detail-commute");
    const points = (appSettings.commute_points || []).filter(
      (point) => Number.isFinite(Number(point.lat)) && Number.isFinite(Number(point.lon)),
    );
    const lat = Number(item.lat);
    const lon = Number(item.lon);
    if (!host || !points.length || !Number.isFinite(lat) || !Number.isFinite(lon)) return;
    host.hidden = false;
    host.innerHTML = `<p class="detail-commute-label">Dojezd</p><p class="empty">Počítám časy…</p>`;
    const rows = [];
    for (const point of points) {
      const fromLat = Number(point.lat);
      const fromLon = Number(point.lon);
      const dest = item.locality || item.name || "";
      const origin = point.address || "";
      const response = await fetch(
        `/api/commute?from_lat=${fromLat}&from_lon=${fromLon}&to_lat=${lat}&to_lon=${lon}` +
          `&from_address=${encodeURIComponent(origin)}&to_address=${encodeURIComponent(dest)}`,
      );
      const times = response.ok ? await response.json() : {};
      const modes = [
        ["Auto", formatCommute(times.drive), "car"],
        ["MHD", formatCommute(times.transit), "transit"],
        ["Pěšky", formatCommute(times.walk), "walk"],
      ];
      const dir = `https://www.google.com/maps/dir/?api=1&origin=${fromLat},${fromLon}&destination=${lat},${lon}`;
      rows.push(`
        <article class="commute-card">
          <div class="commute-card-head">
            <p class="commute-card-name">${escapeHtml(point.name || "Bod")}</p>
            ${point.address ? `<p class="commute-card-addr">${escapeHtml(point.address)}</p>` : ""}
          </div>
          <div class="commute-modes">
            ${modes
              .map(
                ([label, value, kind]) =>
                  `<div class="commute-mode">
                    <span>${escapeHtml(label)}</span>
                    <strong class="is-${kind}">${escapeHtml(value)}</strong>
                  </div>`,
              )
              .join("")}
          </div>
          <a class="commute-dir" href="${dir}&travelmode=transit" target="_blank" rel="noreferrer">Trasa v Google Maps</a>
        </article>
      `);
    }
    host.innerHTML = `<p class="detail-commute-label">Dojezd</p>${rows.join("")}`;
  }

  async function setListingUser(url, status, note) {
    if (!url) return;
    const response = await fetch("/api/catalog/user", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, status, note }),
    });
    if (!response.ok) return;
    const item = lastItems.find((row) => row.url === url);
    if (item) {
      item.status = status;
      if (note != null) item.note = note;
    }
    renderList(lastItems, false);
    renderCompareTray();
    if (selected.status && selected.status !== "all") loadCatalog();
    else if (selected.status === "" && status === "hidden") loadCatalog();
  }

  function itemByUrl(url) {
    return lastItems.find((row) => row.url === url);
  }

  function toggleCompare(url) {
    if (!url) return;
    if (compareUrls.includes(url)) compareUrls = compareUrls.filter((item) => item !== url);
    else if (compareUrls.length < 3) compareUrls.push(url);
    renderCompareTray();
    renderList(lastItems, false);
  }

  function renderCompareTray() {
    const tray = $("compare-tray");
    const host = $("compare-items");
    if (!tray || !host) return;
    tray.hidden = compareUrls.length === 0;
    host.innerHTML = compareUrls
      .map((url) => {
        const item = itemByUrl(url);
        return `<button type="button" class="chip" data-drop-compare="${escapeHtml(url)}"><span class="cmp-name">${escapeHtml(item?.locality || item?.name || "Položka")}</span><span class="cmp-x" aria-hidden="true">×</span></button>`;
      })
      .join("");
  }

  let cmpItems = [];

  function cmpRows(items) {
    // Union of all parameters; a missing value is shown as an en dash.
    const rows = [];
    const index = new Map();
    const put = (label, ownerIdx, value) => {
      const key = String(label).toLowerCase();
      if (!index.has(key)) {
        index.set(key, rows.length);
        rows.push({ label, values: items.map(() => "–") });
      }
      if (value != null && value !== "") rows[index.get(key)].values[ownerIdx] = String(value);
    };
    items.forEach((item, i) => {
      const per = item.price_czk && item.area_m2 ? `${formatKc(Math.round(item.price_czk / item.area_m2))} / m²` : "";
      put("Cena", i, item.price_label || (item.price_czk ? formatKc(item.price_czk) : ""));
      put("Cena za m²", i, per);
      put("Sleva", i, item.discount_czk ? `${formatKc(item.discount_czk)}${item.discount_pct ? ` (${item.discount_pct} %)` : ""}` : "");
      const hist = (item.price_history || []).map((r) => Number(r.price_czk)).filter(Number.isFinite);
      if (hist.length > 1 && hist[0] !== item.price_czk) put("Původní cena", i, formatKc(hist[0]));
      if (hist.length > 1) put("Změn ceny", i, String(hist.filter((p, k) => k && p !== hist[k - 1]).length));
      const vp = Number(item.value_pct);
      if (Number.isFinite(vp) && item.value_comps) put("Cena oproti okolí", i, `${vp >= 0 ? "−" : "+"}${Math.abs(Math.round(vp))} %`);
      if (item.value_median_m2) put("Medián okolí", i, `${Number(item.value_median_m2).toLocaleString("cs-CZ")} Kč / m²`);
    });
    items.forEach((item, i) => {
      for (const fact of detailFacts(item)) put(fact.label, i, fact.value);
    });
    items.forEach((item, i) => {
      if (item.first_seen) {
        const days = Math.max(0, Math.floor((Date.now() - new Date(item.first_seen).getTime()) / 86400000));
        if (Number.isFinite(days)) put("V nabídce", i, days === 0 ? "dnes" : days === 1 ? "1 den" : days < 5 ? `${days} dny` : `${days} dní`);
      }
      put("Stav inzerátu", i, item.gone ? "nedostupný" : "aktivní");
      put("Portál", i, item.portal || "");
      const sources = (item.links || []).filter((l) => l.url && !l.gone).length;
      if (sources > 1) put("Počet zdrojů", i, String(sources));
      put("Hlídání", i, (item.monitors || []).map((row) => row.name).join(", ") || item.monitor_name || "");
    });
    return rows;
  }

  function renderCompareView() {
    const grid = $("compare-grid");
    if (!grid) return;
    const items = cmpItems;
    const n = items.length;
    const rows = cmpRows(items);
    const cell = (col, html, cls = "") => `<div class="cmp-cell c${col % 2}${cls ? " " + cls : ""}">${html}</div>`;
    let html = "";
    items.forEach((item, i) => {
      const photo = item.photos?.[0] || item.image_url;
      html += cell(
        i,
        `${photo ? `<img src="${escapeHtml(photo)}" alt="" loading="lazy" />` : `<div class="no-photo compare-no-photo" aria-hidden="true"></div>`}
        <h3>${escapeHtml(item.locality || item.name || "")}</h3>
        <div class="cmp-actions">
          <a class="btn btn-ghost" href="${escapeHtml(item.url || "")}" target="_blank" rel="noreferrer">Otevřít</a>
          <button type="button" class="btn btn-ghost cmp-remove" data-cmp-remove="${escapeHtml(item.url || "")}">Odebrat</button>
        </div>`,
        "cmp-head"
      );
    });
    for (const row of rows) {
      row.values.forEach((value, i) => {
        html += cell(i, `<span class="cmp-l">${escapeHtml(row.label)}</span><strong class="${value === "–" ? "is-empty" : ""}">${escapeHtml(value)}</strong>`, "cmp-row");
      });
    }
    grid.style.setProperty("--n", String(n));
    grid.classList.toggle("is-single", n === 1);
    grid.innerHTML = html;
    grid.scrollLeft = 0;
    let dots = $("compare-dots");
    if (!dots) {
      dots = document.createElement("div");
      dots.id = "compare-dots";
      dots.className = "cmp-dots";
      grid.insertAdjacentElement("afterend", dots);
    }
    const perPage = window.matchMedia("(max-width: 900px)").matches ? 2 : 3;
    const pages = Math.ceil(n / perPage);
    dots.hidden = pages <= 1;
    dots.innerHTML = Array.from({ length: pages }, (_, i) => `<button type="button" aria-label="Strana ${i + 1}" data-cmp-page="${i}" class="${i === 0 ? "on" : ""}"></button>`).join("");
    const pageW = () => grid.clientWidth + 12;
    grid.onscroll = () => {
      const p = Math.round(grid.scrollLeft / Math.max(1, pageW()));
      dots.querySelectorAll("button").forEach((b, i) => b.classList.toggle("on", i === p));
    };
    dots.onclick = (event) => {
      const btn = event.target.closest("[data-cmp-page]");
      if (btn) grid.scrollTo({ left: Number(btn.dataset.cmpPage) * pageW(), behavior: "smooth" });
    };
  }

  async function openCompare() {
    const modal = $("compare-modal");
    const grid = $("compare-grid");
    if (!modal || !grid || !compareUrls.length) return;
    const items = [];
    for (const url of compareUrls) {
      const local = itemByUrl(url) || { url };
      const response = await fetchCatalogItem(local);
      if (response.ok) items.push(await response.json());
      else if (itemByUrl(url)) items.push(local);
    }
    cmpItems = items;
    renderCompareView();
    modal.hidden = false;
    document.body.classList.add("catalog-modal-open");
  }

  $("compare-grid")?.addEventListener("click", (event) => {
    const rm = event.target.closest("[data-cmp-remove]");
    if (!rm) return;
    const url = rm.dataset.cmpRemove;
    cmpItems = cmpItems.filter((item) => (item.url || "") !== url);
    compareUrls = compareUrls.filter((u) => u !== url);
    renderCompareTray();
    renderList(lastItems, false);
    if (!cmpItems.length) closeCompare();
    else renderCompareView();
  });

  function closeCompare() {
    $("compare-modal").hidden = true;
    document.body.classList.remove("catalog-modal-open");
  }

  let catalogBusy = false;
  let catalogQueued = false;

  function setCatalogLoading(listOn, mapOn = listOn) {
    const overlay = $("catalog-map-loading");
    const anim = $("catalog-load-anim");
    if (overlay) {
      overlay.hidden = !mapOn;
      if (anim) {
        // restart looped webp when showing
        if (mapOn) {
          const src = anim.getAttribute("src");
          anim.setAttribute("src", "");
          anim.setAttribute("src", src);
        }
      }
    }
    document.querySelector(".catalog-side")?.classList.toggle("is-loading", Boolean(listOn));
  }

  function finishCatalogLoad(seq, { keepCount = false } = {}) {
    // Only the newest in-flight load may clear busy/loading.
    if (seq !== catalogSeq) return;
    setCatalogLoading(false, false);
    if (!keepCount) {
      const countEl = $("catalog-count");
      if (countEl && /načítám/i.test(countEl.textContent || "")) {
        updateCountLabel();
      }
    }
    catalogBusy = false;
    if (catalogQueued) {
      catalogQueued = false;
      queueMicrotask(() => loadCatalog());
    }
  }

  let catalogRetries = 0;
  let catalogFailed = false;
  // Waits between automatic retries. The server keeps a slow query running and caches its result, so a later attempt picks it up.
  const CATALOG_RETRY_DELAYS = [1500, 3000, 5000, 8000, 12000, 15000, 15000, 15000];

  async function loadCatalog(append = false) {
    if (catalogBusy) {
      if (!append) catalogQueued = true;
      return;
    }
    catalogBusy = true;
    catalogQueued = false;
    if (!append) offset = 0;
    ensureMap();
    if (catalogMap) {
      const b = catalogMap.getBounds();
      lastMapQueryKey = [
        catalogMap.getZoom(),
        b.getSouth().toFixed(5),
        b.getNorth().toFixed(5),
        b.getWest().toFixed(5),
        b.getEast().toFixed(5),
      ].join("|");
    }
    const seq = ++catalogSeq;
    catalogAbort?.abort();
    const ac = new AbortController();
    catalogAbort = ac;
    const timeout = window.setTimeout(() => ac.abort(), 45000);
    if (!append) {
      setCatalogLoading(true, true);
      const countEl = $("catalog-count");
      if (countEl) countEl.textContent = "Načítám…";
    }
    if (!append && selectedPlaces.length) {
      ensurePlaceGeoms()
        .then(() => {
          if (seq !== catalogSeq) return;
          drawPlaceLayer(false);
        })
        .catch(() => {});
    }
    let data;
    try {
      // One request: the list, the total and the pins are cut from the same set on the server.
      const response = await fetch(
        `/api/catalog?${queryString({ limit: LIMIT, offset, include_pins: append ? "0" : "1" })}`,
        { signal: ac.signal },
      );
      if (!response.ok) throw new Error("catalog");
      data = await response.json();
    } catch (err) {
      window.clearTimeout(timeout);
      if (seq !== catalogSeq) {
        // Superseded — newer load owns busy/loading.
        return;
      }
      if (lastItems.length && !append) {
        updateCountLabel();
        finishCatalogLoad(seq, { keepCount: true });
        return;
      }
      if (!append && catalogRetries < CATALOG_RETRY_DELAYS.length) {
        const wait = CATALOG_RETRY_DELAYS[catalogRetries];
        catalogRetries += 1;
        const countEl = $("catalog-count");
        if (countEl) countEl.textContent = `Načítání trvá déle než obvykle, zkouším znovu… (${catalogRetries}/${CATALOG_RETRY_DELAYS.length})`;
        finishCatalogLoad(seq, { keepCount: true });
        window.setTimeout(() => loadCatalog(), wait);
        return;
      }
      catalogFailed = true;
      const failEl = $("catalog-count");
      if (failEl) {
        window.reportClientError?.("Seznam nabídek se nenačetl", "catalog-list");
        failEl.innerHTML = '<button type="button" id="catalog-retry" class="btn">Načíst znovu</button>';
        $("catalog-retry")?.addEventListener("click", () => {
          catalogRetries = 0;
          catalogFailed = false;
          loadCatalog();
        });
      }
      finishCatalogLoad(seq, { keepCount: true });
      return;
    }
    if (seq !== catalogSeq) {
      window.clearTimeout(timeout);
      return;
    }
    catalogRetries = 0;
    catalogFailed = false;
    try {
      applyPlaceGeoms(data.places);
      const items = uniqueOffers(data.items || []);
      total = data.total || 0;
      if (!append) pinned = Number.isFinite(Number(data.pinned)) ? Number(data.pinned) : total;
      renderFacets(data.facets);
      if (append) lastItems = uniqueOffers(lastItems.concat(items));
      else lastItems = items;
      updateCountLabel();
      const empty = $("catalog-empty");
      empty.hidden = lastItems.length > 0;
      if (!lastItems.length) {
        if (selected.status === "saved") empty.textContent = "Zatím nemáš žádné uložené inzeráty.";
        else if (selected.status === "hidden") empty.textContent = "Nic není skryté.";
        else if (selected.discounted) empty.textContent = "Žádné zlevněné nabídky v aktuálním výběru.";
        else empty.textContent = "Nic v uložené nabídce neodpovídá filtrům.";
      }
      renderList(items, append);
      if (!append) {
        renderMapPins(uniqueOffers(data.pins || []));
        drawPlaceLayer(false);
        if (needPlaceFit && (selectedPlaces.length || circleFilter)) goToSelection();
        needPlaceFit = false;
        setCatalogLoading(false, true);
      }
      offset = lastItems.length;
      $("catalog-more").hidden = lastItems.length >= total;
      updateFilterToggle();
      writeUrlState();
      renderViews();
      renderCompareTray();
      syncStatusChips();
    } catch (err) {
      console.warn("catalog render failed", err);
      // A render error must never leave the list empty: load the page again a couple of times.
      if (!append && !lastItems.length && catalogRetries < 3) {
        catalogRetries += 1;
        window.setTimeout(() => loadCatalog(), 1500);
      }
    } finally {
      window.clearTimeout(timeout);
      finishCatalogLoad(seq, { keepCount: true });
    }
  }

  const retryAfterFailure = () => {
    if (catalogFailed && document.visibilityState !== "hidden") {
      catalogRetries = 0;
      catalogFailed = false;
      loadCatalog();
    }
  };
  window.addEventListener("online", retryAfterFailure);
  document.addEventListener("visibilitychange", retryAfterFailure);

  let ignoreCardClick = false;
  listEl.addEventListener("click", (event) => {
    const nav = event.target.closest(".offer-nav");
    const gallery = event.target.closest(".offer-photo");
    if (nav && gallery) {
      event.preventDefault();
      event.stopPropagation();
      showGallery(gallery, Number(gallery.dataset.index || 0) + (nav.classList.contains("next") ? 1 : -1));
      return;
    }
    if (ignoreCardClick) {
      ignoreCardClick = false;
      return;
    }
    const save = event.target.closest("[data-save-url]");
    const hide = event.target.closest("[data-hide-url]");
    const compare = event.target.closest("[data-compare-url]");
    if (save || hide || compare) {
      event.preventDefault();
      event.stopPropagation();
      if (save) {
        const url = save.dataset.saveUrl;
        const item = itemByUrl(url);
        setListingUser(url, item?.status === "saved" ? "" : "saved", item?.note || "");
      } else if (hide) {
        const url = hide.dataset.hideUrl;
        const item = itemByUrl(url);
        setListingUser(url, item?.status === "hidden" ? "" : "hidden", item?.note || "");
      } else toggleCompare(compare.dataset.compareUrl);
      return;
    }
    const card = event.target.closest("[data-open-offer]");
    if (!card) return;
    const [monitorId, listingId] = card.dataset.openOffer.split(":");
    const fromList = lastItems.find((item) => listingKey(item) === card.dataset.openOffer);
    openDetail(monitorId, listingId, fromList || { url: card.dataset.url || "" });
  });

  let galleryStart = null;
  listEl.addEventListener("pointerdown", (event) => {
    const gallery = event.target.closest(".offer-photo");
    if (!gallery || event.target.closest(".offer-nav")) return;
    galleryStart = { gallery, x: event.clientX, y: event.clientY };
  });
  listEl.addEventListener("pointerup", (event) => {
    if (!galleryStart) return;
    const dx = event.clientX - galleryStart.x;
    const dy = event.clientY - galleryStart.y;
    if (Math.abs(dx) > 36 && Math.abs(dx) > Math.abs(dy)) {
      ignoreCardClick = true;
      showGallery(galleryStart.gallery, Number(galleryStart.gallery.dataset.index || 0) + (dx < 0 ? 1 : -1));
    }
    galleryStart = null;
  });
  listEl.addEventListener("pointercancel", () => {
    galleryStart = null;
  });
  listEl.addEventListener("mouseover", (event) => {
    const card = event.target.closest("[data-open-offer]");
    const from = event.relatedTarget;
    if (!card || (from instanceof Node && card.contains(from))) return;
    highlightListing(card.dataset.openOffer);
  });
  listEl.addEventListener("mouseout", (event) => {
    const card = event.target.closest("[data-open-offer]");
    const to = event.relatedTarget;
    if (!card || (to instanceof Node && card.contains(to))) return;
    clearListingHover();
  });

  $("cat-portals")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-portal]");
    if (!button) return;
    selected.portal = button.dataset.portal;
    $("cat-portals").querySelectorAll(".chip").forEach((chip) => chip.classList.toggle("on", chip === button));
    loadCatalog();
  });
  $("cat-offer")?.addEventListener("click", (event) => {
    const button = event.target.closest(".chip");
    if (!button) return;
    selected.offers = new Set([button.dataset.value]);
    syncChipGroup($("cat-offer"), "value", selected.offers);
    loadCatalog();
  });
  $("cat-dispositions")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-disposition]");
    if (!button) return;
    button.classList.toggle("on");
    selected.dispositions = dispositionValues();
    loadCatalog();
  });
  $("cat-dispositions-more")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-disposition]");
    if (button) button.classList.toggle("on");
  });
  $("cat-sort")?.addEventListener("change", () => {
    selected.sort = $("cat-sort").value || "newest";
    loadCatalog();
  });
  $("cat-amenities")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-flag]");
    if (!button) return;
    button.classList.toggle("on");
  });
  $("filters-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "filters-modal") closeFilters();
    const button = event.target.closest(".chip");
    if (!button) return;
    const tri = event.target.closest("[data-tri]");
    if (tri && tri.contains(button)) {
      const on = button.classList.contains("on");
      tri.querySelectorAll(".chip").forEach((chip) => chip.classList.remove("on"));
      if (!on) button.classList.add("on");
      return;
    }
    const group = event.target.closest("[data-filter]");
    if (group && group.contains(button)) button.classList.toggle("on");
  });
  $("cat-toggle")?.addEventListener("click", openFilters);
  $("filters-close")?.addEventListener("click", closeFilters);
  $("filters-reset")?.addEventListener("click", () => {
    clearFilterUi();
    syncPlacesFromDistricts();
    restoredFilterState = true;
    writeUrlState();
    ignoreMapMove += 1;
    window.setTimeout(() => {
      ignoreMapMove = Math.max(0, ignoreMapMove - 1);
    }, 1200);
    loadCatalog();
  });
  $("filters-apply")?.addEventListener("click", () => {
    readFilterUi();
    syncPlacesFromDistricts();
    closeFilters();
    loadCatalog();
  });
  $("catalog-close")?.addEventListener("click", closeModal);
  $("catalog-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "catalog-modal") closeModal();
  });
  $("lightbox-close")?.addEventListener("click", closeLightbox);
  $("lightbox-prev")?.addEventListener("click", () => showLightbox(lightboxIndex - 1));
  $("lightbox-next")?.addEventListener("click", () => showLightbox(lightboxIndex + 1));
  $("catalog-lightbox")?.addEventListener("click", (event) => {
    if (event.target.id === "catalog-lightbox") closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    const lightboxOpen = !$("catalog-lightbox").hidden;
    const filtersOpen = !$("filters-modal").hidden;
    const modalOpen = !$("catalog-modal").hidden;
    if (event.key === "Escape" && lightboxOpen) {
      closeLightbox();
      return;
    }
    if (event.key === "Escape" && filtersOpen) {
      closeFilters();
      return;
    }
    if (event.key === "Escape" && $("detail-map-wrap")?.classList.contains("is-full")) {
      toggleDetailMapFullscreen();
      return;
    }
    if (event.key === "Escape" && !$("compare-modal")?.hidden) {
      closeCompare();
      return;
    }
    if (event.key === "Escape" && modalOpen) closeModal();
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
    if (!typing && location.pathname.replace(/\/$/, "") === "/nabidka" && lastItems.length) {
      if (event.key === "j" || event.key === "k") {
        event.preventDefault();
        focusedIndex = event.key === "j" ? Math.min(lastItems.length - 1, focusedIndex + 1) : Math.max(0, focusedIndex - 1);
        const item = lastItems[focusedIndex];
        const key = `${item.monitor_id}:${item.id}`;
        highlightCard(key);
        highlightListing(key);
      }
      if (event.key === "Enter" && focusedIndex >= 0) {
        const item = lastItems[focusedIndex];
        openDetail(item.monitor_id, item.id, item);
      }
      if (event.key === "f" && focusedIndex >= 0) {
        const item = lastItems[focusedIndex];
        setListingUser(item.url, item.status === "saved" ? "" : "saved", item.note || "");
      }
      if (event.key === "h" && focusedIndex >= 0) {
        const item = lastItems[focusedIndex];
        setListingUser(item.url, item.status === "hidden" ? "" : "hidden", item.note || "");
      }
      if (event.key === "c" && focusedIndex >= 0) toggleCompare(lastItems[focusedIndex].url);
    }
    if (!lightboxOpen && !modalOpen) return;
    if (filtersOpen) return;
    if (event.key === "ArrowLeft") {
      if (lightboxOpen) showLightbox(lightboxIndex - 1);
      else $("catalog-detail")?.querySelector(".prev")?.click();
    }
    if (event.key === "ArrowRight") {
      if (lightboxOpen) showLightbox(lightboxIndex + 1);
      else $("catalog-detail")?.querySelector(".next")?.click();
    }
  });
  let searchTimer = null;
  const scheduleSearch = () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadCatalog(), 300);
  };
  $("cat-search")?.addEventListener("click", () => loadCatalog());
  $("catalog-more")?.addEventListener("click", () => loadCatalog(true));
  ["cat-price-from", "cat-price-to", "cat-area-from", "cat-area-to"].forEach((id) => {
    $(id)?.addEventListener("input", scheduleSearch);
  });
  $("cat-q")?.addEventListener("input", () => {
    clearTimeout(placeSuggestTimer);
    const q = $("cat-q").value.trim();
    placeSuggestTimer = window.setTimeout(() => searchPlaces(q), 180);
  });
  $("cat-q")?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hidePlaceSuggest();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (!$("cat-place-suggest") || $("cat-place-suggest").hidden || !placeSuggestItems.length) return;
      event.preventDefault();
      const last = placeSuggestItems.length - 1;
      if (event.key === "ArrowDown") {
        placeSuggestFocus = placeSuggestFocus < last ? placeSuggestFocus + 1 : 0;
      } else {
        placeSuggestFocus = placeSuggestFocus > 0 ? placeSuggestFocus - 1 : last;
      }
      paintPlaceSuggest();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (placeSuggestFocus >= 0 && placeSuggestItems[placeSuggestFocus]) {
        addPlace(placeSuggestItems[placeSuggestFocus]);
      }
    }
  });
  $("cat-place-suggest")?.addEventListener("pointerdown", (event) => {
    const btn = event.target.closest("[data-place-suggest]");
    if (!btn) return;
    event.preventDefault();
    event.stopPropagation();
    const item = placeSuggestItems[Number(btn.dataset.placeSuggest)];
    if (item) addPlace(item);
  });
  $("cat-place-chips")?.addEventListener("click", (event) => {
    const del = event.target.closest("[data-del-place]");
    if (del) removePlace(del.dataset.delPlace);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".catalog-search-wrap")) hidePlaceSuggest();
  });
  $("cat-status")?.addEventListener("click", (event) => {
    const discount = event.target.closest("[data-discounted]");
    const status = event.target.closest("[data-status]");
    if (discount) {
      selected.discounted = !selected.discounted;
      if (selected.discounted) selected.status = "";
      syncStatusChips();
      loadCatalog();
      return;
    }
    if (!status) return;
    selected.status = status.dataset.status || "";
    selected.discounted = false;
    syncStatusChips();
    loadCatalog();
  });
  $("cat-hits")?.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-hits]");
    if (!chip) return;
    const next = chip.dataset.hits || "";
    selected.hits = selected.hits === next ? "" : next;
    syncStatusChips();
    loadCatalog();
  });

  /* ---- "Uložit pohled" = create a watchdog from the current filters (or upsell when the plan limit is reached) ---- */
  function watchQuota() {
    let billing = null;
    let monitors = [];
    try {
      billing = typeof statusCache !== "undefined" ? statusCache?.billing : null;
      monitors = typeof statusCache !== "undefined" ? statusCache?.monitors || [] : [];
    } catch {
      billing = null;
    }
    const limit = billing ? billing.watch_limit : null;
    const used = monitors.filter((item) => item.enabled).length;
    return { limit: limit == null ? null : Number(limit), used, label: billing?.label || "Zdarma", reached: limit != null && used >= Number(limit) };
  }

  function watchCriteria() {
    const f = filters();
    const offerNames = { pronajem: "Pronájem", prodej: "Prodej", drazby: "Dražby", podily: "Podíly" };
    const estateNames = { byt: "Byt", dum: "Dům", pozemek: "Pozemek", projekt: "Projekt", komercni: "Komerční", ostatni: "Ostatní" };
    const rows = [];
    const offers = [...selected.offers];
    rows.push(["Typ", `${(offers.length ? offers : ["pronajem"]).map((o) => offerNames[o] || o).join(", ")}${selected.estates.size ? " · " + [...selected.estates].map((e) => estateNames[e] || e).join(", ") : ""}`]);
    const where = selectedPlaces.map((p) => p.label || p.id).concat([...selected.districts].filter((d) => !selectedPlaces.some((p) => slugForPlace(p.id) === d)));
    if (where.length) rows.push(["Lokalita", where.join(", ")]);
    else if (f.q) rows.push(["Lokalita", f.q]);
    if (selected.dispositions.size) rows.push(["Dispozice", [...selected.dispositions].join(", ")]);
    if (f.price_from || f.price_to) {
      rows.push(["Cena", `${f.price_from ? "od " + Number(f.price_from).toLocaleString("cs-CZ") : ""} ${f.price_to ? "do " + Number(f.price_to).toLocaleString("cs-CZ") : ""} Kč`.trim()]);
    }
    if (f.area_from || f.area_to) {
      rows.push(["Plocha", `${f.area_from ? "od " + f.area_from : ""} ${f.area_to ? "do " + f.area_to : ""} m²`.trim()]);
    }
    return rows;
  }

  function defaultWatchName() {
    const rows = Object.fromEntries(watchCriteria());
    const offer = String(rows["Typ"] || "Hlídání").split(" · ")[0];
    const where = rows["Lokalita"] ? String(rows["Lokalita"]).split(", ").slice(0, 2).join(", ") : "";
    const disp = rows["Dispozice"] ? String(rows["Dispozice"]).split(", ").slice(0, 2).join(", ") : "";
    return [offer, disp, where].filter(Boolean).join(" · ").slice(0, 60);
  }

  function ensureSaveViewModal() {
    let modal = $("view-save-modal");
    if (modal) return modal;
    modal = document.createElement("div");
    modal.id = "view-save-modal";
    modal.className = "catalog-modal";
    modal.hidden = true;
    modal.innerHTML = '<div class="catalog-modal-card monitor-modal-card save-view-card"><button class="catalog-close" type="button" data-sv-close aria-label="Zavřít">×</button><div id="view-save-body"></div></div>';
    document.body.appendChild(modal);
    modal.addEventListener("click", (event) => {
      if (event.target === modal || event.target.closest("[data-sv-close], a[href]")) closeSaveViewModal();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hidden) closeSaveViewModal();
    });
    return modal;
  }

  function closeSaveViewModal() {
    const modal = $("view-save-modal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("catalog-modal-open");
  }

  function planSlides() {
    let plans = [];
    try {
      plans = (typeof statusCache !== "undefined" ? statusCache?.billing?.plans : null) || [];
    } catch {
      plans = [];
    }
    const pro = proPlan();
    const start = plans.find((item) => item.id === "start");
    const slides = [
      { id: "pro", tab: "PRO", kicker: "Realitify PRO", price: pro.price, features: pro.features, cta: "Přejít na Pro", note: "3 dny zdarma, zrušíte kdykoli" },
    ];
    if (start) {
      slides.push({ id: "start", tab: "Start", kicker: "Realitify Start", price: start.price_czk, features: start.features || [], cta: "Přejít na Start", note: "Zrušíte kdykoli" });
    }
    return slides;
  }

  function profileAvatarUrl() {
    try {
      if (typeof lastProfile !== "undefined" && lastProfile?.avatar) return lastProfile.avatar;
    } catch {}
    return "/static/site/assets/bara/001.webp";
  }

  function upgradeHtml(quota) {
    const slides = planSlides();
    const used = Math.max(quota.used, quota.limit || 0);
    return `
      <p class="card-label">Hlídací pes</p>
      <h2 class="sv-title"><span class="sv-bara"><img src="${escapeHtml(profileAvatarUrl())}" width="56" height="56" alt="" /></span>Na dalšího hlídacího psa je Bára krátká</h2>
      <p class="sv-copy">${quota.limit == null ? `Limit hlídacích psů v tarifu ${escapeHtml(quota.label)} je naplněný.` : `<strong>Už máte využito ${used}/${quota.limit} hlídacích psů</strong> v tarifu ${escapeHtml(quota.label)}.`} Upgradujte a hlídejte tolik hledání, kolik chcete.</p>
      <div class="sv-slider" data-sv-slider>
        ${slides.length > 1 ? `<div class="sv-tabs" role="tablist">${slides.map((sl, i) => `<button type="button" role="tab" class="sv-tab${i === 0 ? " on" : ""}" data-sv-go="${i}">${escapeHtml(sl.tab)}</button>`).join("")}</div>` : ""}
        <div class="sv-track">
          ${slides
            .map(
              (sl) => `
          <div class="sv-slide" data-plan="${sl.id}">
            <article class="upsell-card${sl.id === "start" ? " is-start" : ""}">
              <div class="upsell-main">
                <p class="upsell-kicker">${escapeHtml(sl.kicker)}</p>
                <ul class="upsell-list">${sl.features.map((f) => `<li>${escapeHtml(f)}</li>`).join("")}</ul>
              </div>
              <div class="upsell-side">
                <p class="upsell-price"><strong>${formatKc(sl.price)}</strong> <span>/ měsíc</span></p>
                <p class="upsell-note">${escapeHtml(sl.note)}</p>
                <a class="upsell-cta" href="/nastaveni/predplatne">${escapeHtml(sl.cta)}</a>
              </div>
            </article>
          </div>`,
            )
            .join("")}
        </div>
        ${slides.length > 1 ? `<div class="sv-dots">${slides.map((_, i) => `<button type="button" class="sv-dot${i === 0 ? " on" : ""}" data-sv-go="${i}" aria-label="Tarif ${i + 1}"></button>`).join("")}</div>` : ""}
      </div>`;
  }

  function initUpgradeSlider(root) {
    const slider = root.querySelector("[data-sv-slider]");
    const track = slider?.querySelector(".sv-track");
    if (!track) return;
    const mark = () => {
      const index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      slider.querySelectorAll(".sv-tab, .sv-dot").forEach((el) => el.classList.toggle("on", Number(el.dataset.svGo) === index));
    };
    track.addEventListener("scroll", () => requestAnimationFrame(mark), { passive: true });
    slider.addEventListener("click", (event) => {
      const go = event.target.closest("[data-sv-go]");
      if (!go) return;
      track.scrollTo({ left: Number(go.dataset.svGo) * track.clientWidth, behavior: "smooth" });
    });
  }

  function openSaveViewModal() {
    const modal = ensureSaveViewModal();
    const body = $("view-save-body");
    const quota = watchQuota();
    if (quota.reached) {
      body.innerHTML = upgradeHtml(quota);
      initUpgradeSlider(body);
    } else {
      const rows = watchCriteria();
      const left = quota.limit == null ? "" : `Zbývá vám ${quota.limit - quota.used} z ${quota.limit} aktivních hlídacích psů v tarifu ${quota.label}.`;
      body.innerHTML = `
        <p class="card-label">Nový hlídací pes</p>
        <h2 class="sv-title">${icon("paw")}Hlídat toto hledání</h2>
        <p class="sv-copy">Podle aktuálních filtrů vytvoříme hlídacího psa. Jakmile se objeví nová nabídka, hned vám dáme vědět.</p>
        <form id="view-save-form" class="sv-form">
          <label>Název hlídacího psa<input name="name" maxlength="60" value="${escapeHtml(defaultWatchName())}" required /></label>
          <dl class="sv-criteria">${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("")}</dl>
          ${left ? `<p class="sv-quota">${escapeHtml(left)}</p>` : ""}
          <p class="sv-error" id="view-save-error" hidden></p>
          <div class="actions sv-actions">
            <button class="btn" type="submit" data-icon="paw">Vytvořit hlídacího psa</button>
            <button class="btn btn-ghost" type="button" data-sv-close>Zrušit</button>
          </div>
        </form>`;
      $("view-save-form").addEventListener("submit", submitSaveView);
    }
    decorateIcons(body);
    modal.hidden = false;
    document.body.classList.add("catalog-modal-open");
    body.querySelector("input[name=name]")?.select();
  }

  function monitorFiltersFromCatalog() {
    const districts = new Set(selected.districts);
    for (const place of selectedPlaces) {
      const slug = slugForPlace(place.id);
      if (slug) districts.add(slug);
    }
    const num = (id) => {
      const v = $(id)?.value;
      return v ? Number(v) : null;
    };
    return {
      source: "sreality",
      offers: selected.offers.size ? [...selected.offers] : ["pronajem"],
      category: "byty",
      estate: [...selected.estates],
      sizes: [...selected.dispositions],
      districts: [...districts],
      price_from: num("cat-price-from"),
      price_to: num("cat-price-to"),
      area_from: num("cat-area-from"),
      area_to: num("cat-area-to"),
      ownership: [...selected.ownership],
      conditions: [...selected.conditions],
      buildings: [...selected.buildings],
      sort: "nejnovejsi",
    };
  }

  async function submitSaveView(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const btn = form.querySelector('button[type="submit"]');
    const err = $("view-save-error");
    err.hidden = true;
    btn.disabled = true;
    try {
      const built = await fetch("/api/filters/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filters: monitorFiltersFromCatalog(), portals: "all" }),
      }).then((r) => r.json());
      if (!built.url) throw new Error("Hledání se nepodařilo převést na hlídacího psa.");
      const res = await fetch("/api/monitors", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: form.elements.name.value.trim() || "Hlídací pes", search_url: built.url, template_id: "default", enabled: true, portals: "all" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const message = String(data.detail || "Hlídacího psa se nepodařilo vytvořit.");
        if (/limit/i.test(message)) {
          $("view-save-body").innerHTML = upgradeHtml(watchQuota(), message);
          initUpgradeSlider($("view-save-body"));
          decorateIcons($("view-save-body"));
          return;
        }
        throw new Error(message);
      }
      closeSaveViewModal();
      if (typeof toast === "function") toast(`Hlídací pes „${data.name || "nový"}“ je vytvořený`);
      if (typeof refresh === "function") refresh();
      renderViews();
    } catch (e) {
      err.textContent = e.message || "Hlídacího psa se nepodařilo vytvořit.";
      err.hidden = false;
    } finally {
      btn.disabled = false;
    }
  }

  $("catalog-views")?.addEventListener("click", (event) => {
    if (event.target.closest("#view-save")) {
      openSaveViewModal();
      return;
    }
    const watch = event.target.closest("[data-watch]");
    if (watch) {
      selected.monitor = selected.monitor === watch.dataset.watch ? "" : watch.dataset.watch;
      if ($("cat-monitor")) $("cat-monitor").value = selected.monitor;
      renderViews();
      loadCatalog();
      return;
    }
    const del = event.target.closest("[data-del-view]");
    if (del) {
      localStorage.setItem(
        "nabidka-views",
        JSON.stringify(savedViews().filter((view) => view.id !== del.dataset.delView)),
      );
      renderViews();
      return;
    }
    const chip = event.target.closest("[data-view]");
    if (!chip) return;
    const view = savedViews().find((item) => item.id === chip.dataset.view);
    if (!view) return;
    const params = new URLSearchParams(view.filters || {});
    history.replaceState(null, "", `/nabidka?${params}`);
    readUrlState();
    if (view.circle) circleFilter = view.circle;
    if (Array.isArray(view.places)) selectedPlaces = view.places;
    syncPlacesFromDistricts();
    syncCircleUi();
    loadCatalog();
  });
  $("compare-open")?.addEventListener("click", openCompare);
  $("compare-clear")?.addEventListener("click", () => {
    compareUrls = [];
    renderCompareTray();
    renderList(lastItems, false);
  });
  $("compare-close")?.addEventListener("click", closeCompare);
  $("compare-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "compare-modal") closeCompare();
  });
  $("compare-items")?.addEventListener("click", (event) => {
    const drop = event.target.closest("[data-drop-compare]");
    if (!drop) return;
    toggleCompare(drop.dataset.dropCompare);
  });
  if ($("catalog-more")) {
    new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting) && lastItems.length < total) loadCatalog(true);
      },
      { root: listEl.closest(".catalog-side") || null, rootMargin: "200px" },
    ).observe($("catalog-more"));
  }
  $("map-circle-btn")?.addEventListener("click", startCircleDraw);
  $("map-circle-clear")?.addEventListener("click", clearCircle);
  $("map-radius")?.addEventListener("input", (event) => {
    if (!circleFilter) return;
    circleFilter.radius_m = Number(event.target.value);
    $("map-radius-label").textContent = radiusLabel(circleFilter.radius_m);
    if (circleLayer) circleLayer.setRadius(circleFilter.radius_m);
    clearTimeout(radiusTimer);
    radiusTimer = setTimeout(() => {
      saveCircle();
      loadCatalog();
    }, 250);
  });

  window.addEventListener("catalog-show", () => {
    const query = location.search;
    if (!loaded || query !== lastCatalogQuery) {
      loaded = true;
      lastCatalogQuery = query;
      if (query) readUrlState();
      else restoreSavedFilterState();
      resolveQueryPlace().finally(() => loadCatalog());
    } else if (catalogMap) {
      setTimeout(() => catalogMap.invalidateSize(), 80);
    }
  });

  try {
    const saved = JSON.parse(localStorage.getItem("nabidka-circle") || "null");
    if (saved?.lat != null && saved?.lon != null && saved?.radius_m) {
      circleFilter = {
        lat: Number(saved.lat),
        lon: Number(saved.lon),
        radius_m: Number(saved.radius_m),
      };
    }
  } catch {
    circleFilter = null;
  }
  function restoreSavedFilterState() {
    try {
      const raw = localStorage.getItem(FILTER_STATE_KEY);
      if (raw === null) return false;
      restoredFilterState = true;
      readUrlState(new URLSearchParams(raw));
      return true;
    } catch {
      return false;
    }
  }

  if (location.search) readUrlState();
  else restoreSavedFilterState();
  const settingsReady = fetch("/api/settings")
    .then((res) => res.json())
    .then((data) => {
      appSettings = data || appSettings;
      const prefs = data?.watch_prefs;
      const skipPrefs = Boolean(location.search) || restoredFilterState || !prefs;
      if (skipPrefs) return;
      if (Array.isArray(prefs.sizes) && prefs.sizes.length) {
        selected.dispositions = new Set(prefs.sizes);
      }
      if (prefs.price_from != null && $("cat-price-from")) $("cat-price-from").value = String(prefs.price_from);
      if (prefs.price_to != null && $("cat-price-to")) $("cat-price-to").value = String(prefs.price_to);
      if (prefs.area_from != null && $("cat-area-from")) $("cat-area-from").value = String(prefs.area_from);
      const districts = [];
      for (const loc of prefs.localities || []) {
        const name = String(loc.label || "").split(",")[0].trim();
        const numbered = /^Praha\s+(\d+)$/i.exec(name);
        if (numbered) districts.push(`praha-${numbered[1]}`);
        else if (name) districts.push(name);
      }
      if (districts.length) {
        selected.districts = new Set(districts);
        syncPlacesFromDistricts();
      }
      syncFilterUi();
    })
    .catch(() => {});
  window.addEventListener("app-settings", (event) => {
    if (event.detail) appSettings = event.detail;
  });
  fetch("/api/monitors")
    .then((res) => res.json())
    .then((data) => {
      watchProfiles = data.items || [];
      renderViews();
    })
    .catch(() => {});
  syncCircleUi();
  renderFacets({});
  syncFilterUi();

  // Přepínač zobrazení: seznam + mapa / karty / mapa.
  (function initViewSwitch() {
    const root = $("view-catalog");
    const sw = $("catalog-view-switch");
    if (!root || !sw) return;
    let mode = "list";
    try { mode = localStorage.getItem("catalogView") || "list"; } catch (_) {}
    // Phones: no list view — cards are the default.
    if (window.matchMedia && window.matchMedia("(max-width: 900px)").matches && mode === "list") mode = "grid";
    const host = $("cat-inline-filters");
    const placeFilters = (inline) => {
      const scroll = document.querySelector(".filters-scroll");
      const card = document.querySelector(".filters-card");
      if (!scroll || !host || !card) return;
      if (inline) {
        if (scroll.parentElement !== host) {
          host.appendChild(scroll);
          if (!host.querySelector(".cat-inline-reset")) {
            const reset = document.createElement("button");
            reset.type = "button";
            reset.className = "btn btn-ghost cat-inline-reset";
            reset.textContent = "Vymazat filtry";
            reset.addEventListener("click", () => $("filters-reset")?.click());
            host.appendChild(reset);
          }
        }
        syncFilterUi();
        host.hidden = false;
      } else {
        host.hidden = true;
        if (scroll.parentElement !== card) card.insertBefore(scroll, card.querySelector(".filters-actions"));
      }
    };
    const applyInline = () => {
      readFilterUi();
      syncPlacesFromDistricts();
      updateFilterToggle();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadCatalog(), 250);
    };
    host?.addEventListener("click", (event) => {
      const button = event.target.closest(".chip");
      if (!button || !host.contains(button)) return;
      const tri = event.target.closest("[data-tri]");
      if (tri && tri.contains(button)) {
        const on = button.classList.contains("on");
        tri.querySelectorAll(".chip").forEach((chip) => chip.classList.remove("on"));
        if (!on) button.classList.add("on");
      } else {
        const group = event.target.closest("[data-filter]");
        if (group && group.contains(button)) button.classList.toggle("on");
      }
      // chipy s vlastním handlerem (portály) už načetly samy; ostatní načteme zde
      if (!button.closest("#cat-portals")) applyInline();
    });
    host?.addEventListener("change", (event) => {
      if (event.target.closest("select")) applyInline();
    });
    const apply = (next) => {
      mode = ["list", "grid", "map"].includes(next) ? next : "list";
      placeFilters(mode === "map");
      root.classList.remove("cv-list", "cv-grid", "cv-map");
      root.classList.add(`cv-${mode}`);
      sw.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.cv === mode));
      try { localStorage.setItem("catalogView", mode); } catch (_) {}
      [60, 250, 600].forEach((ms) => setTimeout(() => catalogMap?.invalidateSize(), ms));
    };
    sw.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-cv]");
      if (btn) apply(btn.dataset.cv);
    });
    apply(mode);
  })();

  // Bridge for the assistant (Bára): reads what the user sees and drives the catalog UI.
  const UI_FILTER_KEYS = ["q", "portal", "disposition", "price_from", "price_to", "area_from", "area_to", "offer", "estate", "district", "amenities", "sort", "status", "discounted", "ownership", "condition", "building", "equipped", "roommate", "pets", "short_term", "monitor_id", "hits", "lat", "lon", "radius_m"];
  window.RealitifyUI = {
    state() {
      const onCatalog = location.pathname.replace(/\/$/, "") === "/nabidka";
      const out = { page: location.pathname };
      if (!onCatalog) return out;
      const active = {};
      for (const [key, value] of filterStateParams().entries()) {
        if (UI_FILTER_KEYS.includes(key)) active[key] = value;
      }
      out.filters = active;
      out.total = total;
      out.compare = compareUrls.map((url) => {
        const item = itemByUrl(url);
        return item ? `${item.name || item.locality || ""} (${url})` : url;
      });
      out.visible = lastItems.slice(0, 5).map((item) => `${item.name || ""}, ${item.locality || ""}, ${item.price_label || item.price_czk || ""} (${item.url})`);
      const modal = $("catalog-modal");
      if (modal && !modal.hidden) out.detail = (modal.querySelector("h1,h2,h3")?.textContent || "").trim();
      return out;
    },
    async applyFilters(params, keepCurrent) {
      if (location.pathname.replace(/\/$/, "") !== "/nabidka") return false;
      const next = keepCurrent ? filterStateParams() : new URLSearchParams();
      for (const [key, value] of Object.entries(params || {})) {
        if (value !== "" && value != null) next.set(key, String(value));
      }
      if (!keepCurrent || !next.has("places")) next.delete("places");
      if (params && params.q) {
        next.delete("district");
        next.delete("places");
      }
      circleFilter = null;
      readUrlState(next);
      await resolveQueryPlace();
      await loadCatalog();
      return true;
    },
    compare(action, urls) {
      if (action === "clear") compareUrls = [];
      else if (action === "add") for (const url of urls || []) if (url && !compareUrls.includes(url) && compareUrls.length < 3) compareUrls.push(url);
      else if (action === "remove") compareUrls = compareUrls.filter((url) => !(urls || []).includes(url));
      renderCompareTray();
      renderList(lastItems, false);
      if (action === "open") openCompare();
    },
    ready() {
      return loaded && !catalogBusy;
    },
    setView(mode) {
      document.querySelector(`#catalog-views-switch [data-cv="${mode}"], [data-cv="${mode}"]`)?.click();
    },
    openListing(target) {
      return openDetail("", target.id || "", { listing_key: target.listing_key || "", url: target.url || "" });
    },
  };

  if (location.pathname.replace(/\/$/, "") === "/nabidka") {
    loaded = true;
    ensureMap();
    settingsReady.finally(() => resolveQueryPlace()).finally(() => {
      if (location.pathname.replace(/\/$/, "") === "/nabidka") loadCatalog();
    });
    // Deep-link from Chrome extension CTA
    const deep = new URLSearchParams(location.search);
    const deepKey = deep.get("listing_key") || "";
    const deepUrl = deep.get("url") || "";
    const deepId = deep.get("id") || "";
    if (deepKey || deepUrl || deepId) {
      const open = () => openDetail("", deepId, { listing_key: deepKey, url: deepUrl });
      // Wait a tick so modal DOM is ready
      setTimeout(open, 0);
    }
  }

  // Karty na přehledu (karusely) používají stejný vzhled i chování jako karty v nabídce.
  window.RealitifyCard = { html: cardHtml, open: openDetail };
  (function bindOverviewCards() {
    let down = null;
    document.addEventListener("pointerdown", (event) => {
      down = event.target.closest(".carousel-track") ? { x: event.clientX, y: event.clientY } : null;
    }, true);
    document.addEventListener("click", (event) => {
      const card = event.target.closest(".carousel-track .offer-card");
      if (!card) return;
      if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) return;
      const nav = event.target.closest(".offer-nav");
      const gallery = event.target.closest(".offer-photo");
      if (nav && gallery) {
        showGallery(gallery, Number(gallery.dataset.index || 0) + (nav.classList.contains("next") ? 1 : -1));
        return;
      }
      const save = event.target.closest("[data-save-url]");
      const hide = event.target.closest("[data-hide-url]");
      if (save || hide) {
        const btn = save || hide;
        const status = save ? "saved" : "hidden";
        setListingUser(btn.dataset.saveUrl || btn.dataset.hideUrl, btn.classList.contains("on") ? "" : status, "");
        btn.classList.toggle("on");
        return;
      }
      if (event.target.closest("[data-compare-url]")) return;
      const [monitorId, listingId] = String(card.dataset.openOffer || "").split(":");
      if (listingId) openDetail(monitorId, listingId, { url: card.dataset.url || "" });
    });
  })();
})();
