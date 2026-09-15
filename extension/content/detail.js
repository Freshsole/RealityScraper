(() => {
  const RT = window.RealitifyExt;
  if (!RT) return;

  const MARK = "data-rt-detail";
  let busy = false;
  let placedFor = "";

  function detailId() {
    return RT.extractIdFromHref(location.href);
  }

  function textOf(el) {
    return ((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim();
  }

  function findPriceNearTitle() {
    const h1 = document.querySelector("h1");
    if (!h1) return null;
    const priceRe = /\d[\d\s\u00a0.]*\s*Kč/i;
    const monthRe = /měsíc|mesic/i;
    const scope =
      h1.closest("[class*='detail']") ||
      h1.closest("[class*='content']") ||
      h1.closest("article") ||
      h1.closest("main") ||
      h1.parentElement;
    if (!scope) return null;

    const nodes = scope.querySelectorAll("p, span, div, strong, b, h2, h3");
    let best = null;
    let bestDist = Infinity;
    const h1Top = h1.getBoundingClientRect().top;

    for (const node of nodes) {
      if (node.querySelector?.("h1")) continue;
      const text = textOf(node);
      if (!text || text.length > 80 || !priceRe.test(text)) continue;
      const looksLikePrice =
        /Kč\s*\/\s*měsíc/i.test(text) ||
        /Kč\s*\/\s*mesic/i.test(text) ||
        (/Kč/i.test(text) && !/m²|m2|energie|třída/i.test(text));
      if (!looksLikePrice) continue;
      if (!(h1.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
      const dist = Math.abs(node.getBoundingClientRect().top - h1Top) + (monthRe.test(text) ? -40 : 0);
      if (dist < bestDist) {
        bestDist = dist;
        best = node;
      }
    }
    return best;
  }

  /** Price + energy row (e.g. css-czxitj) — never the big MuiBox that also wraps the title. */
  function findPriceBlock() {
    const h1 = document.querySelector("h1");
    const price = findPriceNearTitle();
    if (!price) return null;

    let best = price;
    let node = price;
    for (let i = 0; i < 8 && node; i++) {
      const parent = node.parentElement;
      if (!parent || (h1 && parent.contains(h1))) break;
      const rect = parent.getBoundingClientRect();
      if (rect.height <= 0 || rect.height > 160) break;
      // Prefer the compact wrapper that still only holds price (+ energy badge).
      const text = textOf(parent);
      if (text.length > 160) break;
      best = parent;
      node = parent;
    }
    return best;
  }

  function findAnchor() {
    return findPriceBlock() || document.querySelector("h1");
  }

  function placeWidget(wrap) {
    document.querySelectorAll(`[${MARK}]`).forEach((n) => {
      if (n !== wrap) n.remove();
    });
    const anchor = findAnchor();
    if (!anchor) {
      (document.querySelector("main") || document.body).prepend(wrap);
      return;
    }
    if (wrap.previousElementSibling === anchor && wrap.parentElement === anchor.parentElement) return;
    anchor.insertAdjacentElement("afterend", wrap);
  }

  async function injectDetail() {
    if (!RT.isDetailPage()) {
      document.querySelectorAll(`[${MARK}]`).forEach((n) => n.remove());
      placedFor = "";
      return;
    }
    const id = detailId();
    if (!id) return;

    const existing = document.querySelector(`[${MARK}="${id}"]`);
    const anchor = findAnchor();
    const wellPlaced =
      existing &&
      anchor &&
      existing.previousElementSibling === anchor &&
      existing.parentElement === anchor.parentElement;
    if (existing && placedFor === id && !existing.classList.contains("rt-pending") && wellPlaced) {
      return; // already done — do not re-place (avoids mutation loops)
    }
    if (busy) return;
    busy = true;

    try {
      let wrap = existing;
      if (!wrap) {
        wrap = document.createElement("div");
        wrap.setAttribute(MARK, id);
        wrap.classList.add("rt-pending");
        wrap.innerHTML = RT.skeletonDetail();
        placeWidget(wrap);
      }

      const res = await RT.boot({ ids: [id], urls: [location.href] });
      if (!document.contains(wrap)) {
        wrap = document.createElement("div");
        wrap.setAttribute(MARK, id);
      }

      if (!res.ok) {
        wrap.classList.remove("rt-pending");
        wrap.innerHTML = RT.lockedBlock({ authenticated: false, active: false });
        placeWidget(wrap);
        placedFor = id;
        return;
      }

      const me = res.me || { authenticated: false, active: false };
      RT.ensureHeaderBadge(me);

      if (!me.active) {
        wrap.classList.remove("rt-pending");
        wrap.innerHTML = RT.lockedBlock(me);
        placeWidget(wrap);
        placedFor = id;
        return;
      }

      const item = res.scores?.items?.[id] || Object.values(res.scores?.items || {})[0];
      wrap.classList.remove("rt-pending");
      wrap.innerHTML = RT.detailWidgetHtml(item, me);
      placeWidget(wrap);
      placedFor = id;
    } finally {
      busy = false;
    }
  }

  let lastHref = location.href;
  RT.watchDom(() => {
    if (location.href !== lastHref) {
      lastHref = location.href;
      placedFor = "";
    }
    injectDetail().catch(() => {});
  });
})();
