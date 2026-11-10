/* Chyby z frontendu se uživateli neukazují. Odcházejí přes backend (/api/public/client-error) na Discord webhook,
   jehož URL je jen v env. Backend sám doplní pseudonym uživatele (ID, role, tarif) bez e-mailu, jména a IP. */
(function () {
  if (window.reportClientError) return;
  var ENDPOINT = "/api/public/client-error";
  var seen = {};
  var crumbs = [];
  var SENSITIVE = /token|code|key|email|pass|secret|session/i;

  function crumb(text) {
    var t = new Date().toISOString().slice(11, 19);
    crumbs.push(t + " " + String(text).slice(0, 120));
    if (crumbs.length > 16) crumbs.shift();
  }
  function path(url) {
    try {
      var u = new URL(url, location.href);
      return u.pathname;
    } catch (_) { return String(url || "").split("?")[0]; }
  }
  function queryKeys() {
    try {
      return Array.from(new URLSearchParams(location.search).keys())
        .map(function (k) { return SENSITIVE.test(k) ? k + "=[skryto]" : k; }).join(", ");
    } catch (_) { return ""; }
  }
  function appVersion() {
    var el = document.querySelector('script[src*="app.js"]');
    var m = el && /[?&]v=([^&]+)/.exec(el.getAttribute("src") || "");
    return m ? "assets " + m[1].slice(0, 12) : "";
  }
  function describe(el) {
    if (!el || !el.tagName) return "";
    var tag = el.tagName.toLowerCase();
    var out = tag;
    if (el.id) out += "#" + el.id;
    var cls = typeof el.className === "string" ? el.className.trim().split(/\s+/).slice(0, 3).join(".") : "";
    if (cls) out += "." + cls;
    var label = el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("data-action") || el.getAttribute("href"));
    if (label) out += " [" + path(label).slice(0, 50) + "]";
    else if (tag === "button" && el.textContent) out += " “" + el.textContent.trim().slice(0, 30) + "”";
    return out;
  }

  function report(message, source, detail, request) {
    try {
      var text = String(message || "").trim();
      if (!text) return;
      var key = (source || "") + "|" + text + "|" + location.pathname;
      if (seen[key]) return;
      seen[key] = 1;
      fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: text,
          source: source || "site",
          detail: String(detail || ""),
          page: location.pathname,
          query_keys: queryKeys(),
          hash: location.hash || "",
          referrer: document.referrer ? path(document.referrer) : "",
          viewport: window.innerWidth + "×" + window.innerHeight + " @" + (window.devicePixelRatio || 1) + "x",
          lang: navigator.language || "",
          tz: (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || "",
          online: navigator.onLine,
          app_version: appVersion(),
          request: request || null,
          crumbs: crumbs.slice(),
        }),
        keepalive: true,
        __reporter: true,
      }).catch(function () {});
    } catch (_) {}
  }
  window.reportClientError = report;

  window.addEventListener("error", function (ev) {
    var where = (ev.filename || "") + ":" + (ev.lineno || 0) + ":" + (ev.colno || 0);
    report(ev.message || "Neošetřená chyba", "window.onerror", ((ev.error && ev.error.stack) || "") + "\n@ " + where);
  });
  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev.reason;
    report((r && r.message) || String(r || "Neošetřený promise"), "unhandledrejection", (r && r.stack) || "");
  });
  document.addEventListener("click", function (ev) {
    var el = ev.target && ev.target.closest ? ev.target.closest("a,button,[role=button],input,select,label,summary") : null;
    crumb("klik " + describe(el || ev.target));
  }, true);
  document.addEventListener("submit", function (ev) { crumb("odeslán formulář " + describe(ev.target)); }, true);
  ["pushState", "replaceState"].forEach(function (name) {
    var orig = history[name];
    history[name] = function (state, title, url) {
      crumb("navigace " + path(url || location.href));
      return orig.apply(this, arguments);
    };
  });
  window.addEventListener("popstate", function () { crumb("navigace zpět/vpřed " + location.pathname); });

  /* fetch: každé volání jde do stop; 5xx a výpadky sítě se hlásí jako chyba i s odpovědí serveru */
  var origFetch = window.fetch;
  if (typeof origFetch === "function") {
    window.fetch = function (input, init) {
      if (init && init.__reporter) return origFetch.apply(this, arguments);
      var url = typeof input === "string" ? input : (input && input.url) || "";
      var method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
      var p = path(url);
      var started = Date.now();
      var same = !/^https?:\/\//i.test(url) || url.indexOf(location.origin) === 0;
      return origFetch.apply(this, arguments).then(function (res) {
        var ms = Date.now() - started;
        if (same) crumb(method + " " + p + " → " + res.status + " (" + ms + " ms)");
        if (same && res.status >= 500) {
          res.clone().text().then(function (body) {
            report("API " + res.status + " " + method + " " + p, "api", "", { method: method, path: p, status: res.status, ms: ms, body: body.slice(0, 500) });
          }).catch(function () {
            report("API " + res.status + " " + method + " " + p, "api", "", { method: method, path: p, status: res.status, ms: ms, body: "" });
          });
        }
        return res;
      }, function (err) {
        var ms = Date.now() - started;
        if (same) {
          crumb(method + " " + p + " → síťová chyba");
          report("Síťová chyba " + method + " " + p + ": " + ((err && err.message) || "fetch selhal"), "fetch", (err && err.stack) || "", { method: method, path: p, status: "síť", ms: ms, body: "" });
        }
        throw err;
      });
    };
  }
  crumb("načtena stránka " + location.pathname);
})();
