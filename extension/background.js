import { DEFAULT_API_BASE, DEV_API_BASES, SESSION_COOKIE } from "./config.js";

const ME_TTL_MS = 5 * 60_000;
const SCORE_TTL_MS = 10 * 60_000;

let meCache = null;
const scoreById = new Map();
const inflightScores = new Map();
let inflightMe = null;

async function getApiBase() {
  const stored = await chrome.storage.local.get(["apiBase"]);
  const sync = await chrome.storage.sync.get(["apiBase"]);
  const value = (stored.apiBase || sync.apiBase || "").trim();
  return value || DEFAULT_API_BASE;
}

async function cookieUrl(apiBase, domain) {
  try {
    const u = new URL(apiBase);
    const isLocal = u.hostname === "127.0.0.1" || u.hostname === "localhost";
    if (isLocal) return `${u.protocol}//${domain}${u.port ? `:${u.port}` : ""}/`;
    return `https://${domain}/`;
  } catch {
    return `https://${domain}/`;
  }
}

async function syncSessionToStorage() {
  const apiBase = await getApiBase();
  const domains = [];
  try {
    const host = new URL(apiBase).hostname;
    domains.push(host, host.startsWith("www.") ? host.slice(4) : `www.${host}`);
  } catch {
    /* ignore */
  }
  domains.push("realitify.cz", "www.realitify.cz", "127.0.0.1", "localhost");

  let token = "";
  for (const domain of [...new Set(domains)]) {
    try {
      const cookie = await chrome.cookies.get({
        url: await cookieUrl(apiBase, domain),
        name: SESSION_COOKIE,
      });
      if (cookie?.value) {
        token = cookie.value;
        break;
      }
    } catch {
      /* next */
    }
  }
  if (!token) {
    try {
      const all = await chrome.cookies.getAll({ name: SESSION_COOKIE });
      if (all?.length) token = all[0].value;
    } catch {
      /* ignore */
    }
  }
  if (token) {
    await chrome.storage.local.set({ sessionToken: token, apiBase, sessionSyncedAt: Date.now() });
  } else {
    await chrome.storage.local.set({ apiBase, sessionSyncedAt: Date.now() });
  }
  return { apiBase, token };
}

async function readSessionToken() {
  const stored = await chrome.storage.local.get(["sessionToken"]);
  if (stored.sessionToken) return stored.sessionToken;
  const synced = await syncSessionToStorage();
  return synced.token || "";
}

async function apiFetch(path, { method = "GET", body } = {}) {
  const apiBase = await getApiBase();
  const token = await readSessionToken();
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Realitify-Session"] = token;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const res = await fetch(`${apiBase}${path}`, {
      method,
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
    if (!res.ok) {
      const err = new Error((data && (data.detail || data.message)) || `HTTP ${res.status}`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

async function getMe(force = false) {
  if (!force && meCache && Date.now() - meCache.at < ME_TTL_MS) return meCache.data;
  if (inflightMe) return inflightMe;
  inflightMe = (async () => {
    try {
      const data = await apiFetch("/api/extension/me");
      meCache = { at: Date.now(), data };
      await chrome.storage.local.set({ meCache: meCache });
      return data;
    } catch (err) {
      if (err.status === 401 || err.name === "AbortError") {
        const data = {
          authenticated: false,
          active: false,
          pro: false,
          login_url: `${await getApiBase()}/prihlaseni`,
          pricing_url: `${await getApiBase()}/#cenik`,
        };
        meCache = { at: Date.now(), data };
        return data;
      }
      const stored = await chrome.storage.local.get(["meCache"]);
      if (stored.meCache?.data) return stored.meCache.data;
      throw err;
    } finally {
      inflightMe = null;
    }
  })();
  return inflightMe;
}

function cacheScores(items) {
  const at = Date.now();
  for (const [id, item] of Object.entries(items || {})) {
    if (!id) continue;
    scoreById.set(String(id), { at, item });
    if (item?.id) scoreById.set(String(item.id), { at, item });
  }
}

async function getScores({ ids = [], urls = [] }) {
  const wantIds = [...new Set(ids.map(String).filter(Boolean))];
  const now = Date.now();
  const items = {};
  const missing = [];

  for (const id of wantIds) {
    const hit = scoreById.get(id);
    if (hit && now - hit.at < SCORE_TTL_MS) items[id] = hit.item;
    else missing.push(id);
  }

  if (!missing.length && !urls.length) {
    return { items, count: Object.keys(items).length };
  }

  const key = missing.slice().sort().join(",") + "|" + urls.slice().sort().join(",");
  if (inflightScores.has(key)) {
    const data = await inflightScores.get(key);
    return { items: { ...items, ...(data.items || {}) }, count: 0 };
  }

  const promise = (async () => {
    const data = await apiFetch("/api/extension/scores", {
      method: "POST",
      body: { ids: missing.length ? missing : wantIds, urls },
    });
    cacheScores(data.items || {});
    return data;
  })();

  inflightScores.set(key, promise);
  try {
    const data = await promise;
    return { items: { ...items, ...(data.items || {}) }, count: Object.keys(data.items || {}).length, account: data.account };
  } finally {
    inflightScores.delete(key);
  }
}

async function bootPage({ ids = [], urls = [] } = {}) {
  await syncSessionToStorage();
  const [me, scores] = await Promise.all([getMe(), getScores({ ids, urls })]);
  return { me, scores };
}

chrome.runtime.onInstalled.addListener(() => {
  syncSessionToStorage().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  syncSessionToStorage().catch(() => {});
});

// Warm caches so first content-script call is fast
syncSessionToStorage()
  .then(() => getMe())
  .catch(() => {});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const reply = (payload) => {
    try {
      sendResponse(payload);
    } catch {
      /* channel closed — ignore */
    }
  };

  (async () => {
    if (!message || !message.type) return { ok: false };
    if (message.type === "RT_SYNC_SESSION") {
      const synced = await syncSessionToStorage();
      return { ok: true, ...synced };
    }
    if (message.type === "RT_BOOT") {
      const data = await bootPage({ ids: message.ids || [], urls: message.urls || [] });
      return { ok: true, ...data };
    }
    if (message.type === "RT_INGEST") {
      await syncSessionToStorage();
      const data = await apiFetch("/api/extension/ingest", {
        method: "POST",
        body: { urls: message.urls || [], ids: message.ids || [] },
      });
      cacheScores(data.items || {});
      return { ok: true, data };
    }
    if (message.type === "RT_GET_ME") {
      const me = await getMe(Boolean(message.force));
      return { ok: true, me };
    }
    if (message.type === "RT_GET_SCORES") {
      const data = await getScores({ ids: message.ids || [], urls: message.urls || [] });
      return { ok: true, data };
    }
    if (message.type === "RT_SET_API_BASE") {
      const base = String(message.apiBase || "").trim().replace(/\/$/, "");
      await chrome.storage.local.set({ apiBase: base || DEFAULT_API_BASE });
      await chrome.storage.sync.set({ apiBase: base || DEFAULT_API_BASE });
      meCache = null;
      scoreById.clear();
      return { ok: true, apiBase: base || DEFAULT_API_BASE };
    }
    if (message.type === "RT_SET_SESSION") {
      const token = String(message.token || "").trim();
      if (token) await chrome.storage.local.set({ sessionToken: token });
      else await chrome.storage.local.remove("sessionToken");
      meCache = null;
      scoreById.clear();
      return { ok: true };
    }
    if (message.type === "RT_GET_CONFIG") {
      return {
        ok: true,
        apiBase: await getApiBase(),
        defaults: [DEFAULT_API_BASE, ...DEV_API_BASES],
      };
    }
    return { ok: false };
  })()
    .then(reply)
    .catch((err) =>
      reply({
        ok: false,
        error: err.message || String(err),
        status: err.status || 0,
      })
    );

  return true;
});
