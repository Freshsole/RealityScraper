(() => {
  const RT = window.RealitifyExt;
  if (!RT) return;

  const MARK = "data-rt-card";
  let busy = false;
  let lastKey = "";

  function cardRoots() {
    const links = [...document.querySelectorAll('a[href*="/detail/"]')];
    const roots = new Map();
    for (const link of links) {
      const id = RT.extractIdFromHref(link.href);
      if (!id) continue;
      const card =
        link.closest("article") ||
        link.closest("li") ||
        link.closest("[class*='property']") ||
        link.closest("[class*='Property']") ||
        link.closest("[class*='estate']") ||
        link.closest("[class*='item']") ||
        link.parentElement;
      if (!card) continue;
      const rect = card.getBoundingClientRect();
      if (rect.height < 120 || rect.width < 180) continue;
      if (!roots.has(id)) roots.set(id, { id, url: link.href.split("#")[0], card });
    }
    return [...roots.values()];
  }

  function mountPoint(card) {
    const price =
      card.querySelector("[class*='price'], [class*='Price'], strong, .norm-price") || null;
    if (price && price.parentElement) return { parent: price.parentElement, after: price };
    return { parent: card, after: null };
  }

  function attach(card, id, html) {
    let wrap = card.querySelector(`[${MARK}="${id}"]`);
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.setAttribute(MARK, id);
      const mount = mountPoint(card);
      if (mount.after && mount.after.parentElement === mount.parent) {
        mount.after.insertAdjacentElement("afterend", wrap);
      } else {
        mount.parent.appendChild(wrap);
      }
    }
    if (wrap.dataset.rtHtml !== html) {
      wrap.dataset.rtHtml = html;
      wrap.innerHTML = html;
    }
  }

  async function injectCards() {
    if (RT.isDetailPage()) return;
    const entries = cardRoots();
    if (!entries.length) return;

    const key = entries.map((e) => e.id).sort().join(",");
    const pending = entries.filter((e) => !e.card.querySelector(`[${MARK}="${e.id}"]`));
    if (!pending.length && key === lastKey) return;
    if (busy) return;
    busy = true;
    lastKey = key;

    const applyItems = (me, items) => {
      for (const entry of entries) {
        if (!me.active) {
          attach(entry.card, entry.id, RT.lockedBlock(me));
          continue;
        }
        const item = items[entry.id];
        attach(entry.card, entry.id, RT.cardWidgetHtml(item));
      }
    };

    const onUpdate = (event) => {
      const items = event.detail?.items || {};
      applyItems(meCache || { active: true }, items);
    };
    window.addEventListener("rt-scores-updated", onUpdate, { once: true });

    let meCache = null;
    try {
      for (const entry of pending) {
        attach(entry.card, entry.id, RT.skeletonCard());
      }

      const res = await RT.boot({
        ids: entries.map((e) => e.id),
        urls: entries.map((e) => e.url),
      });
      if (!res.ok) return;

      meCache = res.me || { authenticated: false, active: false };
      RT.ensureHeaderBadge(meCache);
      applyItems(meCache, res.scores?.items || {});
    } finally {
      busy = false;
    }
  }

  RT.watchDom(() => {
    if (RT.isDetailPage()) return;
    injectCards().catch(() => {});
  });
})();
