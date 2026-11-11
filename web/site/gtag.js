// Google tag (GA4 G-WGP2CCXNBD) + PostHog (EU, session replay), řízeno souhlasem z cookies.js (rf_consent).
// Žádný skript třetí strany se nenačte, dokud návštěvník výslovně nesouhlasí s analytikou/marketingem.
(() => {
  const ID = "G-WGP2CCXNBD";
  const PH_KEY = "phc_kqvGH3j3r3vhdkoToEHoSd8oe88PL9iur7ZvqU7P3rU5";
  function consent() {
    const raw = document.cookie.split("; ").find((p) => p.startsWith("rf_consent="));
    let a = false, m = false; // bez výslovného souhlasu se nesleduje
    if (raw) {
      try {
        const d = JSON.parse(decodeURIComponent(raw.slice(11)));
        a = d.a === 1 || d.analytics === true;
        m = d.m === 1 || d.marketing === true;
      } catch {}
    }
    return { a, m };
  }
  const c = consent();
  if (!/^\/admin(\/|$)/.test(location.pathname)) {
    const cb = document.createElement("script");
    cb.src = "/static/site/consent-banner.js?v=5";
    cb.defer = true;
    document.head.appendChild(cb);
  }
  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  gtag("consent", "default", {
    analytics_storage: c.a ? "granted" : "denied",
    ad_storage: c.m ? "granted" : "denied",
    ad_user_data: c.m ? "granted" : "denied",
    ad_personalization: c.m ? "granted" : "denied",
  });
  gtag("js", new Date());
  gtag("config", ID);

  let gaLoaded = false;
  let phLoaded = false;

  function loadGa() {
    if (gaLoaded) return;
    gaLoaded = true;
    const s = document.createElement("script");
    s.async = true;
    s.src = "https://www.googletagmanager.com/gtag/js?id=" + ID;
    document.head.appendChild(s);
  }

  // PostHog – nahrávání relací jen při souhlasu s analytikou, vstupy maskované.
  function loadPh() {
    if (phLoaded) return;
    phLoaded = true;
    const ph = document.createElement("script");
    ph.async = true;
    ph.crossOrigin = "anonymous";
    ph.src = "https://eu-assets.i.posthog.com/static/array.js";
    ph.onload = () => {
      if (!window.posthog || !posthog.init) return;
      posthog.init(PH_KEY, {
        api_host: "https://eu.i.posthog.com",
        defaults: "2025-05-24",
        person_profiles: "identified_only",
        session_recording: { maskAllInputs: true },
      });
      if (posthog.opt_in_capturing) posthog.opt_in_capturing();
    };
    document.head.appendChild(ph);
  }

  // Po odvolání souhlasu smažeme identifikátory analytiky.
  function purge() {
    try {
      document.cookie.split("; ").forEach((p) => {
        const name = p.split("=")[0];
        if (/^(ph_|_ga|_gid|_gat)/.test(name)) {
          document.cookie = `${name}=; path=/; max-age=0; SameSite=Lax`;
        }
      });
      Object.keys(localStorage).filter((k) => /^(ph_|__ph)/.test(k)).forEach((k) => localStorage.removeItem(k));
    } catch {}
  }

  if (c.a || c.m) loadGa();
  if (c.a) loadPh();

  window.rfGtagConsent = function (a, m) {
    gtag("consent", "update", {
      analytics_storage: a ? "granted" : "denied",
      ad_storage: m ? "granted" : "denied",
      ad_user_data: m ? "granted" : "denied",
      ad_personalization: m ? "granted" : "denied",
    });
    if (a || m) loadGa();
    if (a) loadPh();
    else {
      if (window.posthog && posthog.opt_out_capturing) posthog.opt_out_capturing();
      purge();
    }
  };
})();
