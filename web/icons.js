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
    user: `<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/>`,
    mail: `<rect x="3.5" y="5.5" width="17" height="13" rx="2"/><path d="m4 7.5 8 6 8-6"/>`,
    phone: `<path d="M6.6 3.8h2.6l1.4 4-1.8 1.2a11 11 0 0 0 5.2 5.2l1.2-1.8 4 1.4v2.6a1.8 1.8 0 0 1-2 1.8A15 15 0 0 1 4.8 5.8a1.8 1.8 0 0 1 1.8-2z"/>`,
    paw: `<circle cx="7" cy="10" r="1.8"/><circle cx="11" cy="6.6" r="1.8"/><circle cx="16" cy="7.4" r="1.8"/><circle cx="18.2" cy="12" r="1.7"/><path d="M12.4 12.4c-2.6 0-5 3-5 5.2 0 1.4 1.2 2 2.6 2 1 0 1.6-.4 2.4-.4s1.4.4 2.4.4c1.4 0 2.6-.6 2.6-2 0-2.2-2.4-5.2-5-5.2z"/>`,
    route: `<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>`,
    alert: `<path d="M12 4 3.6 18.5A1 1 0 0 0 4.5 20h15a1 1 0 0 0 .9-1.5z"/><path d="M12 10v4M12 17h.01"/>`,
    bell: `<path d="M6 8a6 6 0 0 1 12 0c0 6 2.4 8 2.4 8H3.6S6 14 6 8z"/><path d="M10.3 19.6a2 2 0 0 0 3.4 0"/>`,
    clock: `<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>`,
    moon: `<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>`,
    card: `<rect x="3" y="5.5" width="18" height="13" rx="2.4"/><path d="M3 10h18M7 15h3"/>`,
    receipt: `<path d="M6 3.5h12v17l-3-1.8-3 1.8-3-1.8-3 1.8z"/><path d="M9 8h6M9 12h6"/>`,
    key: `<circle cx="8" cy="15" r="3.6"/><path d="m10.6 12.4 8-8M16 7l2.4 2.4M13.4 9.6l1.8 1.8"/>`,
    bot: `<rect x="4.5" y="8" width="15" height="11" rx="2.6"/><path d="M12 8V4.5M9 13v1.5M15 13v1.5"/>`,
    plug: `<path d="M9 4v4M15 4v4M6.5 8h11v3a5.5 5.5 0 0 1-11 0zM12 16.5V20"/>`,
    shield: `<path d="M12 3.5 5 6v5.6c0 4.4 2.9 7.2 7 8.9 4.1-1.7 7-4.5 7-8.9V6z"/><path d="m9 12 2.2 2.2L15.2 10"/>`,
    lock: `<rect x="5" y="10.5" width="14" height="9.5" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>`,
    device: `<rect x="3.5" y="5" width="17" height="11" rx="1.8"/><path d="M8 20h8M12 16v4"/>`,
    logout: `<path d="M14 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19H14M10 12h10M17 8.5l3.5 3.5-3.5 3.5"/>`,
    copy: `<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6A1.5 1.5 0 0 0 14 4.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>`,
    link: `<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>`,
    save: `<path d="M5 4.5h11l3.5 3.5V19a1 1 0 0 1-1 1h-13.5a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1z"/><path d="M8 4.5V9h7V4.5M8 20v-6h8v6"/>`,
    arrowLeft: `<path d="M19 12H5M11 6l-6 6 6 6"/>`,
    sparkles: `<path d="m12 4 1.8 4.7L18.5 10.5l-4.7 1.8L12 17l-1.8-4.7L5.5 10.5l4.7-1.8z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>`,
    zap: `<path d="M13 3.5 5.5 13H12l-1 7.5L18.5 11H12z"/>`,
    calendar: `<rect x="4" y="5.5" width="16" height="14.5" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>`,
    list: `<path d="M9 7h10M9 12h10M9 17h10M5 7h.01M5 12h.01M5 17h.01"/>`,
    image: `<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m5 17 4.5-4.5L13 16l2.5-2.5L19.5 17"/>`,
    discord: `<path d="M7.5 7.2A12 12 0 0 1 12 6.4a12 12 0 0 1 4.5.8c1.6 2.4 2.4 5 2.4 8.1-1.2 1-2.5 1.6-3.8 2l-.9-1.6M7.5 7.2C5.9 9.6 5.1 12.2 5.1 15.3c1.2 1 2.5 1.6 3.8 2l.9-1.6"/><circle cx="9.6" cy="12.4" r="1"/><circle cx="14.4" cy="12.4" r="1"/>`,
    building: `<path d="M5 20V5.5A1.5 1.5 0 0 1 6.5 4h6A1.5 1.5 0 0 1 14 5.5V20M14 10h3.5A1.5 1.5 0 0 1 19 11.5V20M3.5 20h17M8.5 8h2M8.5 12h2M8.5 16h2"/>`,
    crown: `<path d="m4 8 4 4 4-6 4 6 4-4-1.5 10h-13z"/>`,
    gift: `<rect x="4" y="9" width="16" height="11" rx="1.5"/><path d="M3.5 9h17v3h-17zM12 9v11M12 9c-1.2-3-5-3.4-5-1.2C7 9 12 9 12 9zm0 0c1.2-3 5-3.4 5-1.2C17 9 12 9 12 9z"/>`,
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
