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

function bindToggles(root) {
  root.querySelectorAll("[data-toggle]").forEach((el) => {
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
      await postJson("/api/auth/login", { email, password });
      const next = new URLSearchParams(location.search).get("next") || "/prehled";
      location.href = next.startsWith("/oauth/authorize") ? next : "/prehled";
    } catch (err) {
      toast(err.message || "Přihlášení se nepovedlo");
    }
  });
}

const forgotForm = document.querySelector("#forgot-form");
if (forgotForm) {
  forgotForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const email = forgotForm.querySelector("input[type=email]").value.trim();
    const ok = document.querySelector("#forgot-success");
    forgotForm.hidden = true;
    ok.hidden = false;
    ok.querySelector("[data-email]").textContent = email;
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
      price_from: parseAmount(register.querySelector("#price-min")?.value),
      price_to: parseAmount(register.querySelector("#price-max")?.value),
      area_from: parseAmount(register.querySelector("#area")?.value),
    };
  }

  function applyPrefs(saved) {
    if (!saved) return;
    prefs.offer = saved.offer || "pronajem";
    register.querySelectorAll("#offer-seg button").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.offer === prefs.offer);
    });
    prefs.localities = Array.isArray(saved.localities) ? saved.localities : [];
    prefs.sizes = new Set(saved.sizes || ["2+kk", "3+kk"]);
    if (register.querySelector("#price-min")) {
      register.querySelector("#price-min").value = saved.price_from ? String(saved.price_from) : "";
    }
    if (register.querySelector("#price-max")) {
      register.querySelector("#price-max").value = saved.price_to ? String(saved.price_to) : "22000";
    }
    if (register.querySelector("#area")) {
      register.querySelector("#area").value = saved.area_from ? String(saved.area_from) : "45";
    }
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
    return true;
  }

  async function bootPrefs() {
    const [catalogRes, settingsRes] = await Promise.all([fetch("/api/filters/catalog"), fetch("/api/settings")]);
    prefs.catalog = await catalogRes.json();
    const settings = await settingsRes.json().catch(() => ({}));
    renderSizes();
    if (settings.watch_prefs && (settings.watch_prefs.localities || []).length) {
      applyPrefs(settings.watch_prefs);
    } else {
      const praha = (prefs.catalog?.sources?.bezrealitky?.catalog?.districts || []).find(([, label]) => label === "Praha");
      if (praha) prefs.localities = [{ id: praha[0], label: praha[1] }];
      renderLocalities();
    }
  }

  let pendingAccount = null;

  register.querySelector("#reg-step1").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = register.querySelector("#reg-password");
    const pw2 = register.querySelector("#reg-password2");
    if (!passwordsMatch(pw, pw2, { toastOnMismatch: true })) {
      pw2.focus();
      return;
    }
    if (!register.querySelector("#reg-agree").checked) return;
    pendingAccount = {
      name: register.querySelector("#name")?.value.trim() || "",
      email: register.querySelector("#email")?.value.trim() || "",
      password: pw.value,
    };
    show(1);
  });
  register.querySelector("#to-step3").addEventListener("click", async () => {
    try {
      if (await saveWatchPrefs()) show(2);
    } catch (err) {
      showError(err.message || "Filtry se nepodařilo uložit.");
    }
  });
  register.querySelector("#skip-prefs").addEventListener("click", () => show(2));
  register.querySelector("#finish-reg").addEventListener("click", async () => {
    const promo = register.querySelector("#reg-promo")?.value.trim() || "";
    if (!pendingAccount?.email || !pendingAccount?.password) {
      toast("Nejdřív vyplňte jméno, e-mail a heslo v prvním kroku.");
      show(0);
      return;
    }
    try {
      const me = await fetch("/api/auth/me");
      if (me.ok) {
        if (promo) await postJson("/api/billing/promo", { code: promo });
      } else {
        try {
          await postJson("/api/auth/register", { ...pendingAccount, promo });
        } catch (err) {
          try {
            await postJson("/api/auth/login", {
              email: pendingAccount.email,
              password: pendingAccount.password,
            });
            if (promo) await postJson("/api/billing/promo", { code: promo });
          } catch {
            throw err;
          }
        }
      }
      writeStoredPromo(promo);
      location.href = "/nabidka";
    } catch (err) {
      toast(err.message || "Registrace se nepovedla");
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
