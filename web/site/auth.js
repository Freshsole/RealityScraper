function icon(src, w, h) {
  return `<span class="icon" style="width:${w}px;height:${h}px"><img src="${src}" width="${w}" height="${h}" alt="" /></span>`;
}

function syncPasswordToggle(btn) {
  const wrap = btn.closest(".field-input");
  const input = wrap.querySelector("input");
  const hidden = input.type === "password";
  btn.classList.toggle("is-hidden", hidden);
  const label = btn.querySelector("span.txt");
  if (label) label.textContent = hidden ? "Ukázat" : "Skrýt";
}

function togglePassword(btn) {
  const wrap = btn.closest(".field-input");
  const input = wrap.querySelector("input");
  input.type = input.type === "password" ? "text" : "password";
  syncPasswordToggle(btn);
}

function strengthLevel(value) {
  if (!value) return 0;
  let score = 0;
  if (value.length >= 8) score += 1;
  if (/[A-Z]/.test(value) && /[a-z]/.test(value)) score += 1;
  if (/\d/.test(value) || /[^A-Za-z0-9]/.test(value)) score += 1;
  return Math.min(3, score);
}

function bindStrength(input) {
  const box = document.querySelector(".strength");
  if (!input || !box) return;
  const label = box.querySelector(".strength-label");
  const names = ["Zadejte heslo", "Slabé heslo", "Střední síla hesla", "Silné heslo"];
  const apply = () => {
    const level = strengthLevel(input.value);
    box.dataset.level = String(level);
    box.querySelector(".strength-bars").dataset.level = String(level);
    label.textContent = names[level];
  };
  input.addEventListener("input", apply);
  apply();
}

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((ch) => ch.charCodeAt(0)));
}

// Asks the browser for notification permission (must run inside a click) and
// registers this device for Web Push. Needs the session created in step 1.
async function enablePush() {
  if (!pushSupported()) throw new Error("Tento prohlížeč push notifikace nepodporuje.");
  if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    throw new Error("Push notifikace fungují jen na zabezpečeném spojení (HTTPS).");
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Oznámení jsou v prohlížeči zablokovaná. Povolte je v nastavení webu a zkuste to znovu.");
  }
  const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const vapid = await fetch("/api/push/vapid").then((res) => res.json());
  if (!vapid.publicKey) throw new Error("Push se teď nepodařilo zapnout. Zkuste to později v nastavení.");
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapid.publicKey),
    });
  }
  const response = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(subscription.toJSON()),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Odběr se nepodařilo uložit.");
}

function bindToggles(root) {
  root.querySelectorAll("[data-toggle]").forEach((el) => {
    if (el.hasAttribute("data-locked")) {
      // PRO-only option: stays off, the row shows a tooltip explaining why.
      el.addEventListener("click", (event) => {
        event.preventDefault();
        toast(el.closest("[data-tooltip]")?.dataset.tooltip || "Dostupné v PRO verzi.");
      });
      return;
    }
    if (el.dataset.kind === "push") {
      el.addEventListener("click", async () => {
        if (el.classList.contains("on")) {
          el.classList.remove("on");
          return;
        }
        el.classList.add("busy");
        try {
          await enablePush();
          el.classList.add("on");
        } catch (err) {
          el.classList.remove("on");
          // Browser-level failures (e.g. the push service refusing) arrive as English DOMExceptions.
          toast(
            err instanceof DOMException
              ? "Prohlížeč registraci push notifikací odmítl. Zkuste to později v nastavení účtu nebo v jiném prohlížeči."
              : err.message || "Push notifikace se nepodařilo zapnout.",
          );
        } finally {
          el.classList.remove("busy");
        }
      });
      return;
    }
    el.addEventListener("click", () => el.classList.toggle("on"));
  });
}

function bindChoices(root, sel, multi) {
  root.querySelectorAll(sel).forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!multi) {
        root.querySelectorAll(sel).forEach((other) => other.classList.remove("on"));
        btn.classList.add("on");
        return;
      }
      btn.classList.toggle("on");
    });
  });
}

document.querySelectorAll(".pw-toggle").forEach((btn) => {
  syncPasswordToggle(btn);
  btn.addEventListener("click", () => togglePassword(btn));
});

bindStrength(document.querySelector("#reg-password"));
bindStrength(document.querySelector("#reset-password"));

const PROMO_STORE = "realitify_promo";

function promoFromUrl() {
  try {
    return new URLSearchParams(location.search).get("promo") || "";
  } catch {
    return "";
  }
}

function readStoredPromo() {
  try {
    return sessionStorage.getItem(PROMO_STORE) || "";
  } catch {
    return "";
  }
}

function writeStoredPromo(code) {
  try {
    const value = String(code || "").trim().toUpperCase();
    if (value) sessionStorage.setItem(PROMO_STORE, value);
    else sessionStorage.removeItem(PROMO_STORE);
  } catch {
    /* ignore */
  }
}

function fillPromoInput(input) {
  if (!input) return;
  const fromUrl = promoFromUrl().trim().toUpperCase();
  if (fromUrl) {
    input.value = fromUrl;
    writeStoredPromo(fromUrl);
    return;
  }
  if (!input.value) input.value = readStoredPromo();
}

const MISMATCH = "Hesla se neshodují.";

function toast(message, kind = "error") {
  let host = document.querySelector(".auth-toasts");
  if (!host) {
    host = document.createElement("div");
    host.className = "auth-toasts";
    document.body.appendChild(host);
  }
  const item = document.createElement("div");
  item.className = `auth-toast auth-toast-${kind}`;
  item.innerHTML = `<span class="auth-toast-dot"></span><p>${escapeHtml(message)}</p>`;
  host.appendChild(item);
  requestAnimationFrame(() => item.classList.add("in"));
  const hide = () => {
    item.classList.remove("in");
    setTimeout(() => item.remove(), 220);
  };
  const timer = setTimeout(hide, 5200);
  item.addEventListener("click", () => {
    clearTimeout(timer);
    hide();
  });
}

function fieldError(input, message) {
  const field = input?.closest(".field");
  if (!field) return;
  field.querySelector(".field-input")?.classList.add("is-invalid");
  let hint = field.querySelector(".field-error");
  if (!hint) {
    hint = document.createElement("p");
    hint.className = "field-error";
    field.appendChild(hint);
  }
  hint.hidden = false;
  hint.textContent = message;
}

function clearFieldError(input) {
  const field = input?.closest(".field");
  if (!field) return;
  field.querySelector(".field-input")?.classList.remove("is-invalid");
  const hint = field.querySelector(".field-error");
  if (hint) {
    hint.hidden = true;
    hint.textContent = "";
  }
}

function passwordsMatch(a, b, { toastOnMismatch = false } = {}) {
  const match = a.value === b.value && a.value !== "";
  if (match) {
    clearFieldError(a);
    clearFieldError(b);
    return true;
  }
  if (b.value !== "" || toastOnMismatch) {
    fieldError(a, MISMATCH);
    fieldError(b, MISMATCH);
  }
  if (toastOnMismatch) toast(MISMATCH);
  return false;
}

function bindPasswordPair(primary, confirm) {
  if (!primary || !confirm) return;
  const check = () => passwordsMatch(primary, confirm);
  primary.addEventListener("input", check);
  confirm.addEventListener("input", check);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseAmount(raw) {
  const digits = String(raw || "").replace(/[^\d]/g, "");
  return digits ? Number(digits) : null;
}

function shortLabel(label) {
  return String(label || "").split(",")[0].trim();
}

function srealityDistricts(localities) {
  const keys = new Set();
  for (const loc of localities) {
    if (loc.id === "R51684" || loc.sreality?.includes(",")) {
      String(loc.sreality || "")
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .forEach((slug) => keys.add(slug));
      continue;
    }
    if (loc.sreality) {
      keys.add(loc.sreality);
      continue;
    }
    const name = shortLabel(loc.label);
    const numbered = /^Praha\s+(\d+)$/i.exec(name);
    if (numbered) keys.add(`praha-${numbered[1]}`);
    else if (/^Praha$/i.test(name) || loc.id === "R435514") keys.add("praha");
    else if (/^Brno$/i.test(name) || loc.id === "R438171") keys.add("brno");
    else if (/^Plzeň$/i.test(name) || loc.id === "R438344") keys.add("plzen");
    else if (/^Ostrava$/i.test(name) || loc.id === "R437354") keys.add("ostrava");
  }
  return [...keys];
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Uložení se nepovedlo");
  return data;
}

const loginForm = document.querySelector("#login-form");
if (loginForm) {
  const loginPw = loginForm.querySelector("#password");
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = loginForm.querySelector("#email")?.value.trim() || "";
    const password = loginPw?.value || "";
    try {
      const account = await postJson("/api/auth/login", { email, password });
      const next = new URLSearchParams(location.search).get("next") || "/prehled";
      if (account && account.email_verified === false) {
        location.href = `/registrace?verify=1&next=${encodeURIComponent(next)}`;
        return;
      }
      location.href = next.startsWith("/oauth/authorize") || (next.startsWith("/nabidka") && !next.startsWith("//")) ? next : "/prehled";
    } catch (err) {
      toast(err.message || "Přihlášení se nepovedlo");
    }
  });
}

const forgotForm = document.querySelector("#forgot-form");
const resetForm = document.querySelector("#reset-form");
const forgotSuccess = document.querySelector("#forgot-success");
const resetToken = new URLSearchParams(location.search).get("token") || "";

if (forgotForm && resetForm && resetToken) {
  forgotForm.hidden = true;
  resetForm.hidden = false;
  if (forgotSuccess) forgotSuccess.hidden = true;
  fetch(`/api/auth/reset?token=${encodeURIComponent(resetToken)}`)
    .then(async (response) => {
      if (response.ok) return;
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "Odkaz je neplatný nebo vypršel");
    })
    .catch((err) => {
      resetForm.hidden = true;
      toast(err.message || "Odkaz je neplatný nebo vypršel");
      setTimeout(() => {
        location.href = "/heslo";
      }, 1800);
    });
}

if (forgotForm) {
  forgotForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = forgotForm.querySelector("input[type=email]").value.trim();
    const ok = document.querySelector("#forgot-success");
    const btn = forgotForm.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await postJson("/api/auth/forgot", { email });
      forgotForm.hidden = true;
      if (ok) {
        ok.hidden = false;
        const label = ok.querySelector("[data-email]");
        if (label) label.textContent = email;
      }
    } catch (err) {
      toast(err.message || "Odeslání se nepovedlo");
    } finally {
      if (btn) btn.disabled = false;
    }
  });
}

if (resetForm) {
  const resetPw = resetForm.querySelector("#reset-password");
  const resetPw2 = resetForm.querySelector("#reset-password2");
  bindPasswordPair(resetPw, resetPw2);
  resetForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!passwordsMatch(resetPw, resetPw2, true)) return;
    const password = resetPw?.value || "";
    if (password.length < 8) {
      toast("Heslo musí mít alespoň 8 znaků");
      return;
    }
    const btn = resetForm.querySelector('button[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await postJson("/api/auth/reset", { token: resetToken, password });
      location.href = "/prehled";
    } catch (err) {
      toast(err.message || "Obnovení hesla se nepovedlo");
      if (btn) btn.disabled = false;
    }
  });
}

const register = document.querySelector("#register-flow");
if (register) {
  fillPromoInput(register.querySelector("#reg-promo"));
  bindPasswordPair(register.querySelector("#reg-password"), register.querySelector("#reg-password2"));
  const panels = [...register.querySelectorAll(".step-panel")];
  const show = (n) => {
    panels.forEach((p, i) => {
      p.hidden = i !== n;
    });
  };
  show(0);

  const prefs = {
    offer: "pronajem",
    localities: [],
    sizes: new Set(["2+kk", "3+kk"]),
    catalog: null,
  };

  const cityInput = register.querySelector("#city");
  const suggestEl = register.querySelector("#city-suggest");
  const chipsEl = register.querySelector("#city-chips");
  const sizesEl = register.querySelector("#size-chips");
  const errorEl = register.querySelector("#pref-error");
  let suggestTimer = 0;
  let suggestItems = [];
  let refinedNext = "";

  function showError(message) {
    if (!errorEl) return;
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  }

  function renderLocalities() {
    if (!chipsEl) return;
    const quick = (prefs.catalog?.sources?.bezrealitky?.catalog?.districts || []).filter(([, label]) =>
      ["Praha", "Brno", "Plzeň"].includes(label),
    );
    const selected = prefs.localities;
    const selectedIds = new Set(selected.map((item) => item.id));
    const chosen = selected
      .map(
        (item) =>
          `<button class="chip on" type="button" data-remove-city="${escapeHtml(item.id)}">${escapeHtml(shortLabel(item.label))} <span class="icon" style="width:12px;height:12px"><img src="/static/site/assets/x-circle.svg" width="12" height="12" alt="" /></span></button>`,
      )
      .join("");
    const extras = quick
      .filter(([id]) => !selectedIds.has(id))
      .map(
        ([id, label]) =>
          `<button class="chip" type="button" data-add-city="${escapeHtml(id)}" data-label="${escapeHtml(label)}">+ ${escapeHtml(label)}</button>`,
      )
      .join("");
    chipsEl.innerHTML = chosen + extras;
  }

  function renderSizes() {
    if (!sizesEl) return;
    const sizes = prefs.catalog?.catalog?.sizes || [
      ["1+kk", "1+kk"],
      ["1+1", "1+1"],
      ["2+kk", "2+kk"],
      ["2+1", "2+1"],
      ["3+kk", "3+kk"],
      ["3+1", "3+1"],
      ["4+kk", "4+kk"],
      ["4+1", "4+1"],
    ];
    sizesEl.innerHTML = sizes
      .map(
        ([key, label]) =>
          `<button class="disp${prefs.sizes.has(key) ? " on" : ""}" type="button" data-size="${escapeHtml(key)}">${escapeHtml(label)}</button>`,
      )
      .join("");
  }

  function addLocality(id, label, sreality) {
    if (!id || prefs.localities.some((item) => item.id === id)) return;
    prefs.localities.push({ id, label, sreality });
    renderLocalities();
  }

  function hideSuggest() {
    if (!suggestEl) return;
    suggestEl.hidden = true;
    suggestEl.innerHTML = "";
  }

  async function searchCities(q) {
    if (!suggestEl) return;
    if (q.length < 2) {
      hideSuggest();
      return;
    }
    const response = await fetch(`/api/filters/locality?q=${encodeURIComponent(q)}`);
    const data = await response.json().catch(() => ({ items: [] }));
    suggestItems = data.items || [];
    if (!suggestItems.length) {
      suggestEl.hidden = false;
      suggestEl.innerHTML = `<button type="button" disabled>Nic se nenašlo</button>`;
      return;
    }
    suggestEl.hidden = false;
    suggestEl.innerHTML = suggestItems
      .map(
        (item, index) =>
          `<button type="button" data-suggest="${index}">${escapeHtml(item.label)}</button>`,
      )
      .join("");
  }

  function readPrefsForm() {
    const offer = register.querySelector("#offer-seg .on")?.dataset.offer || "pronajem";
    prefs.offer = offer;
    return {
      offer,
      localities: prefs.localities.map((item) => ({ id: item.id, label: item.label })),
      sizes: [...prefs.sizes],
      price_from: null,
      price_to: parseAmount(register.querySelector("#price-max")?.value),
      area_from: parseAmount(register.querySelector("#area-min")?.value),
      area_to: parseAmount(register.querySelector("#area-max")?.value),
    };
  }

  // Typical floor area for a maximum rent / purchase price. Used to prefill the
  // area range when the visitor gave a price but no area.
  const AREA_BY_RENT = [
    [12000, 20, 40],
    [16000, 25, 45],
    [20000, 30, 55],
    [25000, 40, 65],
    [32000, 50, 80],
    [45000, 65, 110],
    [Infinity, 80, 150],
  ];
  const AREA_BY_SALE = [
    [3500000, 20, 45],
    [5000000, 30, 60],
    [7000000, 45, 80],
    [10000000, 60, 110],
    [15000000, 80, 150],
    [Infinity, 100, 250],
  ];
  // Rough floor-area range per layout, used to preselect layouts for an area range.
  const AREA_BY_SIZE = {
    "1+kk": [20, 38],
    "1+1": [28, 45],
    "2+kk": [38, 62],
    "2+1": [45, 70],
    "3+kk": [60, 90],
    "3+1": [65, 100],
    "4+kk": [85, 125],
    "4+1": [90, 130],
  };

  function areaForPrice(price, offer) {
    const table = offer === "prodej" ? AREA_BY_SALE : AREA_BY_RENT;
    const row = table.find(([limit]) => price <= limit) || table[table.length - 1];
    return { from: row[1], to: row[2] };
  }

  function sizesForArea(from, to) {
    const lo = from || 0;
    const hi = to || Infinity;
    const keys = Object.keys(AREA_BY_SIZE);
    // Layouts whose typical size sits inside the range; fall back to any overlap.
    const inside = keys.filter((key) => {
      const mid = (AREA_BY_SIZE[key][0] + AREA_BY_SIZE[key][1]) / 2;
      return mid >= lo && mid <= hi;
    });
    const overlap = keys.filter((key) => AREA_BY_SIZE[key][0] <= hi && AREA_BY_SIZE[key][1] >= lo);
    const pick = inside.length ? inside : overlap;
    return pick.length ? pick : ["2+kk", "3+kk"];
  }

  function setAmountFields(saved) {
    const price = register.querySelector("#price-max");
    const areaMin = register.querySelector("#area-min");
    const areaMax = register.querySelector("#area-max");
    if (price) price.value = (saved.price_to || 22000).toLocaleString("cs-CZ");
    if (areaMin) areaMin.value = saved.area_from ? String(saved.area_from) : "45";
    if (areaMax) areaMax.value = saved.area_to ? String(saved.area_to) : "";
  }

  // Search the visitor entered on the homepage (?next=/nabidka?q=...&price_to=...).
  function searchFromNext() {
    const next = new URLSearchParams(location.search).get("next") || "";
    if (!next.startsWith("/nabidka")) return null;
    const query = new URLSearchParams(next.split("?")[1] || "");
    const num = (key) => {
      const value = Number(String(query.get(key) || "").replace(/\s/g, ""));
      return Number.isFinite(value) && value > 0 ? value : null;
    };
    const found = {
      offer: query.get("offer") === "prodej" ? "prodej" : "pronajem",
      q: (query.get("q") || "").trim(),
      price_to: num("price_to"),
      area_from: num("area_from"),
      area_to: num("area_to"),
    };
    return found.q || found.price_to || found.area_from || found.area_to ? found : null;
  }

  async function localityForQuery(q) {
    const districts = prefs.catalog?.sources?.bezrealitky?.catalog?.districts || [];
    const wanted = q.toLowerCase();
    const exact = districts.find(([, label]) => String(label).toLowerCase() === wanted);
    if (exact) return { id: exact[0], label: exact[1] };
    try {
      const data = await fetch(`/api/filters/locality?q=${encodeURIComponent(q)}`).then((res) => res.json());
      const item = (data.items || [])[0];
      if (item && item.id) return { id: item.id, label: item.label, sreality: item.sreality };
    } catch (_err) {
      /* fall through to Praha */
    }
    const praha = districts.find(([, label]) => label === "Praha");
    return praha ? { id: praha[0], label: praha[1] } : null;
  }

  async function applySearchFromNext(search) {
    const area = search.area_from || search.area_to
      ? { from: search.area_from, to: search.area_to }
      : search.price_to
        ? areaForPrice(search.price_to, search.offer)
        : { from: null, to: null };
    prefs.offer = search.offer;
    register.querySelectorAll("#offer-seg button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.offer === prefs.offer);
    });
    const place = await localityForQuery(search.q || "Praha");
    prefs.localities = place ? [place] : [];
    prefs.sizes = new Set(sizesForArea(area.from, area.to));
    setAmountFields({
      price_to: search.price_to || (search.offer === "prodej" ? 6000000 : 22000),
      area_from: area.from,
      area_to: area.to,
    });
    renderLocalities();
    renderSizes();
  }

  function applyPrefs(saved) {
    if (!saved) return;
    prefs.offer = saved.offer || "pronajem";
    register.querySelectorAll("#offer-seg button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.offer === prefs.offer);
    });
    prefs.localities = Array.isArray(saved.localities) ? saved.localities : [];
    prefs.sizes = new Set(saved.sizes || ["2+kk", "3+kk"]);
    setAmountFields(saved);
    renderLocalities();
    renderSizes();
  }

  async function saveWatchPrefs() {
    showError("");
    const payload = readPrefsForm();
    if (!payload.localities.length) {
      showError("Vyberte aspoň jednu lokalitu z nápovědy.");
      return false;
    }
    const srSizes = payload.sizes;
    const brCatalog = prefs.catalog?.sources?.bezrealitky?.catalog?.sizes || [];
    const brSizes = srSizes
      .map((key) => {
        const label = (prefs.catalog?.catalog?.sizes || []).find(([id]) => id === key)?.[1] || key;
        return brCatalog.find(([, name]) => name === label)?.[0];
      })
      .filter(Boolean);
    const brOffer = payload.offer === "prodej" ? "PRODEJ" : "PRONAJEM";
    const labels = payload.localities.map((item) => shortLabel(item.label));
    await postJson("/api/settings", { watch_prefs: payload });
    const brFilters = {
      source: "bezrealitky",
      offers: [brOffer],
      estates: ["BYT"],
      sizes: brSizes,
      districts: payload.localities.map((item) => item.id),
      localities: payload.localities,
      osm_value: labels[0] || "",
      price_from: payload.price_from,
      price_to: payload.price_to,
      area_from: payload.area_from,
      area_to: payload.area_to,
      flags: ["includeImports", "includeShortTerm"],
      roommate: [""],
      currency: "CZK",
      location: "exact",
      sort: "TIMEORDER_DESC",
    };
    const brBuilt = await postJson("/api/filters/build", { filters: brFilters, portals: "all" });
    let srBuilt = { url: "" };
    const srDistricts = srealityDistricts(payload.localities);
    if (srDistricts.length) {
      srBuilt = await postJson("/api/filters/build", {
        filters: {
          source: "sreality",
          offers: [payload.offer],
          category: "byty",
          sizes: srSizes,
          districts: srDistricts,
          localities: payload.localities,
          price_from: payload.price_from,
          price_to: payload.price_to,
          area_from: payload.area_from,
          area_to: payload.area_to,
          sort: "nejnovejsi",
        },
        portals: "all",
      });
    }
    const searchUrl = srBuilt.url || brBuilt.url;
    if (!searchUrl) {
      showError("Filtry se nepodařilo převést na hledání.");
      return false;
    }
    const existing = await fetch("/api/monitors").then((res) => res.json());
    const items = existing.items || [];
    const current = items.find((item) => item.id === "default") || items.find((item) => item.enabled) || items[0];
    const kind = payload.offer === "prodej" ? "prodeje" : "pronájmy";
    await postJson("/api/monitors", {
      id: current?.id || "default",
      name: `${labels.join(", ") || "Česko"} ${kind}`,
      search_url: searchUrl,
      portals: "all",
      enabled: true,
    });
    if (searchFromNext()) {
      // Land on the catalog with what was confirmed in this step, not the raw homepage search.
      const params = new URLSearchParams({ offer: payload.offer });
      if (labels[0]) params.set("q", labels[0]);
      if (payload.price_to) params.set("price_to", String(payload.price_to));
      if (payload.area_from) params.set("area_from", String(payload.area_from));
      if (payload.area_to) params.set("area_to", String(payload.area_to));
      refinedNext = `/nabidka?${params.toString()}`;
    }
    return true;
  }

  async function bootPrefs() {
    const [catalogRes, settingsRes] = await Promise.all([fetch("/api/filters/catalog"), fetch("/api/settings")]);
    prefs.catalog = await catalogRes.json();
    const settings = await settingsRes.json().catch(() => ({}));
    renderSizes();
    const fromSearch = searchFromNext();
    if (fromSearch) {
      await applySearchFromNext(fromSearch);
    } else if (settings.watch_prefs && (settings.watch_prefs.localities || []).length) {
      applyPrefs(settings.watch_prefs);
    } else {
      const praha = (prefs.catalog?.sources?.bezrealitky?.catalog?.districts || []).find(([, label]) => label === "Praha");
      if (praha) prefs.localities = [{ id: praha[0], label: praha[1] }];
      renderLocalities();
    }
  }

  // ---- e-mail verification (panel 3, shown between step 1 and step 2) ----
  const VERIFY_PANEL = 3;
  let verifyTimer = 0;
  let resendCooldown = 0;

  function rememberNext() {
    try {
      const next = new URLSearchParams(location.search).get("next");
      if (next) localStorage.setItem("rf_register_next", next);
    } catch (_err) {
      /* storage can be unavailable */
    }
  }

  function setVerifyStatus(text, ok) {
    const el = register.querySelector("#verify-status");
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("is-ok", Boolean(ok));
  }

  async function checkVerified(manual) {
    let profile = null;
    try {
      const res = await fetch("/api/auth/me");
      profile = res.ok ? await res.json() : null;
    } catch (_err) {
      profile = null;
    }
    if (profile && profile.email_verified) {
      clearInterval(verifyTimer);
      setVerifyStatus("E-mail je potvrzený. Pokračujeme…", true);
      setTimeout(() => show(1), 700);
      return true;
    }
    if (manual) setVerifyStatus("Potvrzení jsme zatím nezaznamenali. Klikněte na odkaz v e-mailu.", false);
    return false;
  }

  function showVerify(email, sent) {
    const target = register.querySelector("#verify-email");
    if (target) target.textContent = email || "";
    setVerifyStatus(
      sent ? "Čekám na potvrzení…" : "E-mail se teď nepodařilo odeslat. Zkuste „Poslat odkaz znovu“.",
      false,
    );
    show(VERIFY_PANEL);
    clearInterval(verifyTimer);
    verifyTimer = setInterval(() => checkVerified(false), 4000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) checkVerified(false);
    });
  }

  register.querySelector("#verify-check")?.addEventListener("click", () => checkVerified(true));
  register.querySelector("#verify-resend")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    if (resendCooldown > 0) return;
    btn.disabled = true;
    try {
      await postJson("/api/auth/verify/resend", {});
      toast("Odkaz jsme poslali znovu.", "ok");
      resendCooldown = 45;
      const tick = setInterval(() => {
        resendCooldown -= 1;
        btn.textContent = resendCooldown > 0 ? `Poslat znovu za ${resendCooldown} s` : "Poslat odkaz znovu";
        if (resendCooldown <= 0) {
          clearInterval(tick);
          btn.disabled = false;
        }
      }, 1000);
    } catch (err) {
      toast(err.message || "Odkaz se nepodařilo poslat.");
      btn.disabled = false;
    }
  });

  // Returning from the e-mail link (?resume=1) or from a login with an unconfirmed account (?verify=1).
  (async () => {
    const params = new URLSearchParams(location.search);
    if (!params.get("resume") && !params.get("verify")) return;
    let profile = null;
    try {
      const res = await fetch("/api/auth/me");
      profile = res.ok ? await res.json() : null;
    } catch (_err) {
      profile = null;
    }
    if (!profile) return;
    if (profile.email_verified) show(1);
    else showVerify(profile.email, true);
  })();

  register.querySelector("#reg-step1").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = register.querySelector("#reg-password");
    const pw2 = register.querySelector("#reg-password2");
    if (!passwordsMatch(pw, pw2, { toastOnMismatch: true })) {
      pw2.focus();
      return;
    }
    if (!register.querySelector("#reg-agree").checked) return;
    const btn = register.querySelector('#reg-step1 button[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      // Create the account right away so steps 2 and 3 run under the new session.
      const promo = promoFromUrl() || readStoredPromo();
      const created = await postJson("/api/auth/register", {
        name: register.querySelector("#name")?.value.trim() || "",
        email: register.querySelector("#email")?.value.trim() || "",
        password: pw.value,
        promo,
      });
      rememberNext();
      if (created && created.email_verified === false) {
        showVerify(created.email, created.verification_sent !== false);
      } else {
        show(1);
      }
    } catch (err) {
      toast(err.message || "Registrace se nepovedla");
    } finally {
      if (btn) btn.disabled = false;
    }
  });
  register.querySelector("#to-step3").addEventListener("click", async () => {
    try {
      if (await saveWatchPrefs()) show(2);
    } catch (err) {
      showError(err.message || "Filtry se nepodařilo uložit.");
    }
  });
  register.querySelector("#skip-prefs").addEventListener("click", () => show(2));
  async function saveNotifyChoices() {
    const on = (kind) => Boolean(register.querySelector(`[data-kind="${kind}"]`)?.classList.contains("on"));
    try {
      const current = await fetch("/api/settings").then((res) => res.json()).catch(() => ({}));
      const freq = register.querySelector(".freq-row button.on")?.textContent.trim() || "Okamžitě";
      await postJson("/api/settings", {
        notify: {
          ...(current.notify || {}),
          email: on("email"),
          push: on("push"),
          ntDigest: on("digest"),
          instant: freq === "Okamžitě",
        },
      });
    } catch (_err) {
      // Preferences can be changed later in the settings; never block finishing the sign-up.
    }
  }

  register.querySelector("#finish-reg").addEventListener("click", async () => {
    const promo = register.querySelector("#reg-promo")?.value.trim() || "";
    const btn = register.querySelector("#finish-reg");
    if (btn) btn.disabled = true;
    try {
      // The account already exists (created in step 1); just store the promo code.
      if (promo) await postJson("/api/billing/promo", { code: promo });
      writeStoredPromo(promo);
      await saveNotifyChoices();
      const next = refinedNext || new URLSearchParams(location.search).get("next") || "/nabidka";
      location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/nabidka";
    } catch (err) {
      toast(err.message || "Dokončení se nepovedlo");
      if (btn) btn.disabled = false;
    }
  });
  bindChoices(register, "#offer-seg button", false);
  bindChoices(register, ".freq-row button", false);
  bindToggles(register);

  chipsEl?.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove-city]");
    if (remove) {
      prefs.localities = prefs.localities.filter((item) => item.id !== remove.dataset.removeCity);
      renderLocalities();
      return;
    }
    const add = event.target.closest("[data-add-city]");
    if (add) addLocality(add.dataset.addCity, add.dataset.label);
  });
  sizesEl?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-size]");
    if (!btn) return;
    const key = btn.dataset.size;
    if (prefs.sizes.has(key)) prefs.sizes.delete(key);
    else prefs.sizes.add(key);
    btn.classList.toggle("on");
  });
  cityInput?.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const q = cityInput.value.trim();
    suggestTimer = window.setTimeout(() => {
      searchCities(q).catch(() => hideSuggest());
    }, 220);
  });
  cityInput?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideSuggest();
  });
  suggestEl?.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-suggest]");
    if (!btn) return;
    const item = suggestItems[Number(btn.dataset.suggest)];
    if (item) {
      addLocality(item.id, item.label, item.sreality);
      cityInput.value = "";
      hideSuggest();
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".city-field")) hideSuggest();
  });

  bootPrefs().catch(() => {
    renderSizes();
    renderLocalities();
  });
}

window.authIcon = icon;
