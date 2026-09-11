(() => {
  const $ = (id) => document.getElementById(id);
  const listEl = $("catalog-list");
  if (!listEl) return;

  const LIMIT = 36;
  let catalogMap = null;
  let catalogLayer = null;
  let pinItems = [];
  let circleLayer = null;
  let placeLayer = null;
  let drawingCircle = false;
  let circleFilter = null;
  let selectedPlaces = [];
  let watchProfiles = [];
  let placeSuggestItems = [];
  let placeSuggestTimer = 0;
  let radiusTimer = null;
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
  let offset = 0;
  let loaded = false;
  let lightboxPhotos = [];
  let lightboxIndex = 0;
  let detailMap = null;
  const selected = {
    portal: "",
    dispositions: new Set(),
    amenities: new Set(),
    offers: new Set(),
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
      const key = item.listing_key || item.url || `${item.monitor_id}:${item.id}`;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
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

  function portalIcon(portal) {
    return portal === "Bezrealitky" ? "/static/icons/bezrealitky.svg" : "/static/icons/sreality.svg";
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

  function filters() {
    return {
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
      places: selectedPlaces.map((item) => item.id).join(","),
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
    };
  }

  function writeUrlState() {
    if (location.pathname.replace(/\/$/, "") !== "/nabidka") return;
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters())) {
      if (value) params.set(key, String(value));
    }
    const query = params.toString();
    history.replaceState(null, "", query ? `/nabidka?${query}` : "/nabidka");
  }

  function readUrlState() {
    const params = new URLSearchParams(location.search);
    selected.portal = params.get("portal") || "";
    selected.status = params.get("status") || "";
    selected.discounted = params.get("discounted") === "1";
    selected.hits = params.get("hits") || "";
    selected.monitor = params.get("monitor_id") || "";
    selected.sort = params.get("sort") || "newest";
    selected.dispositions = new Set((params.get("disposition") || "").split(",").filter(Boolean));
    selected.amenities = new Set((params.get("amenities") || "").split(",").filter(Boolean));
    selected.offers = new Set((params.get("offer") || "").split(",").filter(Boolean));
    selected.estates = new Set((params.get("estate") || "").split(",").filter(Boolean));
    selected.districts = new Set((params.get("district") || "").split(",").filter(Boolean));
    selected.ownership = new Set((params.get("ownership") || "").split(",").filter(Boolean));
    selected.conditions = new Set((params.get("condition") || "").split(",").filter(Boolean));
    selected.buildings = new Set((params.get("building") || "").split(",").filter(Boolean));
    selected.equipped = new Set((params.get("equipped") || "").split(",").filter(Boolean));
    selected.roommate = params.get("roommate") || "";
    selected.pets = params.get("pets") || "";
    selected.short_term = params.get("short_term") || "";
    if ($("cat-q")) $("cat-q").value = "";
    if ($("cat-price-from")) $("cat-price-from").value = params.get("price_from") || "";
    if ($("cat-price-to")) $("cat-price-to").value = params.get("price_to") || "";
    if ($("cat-area-from")) $("cat-area-from").value = params.get("area_from") || "";
    if ($("cat-area-to")) $("cat-area-to").value = params.get("area_to") || "";
    selectedPlaces = (params.get("places") || "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean)
      .map((id) => ({ id, label: id, kind: id.startsWith("W") ? "street" : "area" }));
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
      else kicker.textContent = "Uložené inzeráty z hlídaných hledání";
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
    const missing = selectedPlaces.filter((row) => !row.geojson);
    if (!missing.length) return;
    const response = await fetch(`/api/places/geometry?ids=${encodeURIComponent(missing.map((row) => row.id).join(","))}`);
    const data = await response.json().catch(() => ({ items: [] }));
    for (const item of data.items || []) {
      const row = selectedPlaces.find((place) => place.id === item.id);
      if (!row) continue;
      row.geojson = item.geojson;
      row.kind = item.kind || row.kind;
      if (item.label) row.label = item.label;
      row.lat = item.lat;
      row.lon = item.lon;
      row.buffer_m = item.buffer_m;
    }
    renderPlaceChips();
  }

  async function addPlace(item) {
    if (!item?.id || selectedPlaces.some((row) => row.id === item.id)) return;
    selectedPlaces.push({
      id: item.id,
      label: item.label || item.id,
      kind: item.kind || (String(item.id).startsWith("W") ? "street" : "area"),
    });
    const slug = slugForPlace(item.id);
    if (slug) selected.districts.add(slug);
    renderPlaceChips();
    syncFilterUi();
    hidePlaceSuggest();
    if ($("cat-q")) $("cat-q").value = "";
    loadCatalog();
  }

  function removePlace(id) {
    selectedPlaces = selectedPlaces.filter((row) => row.id !== id);
    const slug = slugForPlace(id);
    if (slug) selected.districts.delete(slug);
    renderPlaceChips();
    syncFilterUi();
    drawPlaceLayer(true);
    loadCatalog();
  }

  function hidePlaceSuggest() {
    const box = $("cat-place-suggest");
    if (!box) return;
    box.hidden = true;
    box.innerHTML = "";
  }

  async function searchPlaces(q) {
    const box = $("cat-place-suggest");
    if (!box) return;
    if (q.length < 2) {
      hidePlaceSuggest();
      return;
    }
    const response = await fetch(`/api/places/search?q=${encodeURIComponent(q)}`);
    const data = await response.json().catch(() => ({ items: [] }));
    placeSuggestItems = data.items || [];
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

  function renderViews() {
    const host = $("catalog-views");
    if (!host) return;
    const views = savedViews();
    const watches = watchProfiles
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
    return (
      selected.dispositions.size +
      selected.amenities.size +
      selected.offers.size +
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
    selected.dispositions = chipValues($("cat-dispositions"), "disposition");
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
    if ($("cat-monitor")) $("cat-monitor").value = "";
    if ($("cat-sort")) $("cat-sort").value = "newest";
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
    const host = $("cat-dispositions");
    const current = selected.dispositions;
    host.innerHTML = (facets?.dispositions || [])
      .map((item) => `<button type="button" class="chip${current.has(item) ? " on" : ""}" data-disposition="${escapeHtml(item)}">${escapeHtml(item)}</button>`)
      .join("");
    const monitor = $("cat-monitor");
    const previous = selected.monitor;
    monitor.innerHTML = ['<option value="">Všechny monitory</option>']
      .concat((facets?.monitors || []).map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`))
      .join("");
    selected.monitor = previous;
    monitor.value = previous;
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

  function priceBlock(item) {
    const extras = item.extras || {};
    const main = item.price_czk != null ? formatKc(item.price_czk) : (item.price_label || "").replace(/\/měsíc.*/, "").replace(/\s*\(.*/, "").trim();
    let chargesN = extras.charges_czk;
    if (!chargesN) {
      const match = String(item.price_label || "").match(/\(\+\s*([\d\s]+)\s*Kč\)/);
      if (match) chargesN = Number(match[1].replace(/\s/g, ""));
    }
    const charges = chargesN ? `+ ${formatKc(chargesN)}` : "";
    const per = item.price_czk && item.area_m2 ? `(${formatKc(Math.round(item.price_czk / item.area_m2))} / m²)` : "";
    return `
      <p class="offer-price">
        <strong>${escapeHtml(main || item.price_label || "")}</strong>
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
      isFresh(item) ? `<span class="offer-badge">Nový</span>` : "",
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
          <img class="offer-portal" src="${portalIcon(item.portal)}" alt="${escapeHtml(item.portal)}" />
          <button type="button" class="offer-save${item.status === "saved" ? " on" : ""}" data-save-url="${escapeHtml(item.url || "")}" aria-label="Uložit" title="Uložit">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h12a1 1 0 0 1 1 1V21l-7-4.2L5 21V4.5a1 1 0 0 1 1-1z"/></svg>
          </button>
        </div>
        <div class="offer-body">
          <div class="offer-kicker-row">
            <p class="offer-kicker">${escapeHtml(offerKicker(item))}${monitors.length ? ` · ${escapeHtml(monitors.join(" · "))}` : ""}</p>
            ${badges}
          </div>
          <h3>${escapeHtml(item.locality || item.name)}</h3>
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
          ${item.twin ? `<p class="offer-twin">Podobné na ${escapeHtml(item.twin.portal)}</p>` : ""}
          <div class="offer-foot">
            ${priceBlock(item)}
            <div class="offer-tools">
              <button type="button" class="offer-tool${item.status === "saved" ? " on" : ""}" data-save-url="${escapeHtml(item.url || "")}">${icon("bookmark")}<span>Uložit</span></button>
              <button type="button" class="offer-tool" data-hide-url="${escapeHtml(item.url || "")}">${icon("eyeOff")}<span>Skrýt</span></button>
              <button type="button" class="offer-tool${compareUrls.includes(item.url) ? " on" : ""}" data-compare-url="${escapeHtml(item.url || "")}">${icon("columns")}<span>Porovnat</span></button>
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

  function renderList(items, append) {
    const html = items.map(cardHtml).join("");
    if (append) listEl.insertAdjacentHTML("beforeend", html);
    else listEl.innerHTML = html;
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

  function drawPlaceLayer(fit) {
    ensureMap();
    if (!catalogMap) return;
    if (!placeLayer) placeLayer = L.layerGroup().addTo(catalogMap);
    placeLayer.clearLayers();
    for (const place of selectedPlaces) {
      if (!place.geojson) continue;
      const street = place.kind === "street" || /LineString/i.test(place.geojson.type || "");
      if (street) {
        L.geoJSON(place.geojson, {
          style: {
            color: "#4a90c4",
            weight: 78,
            opacity: 0.28,
            lineCap: "round",
            lineJoin: "round",
          },
        }).addTo(placeLayer);
        L.geoJSON(place.geojson, {
          style: { color: "#2b6a96", weight: 3, opacity: 0.9 },
        }).addTo(placeLayer);
      } else {
        L.geoJSON(place.geojson, {
          style: {
            color: "#3d7ea6",
            weight: 2,
            fillColor: "#5ba3d0",
            fillOpacity: 0.22,
          },
        }).addTo(placeLayer);
      }
    }
    const streetOnly =
      selectedPlaces.length > 0 &&
      selectedPlaces.every((row) => row.kind === "street" || /LineString/i.test(row.geojson?.type || ""));
    if (fit && placeLayer.getLayers().length) {
      catalogMap.fitBounds(placeLayer.getBounds(), { padding: [28, 28], maxZoom: streetOnly ? 16 : 13 });
    }
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
    catalogMap.fitBounds(circleLayer.getBounds(), { padding: [28, 28], maxZoom: 15 });
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
    loadCatalog();
  }

  function startCircleDraw() {
    ensureMap();
    drawingCircle = true;
    if (catalogMap) catalogMap.getContainer().style.cursor = "crosshair";
    syncCircleUi();
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

  function priceMarker(item) {
    const pin = L.divIcon({
      className: "price-pin-wrap",
      html: `<div class="price-pin">${escapeHtml(pinPrice(item))}</div>`,
      iconSize: [88, 32],
      iconAnchor: [44, 16],
      popupAnchor: [0, -18],
    });
    const marker = L.marker([item.lat, item.lon], { icon: pin, riseOnHover: true });
    marker.bindPopup(`<strong>${escapeHtml(pinPrice(item))}</strong><br />${escapeHtml(item.locality || item.name)}`);
    marker.on("click", () => {
      if (drawingCircle) {
        placeCircle(item.lat, item.lon);
        return;
      }
      openDetail(item.monitor_id, item.id);
    });
    marker.on("mouseover", () => highlightCard(listingKey(item)));
    marker.on("mouseout", () => clearCardHighlight());
    return marker;
  }

  function ensureMap() {
    if (catalogMap || typeof L === "undefined") return;
    catalogMap = L.map("catalog-map", { scrollWheelZoom: true, attributionControl: false }).setView([50.08, 14.44], 12);
    L.maplibreGL({
      style: "https://tiles.openfreemap.org/styles/liberty",
    }).addTo(catalogMap);
    placeLayer = L.layerGroup().addTo(catalogMap);
    catalogLayer = L.layerGroup().addTo(catalogMap);
    hoverLayer = L.layerGroup().addTo(catalogMap);
    catalogMap.on("click", (event) => {
      if (!drawingCircle) return;
      placeCircle(event.latlng.lat, event.latlng.lng);
    });
    catalogMap.on("zoomend", () => drawPinLayer(false));
  }

  function listingKey(item) {
    return `${item.monitor_id}:${item.id}`;
  }

  function highlightCard(key) {
    listEl.querySelectorAll(".offer-card.is-hot").forEach((el) => el.classList.remove("is-hot"));
    const card = listEl.querySelector(`[data-open-offer="${CSS.escape(String(key))}"]`);
    if (!card) return;
    card.classList.add("is-hot");
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function clearCardHighlight() {
    listEl.querySelectorAll(".offer-card.is-hot").forEach((el) => el.classList.remove("is-hot"));
  }

  function spiderfyGroup(group) {
    if (!hoverLayer || !catalogMap) return;
    hoverLayer.clearLayers();
    const count = group.items.length;
    const radius = 0.00032 * Math.max(3, Math.sqrt(count));
    group.items.forEach((item, index) => {
      const angle = (2 * Math.PI * index) / count - Math.PI / 2;
      const marker = L.marker([group.lat + radius * Math.cos(angle), group.lon + radius * Math.sin(angle)], {
        icon: L.divIcon({
          className: "price-pin-wrap",
          html: `<div class="price-pin">${escapeHtml(pinPrice(item))}</div>`,
          iconSize: [88, 32],
          iconAnchor: [44, 16],
        }),
        zIndexOffset: 1800,
      });
      marker.on("click", () => openDetail(item.monitor_id, item.id));
      marker.on("mouseover", () => highlightCard(listingKey(item)));
      marker.on("mouseout", () => clearCardHighlight());
      hoverLayer.addLayer(marker);
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

  function drawPinLayer(fit) {
    if (!catalogMap || !catalogLayer) return;
    const keepHover = hoverKey;
    const items = pinItems.filter((row) => row.lat != null && row.lon != null);
    catalogLayer.clearLayers();
    markerByKey.clear();
    clusterByKey.clear();
    hoverLayer?.clearLayers();
    hoverMarker = null;
    const groups = groupedPins(items, catalogMap.getZoom());
    const points = [];
    for (const group of groups) {
      if (group.items.length === 1) {
        const item = group.items[0];
        const marker = priceMarker(item);
        catalogLayer.addLayer(marker);
        markerByKey.set(listingKey(item), marker);
      } else {
        const size = group.items.length > 99 ? 48 : group.items.length > 9 ? 42 : 36;
        const marker = L.marker([group.lat, group.lon], {
          icon: L.divIcon({
            className: "price-cluster-wrap",
            html: `<div class="price-cluster">${group.items.length}</div>`,
            iconSize: [size, size],
            iconAnchor: [size / 2, size / 2],
          }),
          zIndexOffset: 200,
        });
        marker.on("click", () => {
          if (drawingCircle) {
            placeCircle(group.lat, group.lon);
            return;
          }
          const nextZoom = Math.min(catalogMap.getZoom() + 2, 16);
          if (catalogMap.getZoom() >= 15 || nextZoom >= 16) spiderfyGroup(group);
          else catalogMap.setView([group.lat, group.lon], nextZoom);
        });
        marker.on("mouseover", () => {
          const first = group.items.find((item) => listEl.querySelector(`[data-open-offer="${listingKey(item)}"]`));
          if (first) highlightCard(listingKey(first));
        });
        marker.on("mouseout", () => clearCardHighlight());
        catalogLayer.addLayer(marker);
        for (const item of group.items) clusterByKey.set(listingKey(item), { marker, item });
      }
      points.push([group.lat, group.lon]);
    }
    if (fit) {
      if (selectedPlaces.length) drawPlaceLayer(true);
      else if (circleFilter) drawCircleLayer();
      else if (points.length === 1) catalogMap.setView(points[0], 14);
      else if (points.length > 1) catalogMap.fitBounds(points, { padding: [40, 40], maxZoom: 14 });
    } else {
      drawPlaceLayer(false);
    }
    if (keepHover) highlightListing(keepHover);
    setTimeout(() => catalogMap.invalidateSize(), 80);
  }

  function renderMapPins(items) {
    pinItems = items || [];
    ensureMap();
    if (!catalogMap || !catalogLayer) return;
    drawPinLayer(true);
  }

  async function loadPins() {
    const response = await fetch(`/api/catalog/pins?${queryString()}`);
    const data = await response.json();
    renderMapPins(uniqueOffers(data.items || []));
  }

  function historyChart(history) {
    if (!history?.length) return `<p class="empty">Cena se začne ukládat při další kontrole monitoru.</p>`;
    const prices = history.map((row) => Number(row.price_czk)).filter((n) => Number.isFinite(n));
    if (!prices.length) return `<p class="empty">U této nabídky zatím není číselná cena.</p>`;
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const span = Math.max(max - min, 1);
    const w = 520;
    const h = 120;
    const coords = history.map((row, index) => {
      const x = history.length === 1 ? w / 2 : (index / (history.length - 1)) * (w - 24) + 12;
      const y = h - 18 - ((Number(row.price_czk) - min) / span) * (h - 36);
      return { x, y, row };
    });
    const line = coords.map((pt) => `${pt.x},${pt.y}`).join(" ");
    const dots = coords.map((pt) => `<circle cx="${pt.x}" cy="${pt.y}" r="4" fill="#163300" />`).join("");
    const unique = new Set(prices);
    const rows = [];
    history.forEach((row, index) => {
      const prev = index ? Number(history[index - 1].price_czk) : null;
      const price = Number(row.price_czk);
      const changed = prev != null && Number.isFinite(prev) && Number.isFinite(price) && prev !== price;
      if (!changed && index !== 0 && index !== history.length - 1) return;
      const delta = changed ? `<em class="${price < prev ? "down" : "up"}">${escapeHtml(formatKcDelta(price - prev))}</em>` : "";
      rows.push(
        `<li><span>${escapeHtml(formatTime(row.seen_at))}</span><strong>${escapeHtml(row.price_label || formatKc(price))}${delta}</strong></li>`
      );
    });
    const note =
      unique.size === 1
        ? `<p class="price-note">Zatím bez změny · ${escapeHtml(history[0].price_label || formatKc(prices[0]))}</p>`
        : `<p class="price-note">Nejnižší ${escapeHtml(formatKc(min))} · nejvyšší ${escapeHtml(formatKc(max))}</p>`;
    return `
      <svg class="price-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline fill="none" stroke="#163300" stroke-width="3" points="${line}" />
        ${dots}
      </svg>
      ${note}
      <ul class="price-log">${rows.join("")}</ul>
    `;
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
    L.maplibreGL({
      style: "https://tiles.openfreemap.org/styles/liberty",
    }).addTo(detailMap);
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

  async function openDetail(monitorId, listingId) {
    const modal = $("catalog-modal");
    const host = $("catalog-detail");
    catalogMap?.closePopup();
    modal.hidden = false;
    document.body.classList.add("catalog-modal-open");
    host.innerHTML = `<p class="empty">Načítám detail…</p>`;
    let item;
    try {
      const response = await fetch(`/api/catalog/item?monitor_id=${encodeURIComponent(monitorId)}&id=${listingId}`);
      item = await response.json().catch(() => ({}));
      if (!response.ok) {
        host.innerHTML = `<p class="empty">${escapeHtml(item.detail || "Detail se nepodařilo načíst")}</p>`;
        return;
      }
    } catch {
      host.innerHTML = `<p class="empty">Detail se nepodařilo načíst</p>`;
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
        ${photos[0] ? `<img id="detail-photo" src="${escapeHtml(photos[0])}" alt="" />` : ""}
        ${
          photos.length > 1
            ? `<button class="gallery-nav prev" type="button">‹</button><button class="gallery-nav next" type="button">›</button>`
            : ""
        }
        ${photos[0] ? `<p class="gallery-count">1 / ${photos.length}</p><p class="gallery-zoom">Celá obrazovka</p>` : ""}
      </div>
      <div class="detail-grid">
        <div class="detail-copy">
          <p class="offer-kicker">${escapeHtml(item.portal)} · ${escapeHtml((item.monitors || []).map((row) => row.name).join(" · ") || item.monitor_name || "")}</p>
          <h2>${escapeHtml(item.name || item.locality)}</h2>
          <p>${escapeHtml(item.locality || "")}</p>
          ${item.gone ? `<p class="offer-badge offer-badge-gone">Prodáno</p>` : ""}
          ${item.twin ? `<p><a href="${escapeHtml(item.twin.url)}" target="_blank" rel="noreferrer">Podobné na ${escapeHtml(item.twin.portal)}</a></p>` : ""}
          <div class="detail-specs">
            <span>${escapeHtml(item.disposition || "—")}</span>
            <span>${item.area_m2 ? `${item.area_m2} m²` : "rozloha neuvedena"}</span>
            ${extras.offer ? `<span>${escapeHtml(extras.offer)}</span>` : ""}
            ${extras.estate ? `<span>${escapeHtml(extras.estate)}</span>` : ""}
            ${item.views != null ? `<span>${item.views} zobrazení</span>` : ""}
            ${flags.map((flag) => `<span>${escapeHtml(flagLabel(flag))}</span>`).join("")}
          </div>
          ${
            specs.length
              ? `<div class="detail-rows">${specs
                  .map((row) => `<div class="row"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></div>`)
                  .join("")}</div>`
              : ""
          }
          ${description ? `<p class="detail-desc">${escapeHtml(item.description)}</p>` : `<p class="empty">Portál u této nabídky zatím neposlal popis.</p>`}
          <section class="detail-note">
            <div class="detail-note-head">
              <p class="detail-note-title">Poznámka</p>
              <p class="detail-note-hint" id="detail-note-hint">${item.note ? "Uloženo" : "Jen u tebe"}</p>
            </div>
            <textarea id="detail-note" rows="4" placeholder="Termín prohlídky, dojem, co ještě ověřit…">${escapeHtml(item.note || "")}</textarea>
          </section>
          <h3>Historie cen</h3>
          ${historyChart(item.price_history)}
        </div>
        <aside class="detail-side">
          <div class="detail-price">
            <p>Cena</p>
            <strong>${escapeHtml(item.price_label || "")}</strong>
            ${item.discount_czk ? `<p>Sleva ${escapeHtml(formatKc(item.discount_czk))}${item.discount_pct ? ` · ${item.discount_pct} %` : ""}</p>` : ""}
            <a class="btn" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${icon("external")}<span>Otevřít na ${escapeHtml(item.portal)}</span></a>
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
    mountDetailMap(item);
    $("detail-map-full")?.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleDetailMapFullscreen();
    });
    $("detail-note")?.addEventListener("change", () => {
      if (item.url) setListingUser(item.url, item.status || "", $("detail-note").value);
    });
    let noteTimer;
    $("detail-note")?.addEventListener("input", () => {
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
        return `<button type="button" class="chip" data-drop-compare="${escapeHtml(url)}">${escapeHtml(item?.locality || item?.name || "Položka")} ×</button>`;
      })
      .join("");
  }

  async function openCompare() {
    const modal = $("compare-modal");
    const grid = $("compare-grid");
    if (!modal || !grid || !compareUrls.length) return;
    const items = [];
    for (const url of compareUrls) {
      const local = itemByUrl(url);
      if (!local) continue;
      const response = await fetch(`/api/catalog/item?monitor_id=${encodeURIComponent(local.monitor_id)}&id=${local.id}`);
      items.push(response.ok ? await response.json() : local);
    }
    grid.innerHTML = items
      .map((item) => {
        const per = item.price_czk && item.area_m2 ? `${formatKc(Math.round(item.price_czk / item.area_m2))} / m²` : "—";
        return `
        <article class="compare-col">
          ${item.image_url || item.photos?.[0] ? `<img src="${escapeHtml(item.photos?.[0] || item.image_url)}" alt="" />` : ""}
          <h3>${escapeHtml(item.locality || item.name || "")}</h3>
          <p>${escapeHtml(item.price_label || "")}</p>
          <p>${escapeHtml(per)}</p>
          <p>${escapeHtml(item.disposition || "—")} · ${item.area_m2 ? `${item.area_m2} m²` : "—"}</p>
          <p>${item.discount_czk ? `Sleva ${escapeHtml(formatKc(item.discount_czk))}` : "Bez slevy"}</p>
          <p>${escapeHtml((item.monitors || []).map((row) => row.name).join(" · ") || item.monitor_name || "")}</p>
          <a href="${escapeHtml(item.url || "")}" target="_blank" rel="noreferrer">Otevřít</a>
        </article>`;
      })
      .join("");
    modal.hidden = false;
    document.body.classList.add("catalog-modal-open");
  }

  function closeCompare() {
    $("compare-modal").hidden = true;
    document.body.classList.remove("catalog-modal-open");
  }

  async function loadCatalog(append = false) {
    if (!append) offset = 0;
    const geoTask = ensurePlaceGeoms();
    const response = await fetch(`/api/catalog?${queryString({ limit: LIMIT, offset })}`);
    const data = await response.json();
    await geoTask.catch(() => {});
    const items = uniqueOffers(data.items || []);
    total = data.total || 0;
    renderFacets(data.facets);
    if (append) lastItems = uniqueOffers(lastItems.concat(items));
    else lastItems = items;
    $("catalog-count").textContent = `${total} nemovitostí`;
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
      drawPlaceLayer(Boolean(selectedPlaces.length));
      loadPins();
    }
    offset = lastItems.length;
    $("catalog-more").hidden = lastItems.length >= total;
    updateFilterToggle();
    writeUrlState();
    renderViews();
    renderCompareTray();
    syncStatusChips();
  }

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
    openDetail(monitorId, Number(listingId));
  });

  let galleryStart = null;
  listEl.addEventListener("pointerdown", (event) => {
    const gallery = event.target.closest(".offer-photo");
    if (!gallery || event.target.closest(".offer-nav") || event.target.closest(".offer-save")) return;
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
  $("cat-dispositions")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-disposition]");
    if (!button) return;
    button.classList.toggle("on");
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
  $("filters-reset")?.addEventListener("click", clearFilterUi);
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
        openDetail(item.monitor_id, item.id);
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
  $("cat-search")?.addEventListener("click", async () => {
    const q = $("cat-q")?.value.trim() || "";
    if (q.length >= 2 && !selectedPlaces.length) {
      await searchPlaces(q);
      if (placeSuggestItems[0]) {
        await addPlace(placeSuggestItems[0]);
        return;
      }
    }
    loadCatalog();
  });
  $("catalog-more")?.addEventListener("click", () => loadCatalog(true));
  ["cat-price-from", "cat-price-to", "cat-area-from", "cat-area-to"].forEach((id) => {
    $(id)?.addEventListener("input", scheduleSearch);
  });
  $("cat-q")?.addEventListener("input", () => {
    clearTimeout(placeSuggestTimer);
    const q = $("cat-q").value.trim();
    placeSuggestTimer = window.setTimeout(() => searchPlaces(q), 220);
  });
  $("cat-q")?.addEventListener("keydown", async (event) => {
    if (event.key === "Escape") hidePlaceSuggest();
    if (event.key === "Enter") {
      event.preventDefault();
      const q = $("cat-q").value.trim();
      if (!placeSuggestItems.length && q.length >= 2) await searchPlaces(q);
      if (placeSuggestItems[0]) await addPlace(placeSuggestItems[0]);
    }
  });
  $("cat-place-suggest")?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-place-suggest]");
    if (!btn) return;
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
  $("catalog-views")?.addEventListener("click", (event) => {
    if (event.target.id === "view-save") {
      const name = prompt("Název pohledu");
      if (!name) return;
      const views = savedViews();
      views.push({ id: String(Date.now()), name: name.trim(), filters: filters(), circle: circleFilter, places: selectedPlaces });
      localStorage.setItem("nabidka-views", JSON.stringify(views));
      renderViews();
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
      readUrlState();
      loadCatalog();
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
  if (location.search) readUrlState();
  fetch("/api/settings")
    .then((res) => res.json())
    .then((data) => {
      appSettings = data || appSettings;
      const prefs = data?.watch_prefs;
      if (location.search || !prefs) return;
      if (Array.isArray(prefs.sizes) && prefs.sizes.length) {
        selected.dispositions = new Set(prefs.sizes);
      }
      if (prefs.offer) selected.offers = new Set([prefs.offer]);
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
      if (location.pathname.replace(/\/$/, "") === "/nabidka") loadCatalog();
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
  renderViews();

  if (location.pathname.replace(/\/$/, "") === "/nabidka") {
    loaded = true;
    loadCatalog();
  }
})();
