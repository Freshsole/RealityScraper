// Cookie banner: zobrazí se, dokud návštěvník nevybere (cookie rf_consent). Načítá ho gtag.js.
(() => {
  const KEY = "rf_consent";
  const YEAR = 60 * 60 * 24 * 365;
  const p = location.pathname;
  if (/^\/admin(\/|$)/.test(p) || /^\/(nastaveni-cookies|cookies)(\/|$)/.test(p)) return;
  if (document.cookie.split("; ").some((c) => c.startsWith(KEY + "="))) return;

  const css = `
.rf-cc{position:fixed;left:16px;bottom:16px;z-index:2147483000;width:min(640px,calc(100vw - 32px));box-sizing:border-box;
  background:#fff;color:#1a1a2e;border-radius:24px;box-shadow:0 12px 48px rgba(20,24,40,.22),0 0 0 1px rgba(20,24,40,.06);
  padding:28px 200px 28px 32px;font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;
  animation:rf-cc-in .35s ease-out both}
.rf-cc *{box-sizing:border-box}
.rf-cc h2{margin:0 0 10px;font-size:30px;line-height:1.1;font-weight:800;letter-spacing:-.02em;color:#1a1a2e}
.rf-cc p{margin:0 0 20px;font-size:15px;line-height:1.55;color:#4b5563}
.rf-cc p a{color:#1a1a2e;font-weight:700;text-decoration:underline;text-underline-offset:3px}
.rf-cc-btns{display:flex;gap:10px;flex-wrap:wrap}
.rf-cc button,.rf-cc .rf-cc-pref{appearance:none;border:0;cursor:pointer;font:inherit;font-size:14px;font-weight:700;
  height:44px;padding:0 22px;border-radius:12px;display:inline-flex;align-items:center;text-decoration:none;transition:filter .15s}
.rf-cc-ok,.rf-cc-no{background:#1a1a2e;color:#fff}
.rf-cc .rf-cc-pref{background:#e9e9ee;color:#1a1a2e}
.rf-cc button:hover,.rf-cc .rf-cc-pref:hover{filter:brightness(1.12)}
.rf-cc button:focus-visible,.rf-cc .rf-cc-pref:focus-visible{outline:2px solid #1a1a2e;outline-offset:2px}
.rf-cc-dog{position:absolute;right:8px;bottom:0;height:calc(100% + 56px);width:auto;max-width:190px;object-fit:contain;
  object-position:bottom;pointer-events:none;user-select:none}
.rf-cc-list{margin:0 0 20px;padding:0;list-style:none;display:grid;gap:12px}
.rf-cc-list li{display:flex;align-items:center;justify-content:space-between;gap:16px}
.rf-cc-list strong{display:block;font-size:15px;color:#1a1a2e}
.rf-cc-list span{display:block;font-size:13px;line-height:1.4;color:#6b7280}
.rf-cc .rf-cc-sw{flex:none;width:44px;height:26px;padding:0;border-radius:999px;background:#c9ccd4;position:relative;transition:background .15s}
.rf-cc .rf-cc-sw:after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:transform .15s}
.rf-cc .rf-cc-sw[aria-checked=true]{background:#1a1a2e}
.rf-cc .rf-cc-sw[aria-checked=true]:after{transform:translateX(18px)}
.rf-cc .rf-cc-sw:disabled{opacity:.5;cursor:not-allowed}
.rf-cc[data-view=prefs] .rf-cc-intro,.rf-cc:not([data-view=prefs]) .rf-cc-prefs{display:none}
@keyframes rf-cc-in{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
@media (max-width:560px){
  .rf-cc{left:0;right:0;bottom:0;width:auto;padding:14px 16px calc(34px + env(safe-area-inset-bottom));border-radius:18px 18px 0 0}
  .rf-cc h2{font-size:17px;margin:0 0 4px}
  .rf-cc-dog{display:none}
  .rf-cc p{font-size:13px;line-height:1.45;margin:0 0 12px}
  .rf-cc-btns{flex-wrap:nowrap;gap:8px}
  .rf-cc button,.rf-cc .rf-cc-pref{flex:1 1 0;justify-content:center;padding:0 8px;font-size:13px;height:44px;min-width:0}
  .rf-cc-list{gap:8px;margin-bottom:12px}
  .rf-cc-list span{font-size:12px}
  .rf-cc[data-view=prefs]{max-height:80vh;overflow:auto}
}
@media (prefers-reduced-motion:reduce){.rf-cc{animation:none}}`;

  function write(a, m, f) {
    const v = encodeURIComponent(JSON.stringify({ a: a ? 1 : 0, m: m ? 1 : 0, f: f ? 1 : 0 }));
    document.cookie = `${KEY}=${v}; path=/; max-age=${YEAR}; SameSite=Lax`;
    if (window.rfGtagConsent) window.rfGtagConsent(a, m);
  }

  function mount() {
    if (document.getElementById("rf-cc")) return;
    const st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    const box = document.createElement("div");
    box.className = "rf-cc";
    box.id = "rf-cc";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-live", "polite");
    box.setAttribute("aria-labelledby", "rf-cc-title");
    const row = (k, t, d, on, lock) =>
      `<li><div><strong>${t}</strong><span>${d}</span></div>` +
      `<button type="button" class="rf-cc-sw" role="switch" data-k="${k}" aria-label="${t}" aria-checked="${on}"${lock ? " disabled" : ""}></button></li>`;
    box.innerHTML =
      '<h2 id="rf-cc-title">Cookies</h2>' +
      '<div class="rf-cc-intro"><p>Používáme cookies, abychom vás udrželi přihlášené, zapamatovali si vaše nastavení a vylepšovali Realitify. ' +
      'Volitelné cookies (analytika, marketing) zapneme jen s vaším souhlasem. Více v <a href="/ochrana-soukromi">zásadách cookies</a>.</p>' +
      '<div class="rf-cc-btns"><button type="button" class="rf-cc-ok">Přijmout</button>' +
      '<button type="button" class="rf-cc-no">Odmítnout</button>' +
      '<button type="button" class="rf-cc-pref">Nastavení</button></div></div>' +
      '<div class="rf-cc-prefs"><ul class="rf-cc-list">' +
      row("necessary", "Nezbytné", "Přihlášení a bezpečnost, bez nich web nefunguje.", true, true) +
      row("analytics", "Analytické", "Anonymní statistiky návštěvnosti a nahrávání relací.", false) +
      row("marketing", "Marketingové", "Měření reklam a personalizace.", false) +
      row("functional", "Funkční", "Pamatuje si filtry, profil a nastavení oznámení.", false) +
      '</ul><div class="rf-cc-btns"><button type="button" class="rf-cc-save rf-cc-ok">Uložit nastavení</button>' +
      '<button type="button" class="rf-cc-pref rf-cc-all">Přijmout vše</button></div></div>' +
      '<picture><source srcset="/static/site/assets/cookie-dog.webp" type="image/webp" />' +
      '<img class="rf-cc-dog" src="/static/site/assets/cookie-dog.png" width="483" height="640" alt="" decoding="async" /></picture>';
    document.body.appendChild(box);
    const done = (a, m, f) => {
      write(a, m, f);
      if (!a) {
        document.cookie = "rf_vid=; path=/; max-age=0; SameSite=Lax";
      }
      if (!f) ["nabidka-views", "nabidka-circle", "realitify-notify", "realitify-profile"].forEach((n) => { try { localStorage.removeItem(n); } catch {} });
      box.remove();
    };
    box.querySelectorAll(".rf-cc-sw:not(:disabled)").forEach((sw) =>
      sw.addEventListener("click", () => sw.setAttribute("aria-checked", sw.getAttribute("aria-checked") === "true" ? "false" : "true")));
    const val = (k) => box.querySelector(`.rf-cc-sw[data-k="${k}"]`).getAttribute("aria-checked") === "true";
    box.querySelector(".rf-cc-intro .rf-cc-ok").addEventListener("click", () => done(true, true, true));
    box.querySelector(".rf-cc-intro .rf-cc-no").addEventListener("click", () => done(false, false, false));
    box.querySelector(".rf-cc-intro .rf-cc-pref").addEventListener("click", () => {
      box.dataset.view = "prefs";
      box.querySelector(".rf-cc-save").focus();
    });
    box.querySelector(".rf-cc-save").addEventListener("click", () => done(val("analytics"), val("marketing"), val("functional")));
    box.querySelector(".rf-cc-all").addEventListener("click", () => done(true, true, true));
  }

  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
