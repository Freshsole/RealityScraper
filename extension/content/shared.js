(() => {
  const ASSET = (name) => chrome.runtime.getURL(`assets/${name}`);

  const ME_TTL = 5 * 60_000;
  const SCORE_TTL = 10 * 60_000;
  let meMem = null;
  const scoreMem = new Map();
  let bootPromise = null;

  function send(message, timeoutMs = 3500) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        resolve(value);
      };
      const timer = setTimeout(() => {
        finish({ ok: false, error: "timeout", status: 0 });
      }, timeoutMs);
      try {
        chrome.runtime.sendMessage(message, (response) => {
          clearTimeout(timer);
          if (chrome.runtime.lastError) {
            finish({ ok: false, error: chrome.runtime.lastError.message, status: 0 });
            return;
          }
          finish(response || { ok: false });
        });
      } catch (err) {
        clearTimeout(timer);
        finish({ ok: false, error: String(err), status: 0 });
      }
    });
  }

  async function storageGet(keys) {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get(keys, (data) => resolve(data || {}));
      } catch {
        resolve({});
      }
    });
  }

  async function directFetch(apiBase, token, path, body, timeoutMs = 3500) {
    const headers = { Accept: "application/json" };
    if (token) headers["X-Realitify-Session"] = token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(`${apiBase}${path}`, {
        method: body !== undefined ? "POST" : "GET",
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        credentials: "omit",
        signal: controller.signal,
      });
      let data = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      return { ok: res.ok, status: res.status, data };
    } catch (err) {
      const aborted =
        (err && (err.name === "AbortError" || err.code === 20)) ||
        controller.signal.aborted;
      return {
        ok: false,
        status: 0,
        data: null,
        error: aborted ? "timeout" : String(err && err.message ? err.message : err),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async function boot({ ids = [], urls = [] } = {}) {
    const key = `${ids.slice().sort().join(",")}|${location.pathname}`;
    if (bootPromise && bootPromise.key === key) return bootPromise.promise;

    const promise = (async () => {
      const now = Date.now();
      const cachedScores = {};
      const missing = [];
      for (const id of ids) {
        const hit = scoreMem.get(String(id));
        if (hit && now - hit.at < SCORE_TTL) cachedScores[id] = hit.item;
        else missing.push(id);
      }

      if (meMem && now - meMem.at < ME_TTL && missing.length === 0 && ids.length) {
        return { ok: true, me: meMem.data, scores: { items: cachedScores } };
      }

      const stored = await storageGet(["apiBase", "sessionToken", "meCache"]);
      const apiBase = (stored.apiBase || "https://realitify.cz").replace(/\/$/, "");
      const token = stored.sessionToken || "";

      async function loadScores(wantIds, wantUrls) {
        if (!wantIds.length && !wantUrls.length) return { items: {} };
        const timeoutMs = wantIds.length <= 1 ? 14000 : 12000;
        const scoreRes = await directFetch(apiBase, token, "/api/extension/scores", {
          ids: wantIds,
          urls: wantUrls,
        }, timeoutMs);
        if (!scoreRes.ok) return null;
        return scoreRes.data || { items: {} };
      }

      async function ingestMissing(items, wantUrls, { wait = true } = {}) {
        const missUrls = [];
        const byId = items || {};
        for (let i = 0; i < ids.length; i++) {
          const id = ids[i];
          const url = urls[i] || wantUrls.find((u) => String(u).includes(String(id))) || "";
          const item = byId[id];
          if (item && item.found === false && url) missUrls.push(url);
        }
        for (const url of wantUrls) {
          const id = extractIdFromHref(url);
          const item = id ? byId[id] : null;
          if ((!item || item.found === false) && url && !missUrls.includes(url)) missUrls.push(url);
        }
        if (!missUrls.length || !token) return items;

        const run = async () => {
          const ingestRes = await directFetch(apiBase, token, "/api/extension/ingest", {
            urls: missUrls.slice(0, 8),
          });
          if (!ingestRes.ok || !ingestRes.data?.items) return items;
          const merged = { ...items, ...ingestRes.data.items };
          for (const [id, item] of Object.entries(ingestRes.data.items)) {
            scoreMem.set(String(id), { at: Date.now(), item });
          }
          return merged;
        };

        if (!wait) {
          run().then((merged) => {
            try {
              window.dispatchEvent(new CustomEvent("rt-scores-updated", { detail: { items: merged } }));
            } catch {
              /* ignore */
            }
          });
          return items;
        }
        return run();
      }

      if (token) {
        const wantIds = missing.length ? missing : ids;
        const tasks = [];
        if (!meMem || now - meMem.at >= ME_TTL) {
          tasks.push(directFetch(apiBase, token, "/api/extension/me"));
        } else {
          tasks.push(Promise.resolve({ ok: true, status: 200, data: meMem.data, cached: true }));
        }
        tasks.push(loadScores(wantIds, urls));

        const [meRes, scoreData] = await Promise.all(tasks);
        if (meRes.ok && meRes.data) {
          meMem = { at: Date.now(), data: meRes.data };
        } else if (stored.meCache?.data) {
          meMem = { at: Date.now(), data: stored.meCache.data };
        } else if (meRes.status === 401) {
          meMem = {
            at: Date.now(),
            data: { authenticated: false, active: false, pro: false, login_url: `${apiBase}/prihlaseni` },
          };
        }

        let items = { ...cachedScores, ...(scoreData?.items || {}) };
        if (meMem?.data?.active) {
          // Detail = wait for scrape; search grid = background so UI stays ~0.5s
          const wait = ids.length <= 1;
          items = await ingestMissing(items, urls, { wait });
        }
        for (const [id, item] of Object.entries(items)) {
          scoreMem.set(String(id), { at: Date.now(), item });
        }

        if (meMem) {
          return { ok: true, me: meMem.data, scores: { items } };
        }
      }

      const res = await send({ type: "RT_BOOT", ids, urls }, 8000);
      if (res.ok) {
        if (res.me) meMem = { at: Date.now(), data: res.me };
        let items = { ...cachedScores, ...(res.scores?.items || {}) };
        if (meMem?.data?.active && token) {
          items = await ingestMissing(items, urls);
        } else if (res.ok && meMem?.data?.active) {
          // SW path: ask background to ingest
          const missUrls = urls.filter((url, i) => {
            const id = ids[i] || extractIdFromHref(url);
            const item = items[id];
            return item && item.found === false;
          });
          if (missUrls.length) {
            const ing = await send({ type: "RT_INGEST", urls: missUrls.slice(0, 8) }, 12000);
            if (ing.ok && ing.data?.items) items = { ...items, ...ing.data.items };
          }
        }
        for (const [id, item] of Object.entries(items)) {
          scoreMem.set(String(id), { at: Date.now(), item });
        }
        return { ok: true, me: res.me || meMem?.data, scores: { items } };
      }

      if (meMem) return { ok: true, me: meMem.data, scores: { items: cachedScores }, stale: true };
      return res;
    })();

    bootPromise = { key, promise };
    try {
      return await promise;
    } finally {
      if (bootPromise && bootPromise.promise === promise) bootPromise = null;
    }
  }

  function extractIdFromHref(href) {
    if (!href) return "";
    try {
      const url = new URL(href, location.origin);
      const parts = url.pathname.split("/").filter(Boolean);
      const last = parts[parts.length - 1] || "";
      if (/^\d+$/.test(last)) return last;
      const match = url.pathname.match(/\/(\d+)\/?(?:$|\?)/);
      return match ? match[1] : "";
    } catch {
      const match = String(href).match(/\/(\d+)(?:\/|$|\?)/);
      return match ? match[1] : "";
    }
  }

  function isDetailPage() {
    return /\/detail\//.test(location.pathname);
  }

  function isSearchPage() {
    return /\/hledani\//.test(location.pathname);
  }

  function findHeaderMount() {
    const selectors = ["header", "[class*='Header']", "nav", "#page-header"];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return document.body;
  }

  function ensureHeaderBadge(me) {
    let badge = document.getElementById("rt-header-badge");
    if (!me || (!me.authenticated && !me.active)) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "rt-header-badge";
      badge.className = "rt-header-badge";
      const mount = findHeaderMount();
      const right =
        mount.querySelector("[class*='user'], [class*='User'], [class*='account'], [class*='login']") ||
        mount.lastElementChild ||
        mount;
      if (right.parentElement) right.parentElement.insertBefore(badge, right);
      else mount.prepend(badge);
    }
    const pro = me.pro
      ? `<span class="rt-pro-tag">PRO</span>`
      : me.active
        ? `<span class="rt-pro-tag">${(me.label || "START").toUpperCase()}</span>`
        : "";
    badge.innerHTML = `
      <img class="rt-pulse-img" src="${ASSET("pulse-dot.svg")}" width="8" height="8" alt="" />
      <span>Realitify Active</span>
      ${pro}
    `;
  }

  function trendIcon(pct) {
    if (pct == null) return "";
    if (pct > 0) return `<img src="${ASSET("arrow-up.svg")}" width="8" height="8" alt="" />`;
    if (pct < 0) return `<img src="${ASSET("arrow-down.svg")}" width="8" height="8" alt="" />`;
    return "";
  }

  function lockedBlock(me) {
    const url = (me && (me.pricing_url || me.login_url)) || "https://realitify.cz";
    const text = !me || !me.authenticated
      ? "Přihlas se k Realitify a uvidíš skóre inzerátů."
      : "Aktivuj tarif Start nebo PRO pro Realitify skóre na Sreality.";
    const cta = !me || !me.authenticated ? "Přihlásit se" : "Aktivovat Realitify";
    return `
      <div class="rt-locked">
        <p>${text}</p>
        <a class="rt-cta" href="${url}" target="_blank" rel="noopener noreferrer">${cta} →</a>
      </div>
    `;
  }

  function missingBlock(item) {
    const url = (item && item.detail_url) || "https://realitify.cz/app";
    return `
      <div class="rt-missing">
        <p>Ještě nemáme v katalogu — stahuji do Realitify…</p>
        <a class="rt-cta" href="${url}" target="_blank" rel="noopener noreferrer">Otevřít Realitify →</a>
      </div>
    `;
  }

  function skeletonCard() {
    return `<div class="rt-card-widget rt-skeleton" aria-hidden="true"><div class="rt-skel-line"></div></div>`;
  }

  function skeletonDetail() {
    return `<div class="rt-detail-widget rt-skeleton" aria-hidden="true"><div class="rt-skel-line"></div><div class="rt-skel-line"></div></div>`;
  }

  function cardWidgetHtml(item) {
    if (!item || !item.found) return missingBlock(item);
    const tier = item.tier || "mid";
    const color = item.color || "#e0be3e";
    const detail = item.detail_url || "#";
    const trend = item.trend_pct;
    return `
      <div class="rt-card-widget" data-tier="${tier}" data-rt-id="${item.id || ""}">
        <div class="rt-card-top">
          <div class="rt-score-info">
            <div class="rt-score-circle" style="border-color:${color};color:${color}">${item.score ?? "—"}</div>
            <div class="rt-score-text">
              <div class="rt-score-eyebrow">REALITIFY SCORE</div>
              <div class="rt-score-label" style="color:${color}">${item.tier_label || ""}</div>
            </div>
          </div>
          <a class="rt-detail-link" href="${detail}" target="_blank" rel="noopener noreferrer">
            DETAIL
            <img src="${ASSET("arrow-right.svg")}" width="10" height="10" alt="" />
          </a>
        </div>
        <div class="rt-pills">
          <span class="rt-pill" style="color:${color};border-color:${item.pill_border || color};background:${item.pill_bg || "#fff7f2"}">${item.source || "—"}</span>
          <span class="rt-pill" style="color:${color};border-color:${item.pill_border || color};background:${item.pill_bg || "#fff7f2"}">${item.days_label || "—"}</span>
          <span class="rt-pill" style="color:${color};border-color:${item.pill_border || color};background:${item.pill_bg || "#fff7f2"}">${trendIcon(trend)}${item.trend_label || "0%"}</span>
          <span class="rt-pill rt-pill--m2" style="color:${color};border-color:${item.pill_border || color};background:${item.pill_bg || "#fff7f2"}">${item.price_m2_label || "—"}</span>
        </div>
      </div>
    `;
  }

  function metricTone(good) {
    if (good === true) return "is-good";
    if (good === false) return "is-bad";
    return "";
  }

  function detailWidgetHtml(item, me) {
    if (!item || !item.found) return missingBlock(item);
    const tier = item.tier || "mid";
    const color = item.color || "#e0be3e";
    const bar = Math.max(8, Math.min(92, item.trend_bar || 50));
    const vsPct = item.price_vs_market_pct;
    const vsGood = vsPct == null ? null : vsPct <= 0;
    const locScore = item.location_score;
    const locGood = locScore == null ? null : locScore >= 6.5 ? true : locScore < 5 ? false : null;
    const loc = locScore != null ? `${locScore} / 10` : "—";
    const days = item.days_on_market;
    const daysGood = days == null ? null : days <= 14 ? true : days >= 60 ? false : null;
    return `
      <div class="rt-detail-widget" data-tier="${tier}" data-rt-id="${item.id || ""}" style="--rt-accent:${color}">
        <div class="rt-detail-header">
          <div class="rt-detail-header-left">
            <div class="rt-detail-score"><span>${item.score ?? "—"}</span></div>
            <div class="rt-verdict">
              <div class="rt-verdict-eyebrow">REALITIFY ANALÝZA</div>
              <div class="rt-verdict-text">${item.verdict || item.tier_label || ""}</div>
            </div>
          </div>
          <div class="rt-brand-tag">
            <img src="${ASSET("shield.svg")}" width="12" height="12" alt="" />
            REALITIFY${me && me.pro ? " PRO" : ""}
          </div>
        </div>
        <div class="rt-metrics">
          <div class="rt-metric" data-tone="${vsGood === true ? "good" : vsGood === false ? "bad" : "neutral"}">
            <div class="rt-metric-left"><span class="rt-dot"></span>Cena vs. trh v lokalitě</div>
            <div class="rt-metric-right">
              <span class="rt-metric-value ${metricTone(vsGood)}">${item.price_vs_label || "—"}</span>
              <div class="rt-bar" aria-hidden="true"><span class="rt-bar-thumb" style="left:${bar}%"></span></div>
            </div>
          </div>
          <div class="rt-metric" data-tone="${vsGood === true ? "good" : vsGood === false ? "bad" : "neutral"}">
            <div class="rt-metric-left"><span class="rt-dot"></span>Cena za metr čtvereční</div>
            <div class="rt-metric-right">
              <span class="rt-metric-value ${metricTone(vsGood)}">${item.price_m2_label || "—"}</span>
              <span class="rt-metric-hint">(Lokalita avg: ${item.locality_avg_label || "—"})</span>
            </div>
          </div>
          <div class="rt-metric" data-tone="${daysGood === true ? "good" : daysGood === false ? "bad" : "neutral"}">
            <div class="rt-metric-left"><span class="rt-dot"></span>Doba na trhu</div>
            <div class="rt-metric-right">
              <span class="rt-metric-value ${metricTone(daysGood)}">${item.days_label || "—"}</span>
              <span class="rt-metric-hint">(${item.market_note || "—"})</span>
            </div>
          </div>
          <div class="rt-metric" data-tone="${locGood === true ? "good" : locGood === false ? "bad" : "neutral"}">
            <div class="rt-metric-left"><span class="rt-dot"></span>Lokalita &amp; Dostupnost score</div>
            <div class="rt-metric-right">
              <span class="rt-metric-value ${metricTone(locGood)}">${loc}</span>
              <span class="rt-metric-hint">${item.location_note ? `(${item.location_note})` : ""}</span>
            </div>
          </div>
        </div>
        <a class="rt-cta" href="${item.detail_url || "https://realitify.cz/app"}" target="_blank" rel="noopener noreferrer">
          Zobrazit kompletní analýzu na Realitify →
        </a>
      </div>
    `;
  }

  function debounce(fn, ms) {
    let t = 0;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function isOurNode(node) {
    if (!node || node.nodeType !== 1) return false;
    return (
      node.id === "rt-header-badge" ||
      node.hasAttribute?.("data-rt-detail") ||
      node.hasAttribute?.("data-rt-card") ||
      node.classList?.contains("rt-card-widget") ||
      node.classList?.contains("rt-detail-widget") ||
      node.closest?.("[data-rt-detail], [data-rt-card], #rt-header-badge, .rt-card-widget, .rt-detail-widget")
    );
  }

  function watchDom(callback) {
    const run = debounce(callback, 400);
    const obs = new MutationObserver((mutations) => {
      for (const m of mutations) {
        const nodes = [...(m.addedNodes || []), ...(m.removedNodes || []), m.target];
        if (nodes.every((n) => isOurNode(n) || (n.nodeType === 3 && isOurNode(n.parentElement)))) {
          continue;
        }
        run();
        return;
      }
    });
    obs.observe(document.documentElement, { childList: true, subtree: true });
    let last = location.href;
    setInterval(() => {
      if (location.href !== last) {
        last = location.href;
        meMem = null;
        run();
      }
    }, 700);
    run();
    return obs;
  }

  // Kick session sync once in background without blocking UI
  try {
    chrome.runtime.sendMessage({ type: "RT_SYNC_SESSION" }, () => void chrome.runtime.lastError);
  } catch {
    /* ignore */
  }

  window.RealitifyExt = {
    ASSET,
    send,
    boot,
    extractIdFromHref,
    isDetailPage,
    isSearchPage,
    ensureHeaderBadge,
    lockedBlock,
    missingBlock,
    skeletonCard,
    skeletonDetail,
    cardWidgetHtml,
    detailWidgetHtml,
    watchDom,
  };
})();
