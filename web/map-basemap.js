(() => {
  const STORAGE_KEY = "rf_map_style";
  const EVENT = "rf-map-style";
  const CARTO_VOYAGER = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png";
  const CARTO_POSITRON = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
  const STYLES = [
    {
      id: "liberty",
      label: "Liberty",
      kind: "gl",
      style: "https://tiles.openfreemap.org/styles/liberty",
      raster: CARTO_VOYAGER,
    },
    {
      id: "positron",
      label: "Positron",
      kind: "gl",
      style: "https://tiles.openfreemap.org/styles/positron",
      raster: CARTO_POSITRON,
    },
    {
      id: "voyager",
      label: "Carto Voyager",
      kind: "raster",
      raster: CARTO_VOYAGER,
    },
    {
      id: "carto-positron",
      label: "Carto Positron",
      kind: "raster",
      raster: CARTO_POSITRON,
    },
  ];
  const byId = Object.fromEntries(STYLES.map((row) => [row.id, row]));
  const maps = new Set();
  const layerOf = new WeakMap();
  const styleOf = new WeakMap();
  const menuOf = new WeakMap();

  const canUseGl = () =>
    typeof L !== "undefined" && typeof L.maplibreGL === "function" && typeof maplibregl !== "undefined";

  const currentId = () => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored && byId[stored]) return stored;
    } catch {
      /* private mode */
    }
    return "liberty";
  };

  const persist = (id) => {
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* ignore */
    }
  };

  function ensureBasemapPane(map) {
    if (map.getPane("basemapPane")) return;
    map.createPane("basemapPane");
    const pane = map.getPane("basemapPane");
    pane.style.zIndex = 200;
    pane.style.pointerEvents = "none";
  }

  function createLayer(map, style) {
    ensureBasemapPane(map);
    if (style.kind === "gl" && canUseGl()) {
      try {
        return L.maplibreGL({
          style: style.style,
          interactive: false,
          pane: "basemapPane",
          attributionControl: false,
        });
      } catch {
        /* raster fallback */
      }
    }
    return L.tileLayer(style.raster, {
      maxZoom: 20,
      subdomains: "abcd",
      pane: "basemapPane",
      attribution: "&copy; OpenStreetMap &copy; CARTO",
    });
  }

  function refresh(map) {
    map.invalidateSize({ animate: false });
    const layer = layerOf.get(map);
    const gl = layer?.getMaplibreMap?.();
    gl?.resize?.();
  }

  function apply(map, id, { broadcast = false } = {}) {
    const style = byId[id] || byId.liberty;
    if (styleOf.get(map) === style.id && layerOf.get(map) && map.hasLayer(layerOf.get(map))) {
      markMenus(style.id);
      return;
    }
    const prev = layerOf.get(map);
    if (prev) map.removeLayer(prev);
    const layer = createLayer(map, style);
    layer.addTo(map);
    const gl = layer.getMaplibreMap?.();
    gl?.once?.("load", () => refresh(map));
    gl?.on?.("error", () => {
      if (styleOf.get(map) !== style.id) return;
      if (!map.hasLayer(layer)) return;
      map.removeLayer(layer);
      const raster = L.tileLayer(style.raster, {
        maxZoom: 20,
        subdomains: "abcd",
        pane: "basemapPane",
        attribution: "&copy; OpenStreetMap &copy; CARTO",
      });
      raster.addTo(map);
      layerOf.set(map, raster);
    });
    layerOf.set(map, layer);
    styleOf.set(map, style.id);
    markMenus(style.id);
    requestAnimationFrame(() => refresh(map));
    if (broadcast) {
      persist(style.id);
      window.dispatchEvent(new CustomEvent(EVENT, { detail: style.id }));
    }
  }

  function markMenus(id) {
    for (const map of maps) {
      const menu = menuOf.get(map);
      if (!menu) continue;
      menu.querySelectorAll("[data-style]").forEach((btn) => {
        btn.classList.toggle("is-on", btn.dataset.style === id);
      });
    }
  }

  function closeMenus(except) {
    document.querySelectorAll(".map-basemap.is-open").forEach((el) => {
      if (el !== except) {
        el.classList.remove("is-open");
        el.querySelector(".map-basemap-btn")?.setAttribute("aria-expanded", "false");
      }
    });
  }

  function addControl(map) {
    const Control = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const wrap = L.DomUtil.create("div", "leaflet-control map-basemap");
        wrap.innerHTML = `
          <button class="map-basemap-btn" type="button" aria-expanded="false" aria-label="Podklad mapy" title="Podklad mapy">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7.2 12 4l8 3.2-8 3.2z"/><path d="M4 12.2 12 9l8 3.2-8 3.2z"/><path d="M4 17.2 12 14l8 3.2-8 3.2z"/></svg>
          </button>
          <div class="map-basemap-menu" role="menu">
            ${STYLES.map((row) => `<button type="button" role="menuitem" data-style="${row.id}">${row.label}</button>`).join("")}
          </div>`;
        const btn = wrap.querySelector(".map-basemap-btn");
        const menu = wrap.querySelector(".map-basemap-menu");
        menuOf.set(map, menu);
        L.DomEvent.disableClickPropagation(wrap);
        L.DomEvent.disableScrollPropagation(wrap);
        btn.addEventListener("click", (event) => {
          event.preventDefault();
          const open = !wrap.classList.contains("is-open");
          closeMenus();
          wrap.classList.toggle("is-open", open);
          btn.setAttribute("aria-expanded", open ? "true" : "false");
        });
        menu.addEventListener("click", (event) => {
          const target = event.target.closest("[data-style]");
          if (!target) return;
          apply(map, target.dataset.style, { broadcast: true });
          wrap.classList.remove("is-open");
          btn.setAttribute("aria-expanded", "false");
        });
        return wrap;
      },
    });
    map.addControl(new Control());
  }

  function addTo(map) {
    if (!map || typeof L === "undefined") return;
    if (!maps.has(map)) {
      maps.add(map);
      addControl(map);
      if (typeof ResizeObserver === "function") {
        const ro = new ResizeObserver(() => refresh(map));
        ro.observe(map.getContainer());
      }
      map.on("unload", () => {
        maps.delete(map);
      });
    }
    apply(map, currentId());
    requestAnimationFrame(() => refresh(map));
  }

  window.addEventListener(EVENT, (event) => {
    const id = event.detail;
    for (const map of maps) apply(map, id);
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY || !event.newValue || !byId[event.newValue]) return;
    for (const map of maps) apply(map, event.newValue);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".map-basemap")) closeMenus();
  });

  window.RFMapBasemap = { addTo, styles: STYLES };
})();
