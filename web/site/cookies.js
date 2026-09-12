(() => {
  const KEY = "rf_consent";
  const YEAR = 60 * 60 * 24 * 365;
  const FUNCTIONAL_KEYS = ["nabidka-views", "nabidka-circle", "realitify-notify", "realitify-profile"];

  const defaults = { analytics: true, marketing: false, functional: true };

  function readConsent() {
    const raw = document.cookie.split("; ").find((part) => part.startsWith(`${KEY}=`));
    if (!raw) return { ...defaults };
    try {
      const data = JSON.parse(decodeURIComponent(raw.slice(KEY.length + 1)));
      return {
        analytics: data.a !== 0 && data.analytics !== false,
        marketing: data.m === 1 || data.marketing === true,
        functional: data.f !== 0 && data.functional !== false,
      };
    } catch {
      return { ...defaults };
    }
  }

  function writeConsent(consent) {
    const payload = encodeURIComponent(JSON.stringify({ a: consent.analytics ? 1 : 0, m: consent.marketing ? 1 : 0, f: consent.functional ? 1 : 0 }));
    document.cookie = `${KEY}=${payload}; path=/; max-age=${YEAR}; SameSite=Lax`;
  }

  function applyToggle(button, on) {
    button.classList.toggle("is-off", !on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function currentFromUi() {
    const analytics = document.querySelector('[data-key="analytics"]');
    const marketing = document.querySelector('[data-key="marketing"]');
    const functional = document.querySelector('[data-key="functional"]');
    return {
      analytics: analytics ? analytics.getAttribute("aria-pressed") === "true" : true,
      marketing: marketing ? marketing.getAttribute("aria-pressed") === "true" : false,
      functional: functional ? functional.getAttribute("aria-pressed") === "true" : true,
    };
  }

  function setUi(consent) {
    document.querySelectorAll(".cookie-toggle[data-key]").forEach((button) => {
      const key = button.dataset.key;
      if (key === "necessary") return;
      applyToggle(button, Boolean(consent[key]));
    });
  }

  function clearAnalyticsCookie() {
    document.cookie = "rf_vid=; path=/; max-age=0; SameSite=Lax";
    document.cookie = "rf_vid=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  }

  function clearFunctional() {
    FUNCTIONAL_KEYS.forEach((name) => localStorage.removeItem(name));
  }

  function save(consent, message) {
    writeConsent(consent);
    if (!consent.analytics) clearAnalyticsCookie();
    if (!consent.functional) clearFunctional();
    const note = document.getElementById("cookie-saved");
    if (note) note.textContent = message;
  }

  const saved = readConsent();
  setUi(saved);

  document.querySelectorAll(".cookie-toggle[data-key]").forEach((button) => {
    if (button.dataset.key === "necessary") return;
    button.addEventListener("click", () => {
      applyToggle(button, button.classList.contains("is-off"));
    });
  });

  document.getElementById("cookie-accept-all")?.addEventListener("click", () => {
    const consent = { analytics: true, marketing: true, functional: true };
    setUi(consent);
    save(consent, "Přijali jste všechny kategorie.");
  });

  document.getElementById("cookie-save")?.addEventListener("click", () => {
    save(currentFromUi(), "Nastavení cookies je uložené.");
  });
})();
