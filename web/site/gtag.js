// Google tag (gtag.js) – GA4 G-WGP2CCXNBD, řízeno souhlasem z cookies.js (rf_consent).
(() => {
  const ID = "G-WGP2CCXNBD";
  function consent() {
    const raw = document.cookie.split("; ").find((p) => p.startsWith("rf_consent="));
    let a = true, m = false;
    if (raw) {
      try {
        const d = JSON.parse(decodeURIComponent(raw.slice(11)));
        a = d.a !== 0 && d.analytics !== false;
        m = d.m === 1 || d.marketing === true;
      } catch {}
    }
    return { a, m };
  }
  const c = consent();
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
  const s = document.createElement("script");
  s.async = true;
  s.src = "https://www.googletagmanager.com/gtag/js?id=" + ID;
  document.head.appendChild(s);
  window.rfGtagConsent = function (a, m) {
    gtag("consent", "update", {
      analytics_storage: a ? "granted" : "denied",
      ad_storage: m ? "granted" : "denied",
      ad_user_data: m ? "granted" : "denied",
      ad_personalization: m ? "granted" : "denied",
    });
  };
})();
