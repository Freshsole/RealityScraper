(() => {
  const ICONS = {
    home: `<path d="M4 11.2 12 4l8 7.2V20a1 1 0 0 1-1 1h-5.2v-6.2H10.2V21H5a1 1 0 0 1-1-1z"/>`,
    listings: `<rect x="4" y="4" width="7" height="7" rx="1.4"/><rect x="13" y="4" width="7" height="7" rx="1.4"/><rect x="4" y="13" width="7" height="7" rx="1.4"/><rect x="13" y="13" width="7" height="7" rx="1.4"/>`,
    radar: `<circle cx="12" cy="12" r="2"/><path d="M7.2 7.2a6.8 6.8 0 0 0 0 9.6M16.8 7.2a6.8 6.8 0 0 1 0 9.6"/><path d="M4.6 4.6a10.5 10.5 0 0 0 0 14.8M19.4 4.6a10.5 10.5 0 0 1 0 14.8"/>`,
    sliders: `<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="8" cy="7" r="1.8"/><circle cx="15" cy="12" r="1.8"/><circle cx="10" cy="17" r="1.8"/>`,
    message: `<path d="M5 6h14a1 1 0 0 1 1 1v8.2a1 1 0 0 1-1 1H9.2L5 19.4V7a1 1 0 0 1 1-1z"/>`,
    cog: `<circle cx="12" cy="12" r="3"/><path d="M12 4.5v2.2M12 17.3v2.2M4.5 12h2.2M17.3 12h2.2M6.4 6.4l1.6 1.6M16 16l1.6 1.6M17.6 6.4 16 8M8 16l-1.6 1.6"/>`,
    play: `<path d="M9 7.2v9.6L17.4 12z"/>`,
    pause: `<path d="M8 7h2.6v10H8zM13.4 7H16v10h-2.6z"/>`,
    refresh: `<path d="M20 12a8 8 0 1 1-2.2-5.5"/><path d="M20 5.5V10h-4.4"/>`,
    send: `<path d="M4.4 12 19 5.2 14.2 19l-2.4-6.2z"/>`,
    search: `<circle cx="11" cy="11" r="6.2"/><path d="M16.2 16.2 20 20"/>`,
    filter: `<path d="M4 6h16l-6.2 7.4V18l-3.6 2v-6.6z"/>`,
    circle: `<circle cx="12" cy="12" r="7.2"/>`,
    x: `<path d="M6 6l12 12M18 6 6 18"/>`,
    bookmark: `<path d="M6 3.5h12a1 1 0 0 1 1 1V21l-7-4.2L5 21V4.5a1 1 0 0 1 1-1z"/>`,
    plus: `<path d="M12 5v14M5 12h14"/>`,
    pencil: `<path d="M13.6 5.6 18.4 10.4 8 20.8H3.2V16z"/><path d="M11.8 7.4 16.6 12.2"/>`,
    trash: `<path d="M5 7h14M9 7V5.4A1.4 1.4 0 0 1 10.4 4h3.2A1.4 1.4 0 0 1 15 5.4V7M8 7l.8 12.2A1.4 1.4 0 0 0 10.2 20.6h3.6a1.4 1.4 0 0 0 1.4-1.4L16 7"/>`,
    external: `<path d="M10 5H6.4A1.4 1.4 0 0 0 5 6.4v11.2A1.4 1.4 0 0 0 6.4 19h11.2A1.4 1.4 0 0 0 19 17.6V14M13 5h6v6M19 5l-8.5 8.5"/>`,
    pin: `<path d="M12 21s6.5-5.4 6.5-10.2A6.5 6.5 0 0 0 5.5 10.8C5.5 15.6 12 21 12 21z"/><circle cx="12" cy="10.6" r="2.1"/>`,
    columns: `<rect x="4" y="5" width="7" height="14" rx="1.4"/><rect x="13" y="5" width="7" height="14" rx="1.4"/>`,
    eye: `<path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6z"/><circle cx="12" cy="12" r="2.4"/>`,
    eyeOff: `<path d="M3.5 3.5l17 17"/><path d="M10.6 6.2A8 8 0 0 1 12 6c5.2 0 8.7 6 8.7 6a15 15 0 0 1-3.5 4.1"/><path d="M6.5 8.5A15 15 0 0 0 3.3 12S6.8 18 12 18c1.3 0 2.5-.3 3.6-.9"/><path d="M9.9 9.9a2.5 2.5 0 0 0 3.2 3.2"/>`,
    percent: `<circle cx="8" cy="8" r="2"/><circle cx="16" cy="16" r="2"/><path d="M17.5 6.5 6.5 17.5"/>`,
    download: `<path d="M12 4v11M8 11l4 4 4-4M5 19h14"/>`,
    upload: `<path d="M12 20V9M8 13l4-4 4 4M5 5h14"/>`,
    file: `<path d="M7 4h7l5 5v11a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M14 4v5h5"/>`,
    database: `<ellipse cx="12" cy="6.5" rx="7" ry="2.5"/><path d="M5 6.5v11c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-11"/><path d="M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>`,
    check: `<path d="M5 12.4 9.6 17 19 7"/>`,
    more: `<path d="M6 13.5 12 19l6-5.5M6 6.5 12 12l6-5.5"/>`,
    maximize: `<path d="M9 5H5v4M15 5h4v4M5 15v4h4M19 15v4h-4"/>`,
    swap: `<path d="M7 8h11M15 5l3 3-3 3M17 16H6M9 13l-3 3 3 3"/>`,
    grid: `<rect x="4" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.2"/>`,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function icon(name) {
    const inner = ICONS[name];
    if (!inner) return "";
    return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
  }

  function decorateIcons(root = document) {
    const scope = root.querySelectorAll ? root : document;
    const nodes = [];
    if (root.dataset?.icon) nodes.push(root);
    if (scope.querySelectorAll) nodes.push(...scope.querySelectorAll("[data-icon]"));
    for (const el of nodes) {
      if (!el.dataset?.icon) continue;
      if (el.querySelector(":scope > svg.icon")) continue;
      const svg = icon(el.dataset.icon);
      if (svg) el.insertAdjacentHTML("afterbegin", svg);
    }
  }

  function setLabeled(el, label, iconName) {
    if (!el) return;
    if (iconName) el.dataset.icon = iconName;
    el.innerHTML = `${el.dataset.icon ? icon(el.dataset.icon) : ""}<span>${escapeHtml(label)}</span>`;
  }

  window.icon = icon;
  window.decorateIcons = decorateIcons;
  window.setLabeled = setLabeled;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => decorateIcons());
  } else {
    decorateIcons();
  }
})();
