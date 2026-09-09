(() => {
  const $ = (id) => document.getElementById(id);
  const listEl = $("catalog-list");
  if (!listEl) return;

  const LIMIT = 36;
  let catalogMap = null;
  let catalogLayer = null;
  let markerByKey = new Map();
  let lastItems = [];
  let total = 0;
  let offset = 0;
  let loaded = false;
  let lightboxPhotos = [];
  let lightboxIndex = 0;
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
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function formatTime(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric" });
  }

  function portalIcon(portal) {
    return portal === "Bezrealitky" ? "/static/icons/bezrealitky.svg" : "/static/icons/sreality.svg";
  }

  function snippet(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function filters() {
    return {
      portal: selected.portal,
      q: $("cat-q").value.trim(),
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
      ownership: [...selected.ownership].join(","),
      condition: [...selected.conditions].join(","),
      building: [...selected.buildings].join(","),
      equipped: [...selected.equipped].join(","),
      roommate: selected.roommate,
      pets: selected.pets,
      short_term: selected.short_term,
      sort: selected.sort,
    };
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
      (selected.monitor ? 1 : 0) +
      (selected.sort && selected.sort !== "newest" ? 1 : 0)
    );
  }

  function updateFilterToggle() {
    const count = extraFilterCount();
    const button = $("cat-toggle");
    if (!button) return;
    button.textContent = count ? `Všechny filtry · ${count}` : "Všechny filtry";
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
    selected.monitor = [...monitor.options].some((option) => option.value === previous) ? previous : "";
    monitor.value = selected.monitor;
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
    return `
      <article class="offer-card" data-open-offer="${item.monitor_id}:${item.id}">
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
        </div>
        <div class="offer-body">
          <div class="offer-kicker-row">
            <p class="offer-kicker">${escapeHtml(offerKicker(item))}</p>
            ${isFresh(item) ? `<span class="offer-badge">Nový</span>` : ""}
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
          ${priceBlock(item)}
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

  function ensureMap() {
    if (catalogMap || typeof L === "undefined") return;
    catalogMap = L.map("catalog-map", { scrollWheelZoom: true }).setView([50.08, 14.44], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 19,
    }).addTo(catalogMap);
    catalogLayer = L.layerGroup().addTo(catalogMap);
  }

  function renderMap(items, append) {
    ensureMap();
    if (!catalogMap || !catalogLayer) return;
    if (!append) {
      catalogLayer.clearLayers();
      markerByKey.clear();
    }
    const points = [];
    for (const item of items.filter((row) => row.lat != null && row.lon != null)) {
      const key = `${item.monitor_id}:${item.id}`;
      if (markerByKey.has(key)) continue;
      const marker = L.circleMarker([item.lat, item.lon], {
        radius: 8,
        color: "#163300",
        weight: 2,
        fillColor: "#9fe870",
        fillOpacity: 0.95,
      });
      marker.bindPopup(
        `<strong>${escapeHtml(item.price_label || "")}</strong><br />${escapeHtml(item.locality || item.name)}`,
      );
      marker.on("click", () => openDetail(item.monitor_id, item.id));
      catalogLayer.addLayer(marker);
      markerByKey.set(key, marker);
      points.push([item.lat, item.lon]);
    }
    if (!append) {
      if (points.length === 1) catalogMap.setView(points[0], 14);
      else if (points.length > 1) catalogMap.fitBounds(points, { padding: [40, 40], maxZoom: 14 });
    }
    setTimeout(() => catalogMap.invalidateSize(), 80);
  }

  function historyChart(history) {
    if (!history?.length) return `<p class="empty">Zatím jen aktuální cena. Historie se doplní při dalších kontrolách.</p>`;
    const prices = history.map((row) => Number(row.price_czk)).filter((n) => Number.isFinite(n));
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const span = Math.max(max - min, 1);
    const w = 520;
    const h = 120;
    const points = history
      .map((row, index) => {
        const x = history.length === 1 ? w / 2 : (index / (history.length - 1)) * (w - 16) + 8;
        const y = h - 16 - ((Number(row.price_czk) - min) / span) * (h - 32);
        return `${x},${y}`;
      })
      .join(" ");
    const rows = history
      .map((row) => `<li><span>${escapeHtml(formatTime(row.seen_at))}</span><strong>${escapeHtml(row.price_label || "")}</strong></li>`)
      .join("");
    return `
      <svg class="price-chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline fill="none" stroke="#163300" stroke-width="3" points="${points}" />
      </svg>
      <ul class="price-log">${rows}</ul>
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

  function closeModal() {
    closeLightbox();
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
    const response = await fetch(`/api/catalog/item?monitor_id=${encodeURIComponent(monitorId)}&id=${listingId}`);
    const item = await response.json();
    if (!response.ok) {
      host.innerHTML = `<p class="empty">${escapeHtml(item.detail || "Detail se nepodařilo načíst")}</p>`;
      return;
    }
    const photos = item.photos?.length ? item.photos : item.image_url ? [item.image_url] : [];
    lightboxPhotos = photos;
    lightboxIndex = 0;
    const extras = item.extras || {};
    const specs = extras.specs || [];
    const flags = item.flags || extras.flags || [];
    const description = snippet(item.description);
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
          <p class="offer-kicker">${escapeHtml(item.portal)} · ${escapeHtml(item.monitor_name || "")}</p>
          <h2>${escapeHtml(item.name || item.locality)}</h2>
          <p>${escapeHtml(item.locality || "")}</p>
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
          <h3>Historie cen</h3>
          ${historyChart(item.price_history)}
        </div>
        <aside class="detail-price">
          <p>Cena</p>
          <strong>${escapeHtml(item.price_label || "")}</strong>
          <a class="btn" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">Otevřít na ${escapeHtml(item.portal)}</a>
          ${item.maps_url ? `<a class="btn btn-ghost" href="${escapeHtml(item.maps_url)}" target="_blank" rel="noreferrer">Google Maps</a>` : ""}
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
  }

  async function loadCatalog(append = false) {
    if (!append) offset = 0;
    const response = await fetch(`/api/catalog?${queryString({ limit: LIMIT, offset })}`);
    const data = await response.json();
    const items = data.items || [];
    total = data.total || 0;
    renderFacets(data.facets);
    if (append) lastItems = lastItems.concat(items);
    else lastItems = items;
    $("catalog-count").textContent = `${total} nemovitostí`;
    $("catalog-empty").hidden = lastItems.length > 0;
    renderList(items, append);
    renderMap(items, append);
    offset = lastItems.length;
    $("catalog-more").hidden = lastItems.length >= total;
    updateFilterToggle();
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
    const card = event.target.closest("[data-open-offer]");
    if (!card) return;
    const [monitorId, listingId] = card.dataset.openOffer.split(":");
    openDetail(monitorId, Number(listingId));
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
    const marker = card && markerByKey.get(card.dataset.openOffer);
    if (marker) marker.setStyle({ radius: 11, weight: 3 });
  });
  listEl.addEventListener("mouseout", (event) => {
    const card = event.target.closest("[data-open-offer]");
    const marker = card && markerByKey.get(card.dataset.openOffer);
    if (marker) marker.setStyle({ radius: 8, weight: 2 });
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
    if (event.key === "Escape" && modalOpen) closeModal();
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
  $("cat-search")?.addEventListener("click", () => loadCatalog());
  $("catalog-more")?.addEventListener("click", () => loadCatalog(true));
  $("cat-q")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadCatalog();
  });

  window.addEventListener("catalog-show", () => {
    if (!loaded) {
      loaded = true;
      loadCatalog();
    } else if (catalogMap) {
      setTimeout(() => catalogMap.invalidateSize(), 80);
    }
  });

  if (location.pathname.replace(/\/$/, "") === "/nabidka") {
    loaded = true;
    loadCatalog();
  }
})();
