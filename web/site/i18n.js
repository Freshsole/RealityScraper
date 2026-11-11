/* Automatic translation of the whole UI (Czech source -> sk, uk, en, ru, vi, de).
 *
 * Loaded on every public/app HTML page (injected by the server, see inject_i18n in app/main.py).
 * Nothing is translated by hand: every Czech text node / attribute on the page, including anything
 * rendered later by other scripts, is sent to POST /api/i18n/translate (cached server side) and swapped
 * in place. New copy added to the product is therefore translated automatically.
 *
 * Language: saved choice (localStorage rf_lang) > browser language(s) > Czech.
 * Opt out per element with translate="no" (or class "notranslate").
 */
(function () {
  "use strict";
  if (window.__rfI18n) return;

  var LANGS = [
    { code: "cs", name: "Čeština" },
    { code: "sk", name: "Slovenčina" },
    { code: "uk", name: "Українська" },
    { code: "en", name: "English" },
    { code: "ru", name: "Русский" },
    { code: "vi", name: "Tiếng Việt" },
    { code: "de", name: "Deutsch" },
  ];
  var CODES = LANGS.map(function (l) { return l.code; });
  var STORE_KEY = "rf_lang";
  var CACHE_PREFIX = "rf_i18n_v2_";
  var ATTRS = ["placeholder", "title", "aria-label", "alt"];
  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, CODE: 1, PRE: 1, TEXTAREA: 1, SVG: 1, IFRAME: 1, CANVAS: 1 };
  /* Data coming from portals (addresses, titles, amenities) is content, not UI. */
  var SKIP_SELECTOR = [
    "[translate=no]", ".notranslate", "[contenteditable=true]", ".rf-lang",
    ".offer-card h3", ".offer-kicker", ".offer-amenities", ".listing h3", ".listing .meta",
    ".brand", ".footer-mark", ".mock-card:not(.is-sold)", ".maplibregl-map", ".leaflet-container", ".aic-msg", ".aic-card-title", ".aic-meta",
  ].join(",");
  var NUM_RE = /\d+(?:[  ]\d{3})*(?:[.,]\d+)?/g;

  var tryJson = function (s) { try { return JSON.parse(s); } catch (e) { return null; } };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };

  function detect() {
    var saved = store.get(STORE_KEY);
    if (saved && CODES.indexOf(saved) >= 0) return saved;
    var prefs = (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || ""]);
    for (var i = 0; i < prefs.length; i++) {
      var base = String(prefs[i] || "").toLowerCase().split("-")[0];
      if (base === "ua") base = "uk";
      if (CODES.indexOf(base) >= 0) return base;
    }
    return "cs";
  }

  var lang = detect();
  var docEl = document.documentElement;
  var cache = {}; // key -> translation for the current language
  var inflight = {}; // key -> true
  var failed = {}; // key -> true (server could not translate, stay Czech)
  var unitOf = new WeakMap(); // text node -> unit (single node or a group of nodes forming one sentence)
  var elUnits = new WeakMap(); // element -> {attrName: unit} for attributes / meta
  var tracked = []; // WeakRef(unit), re-applied on language switch
  var pending = {}; // key -> template string waiting to be sent
  var timer = 0;
  var observer = null;
  var started = false;
  var revealed = false;

  // ---------- hashing (must match server key_for: sha1 hex, first 20 chars) ----------
  function sha1(str) {
    function rotl(n, s) { return (n << s) | (n >>> (32 - s)); }
    var bytes = unescape(encodeURIComponent(str));
    var words = [];
    var i;
    for (i = 0; i < bytes.length; i++) words[i >> 2] |= bytes.charCodeAt(i) << (24 - (i % 4) * 8);
    var bitLen = bytes.length * 8;
    words[bitLen >> 5] |= 0x80 << (24 - (bitLen % 32));
    words[(((bitLen + 64) >> 9) << 4) + 15] = bitLen;
    var h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
    var w = new Array(80);
    for (var j = 0; j < words.length; j += 16) {
      var a = h0, b = h1, c = h2, d = h3, e = h4;
      for (i = 0; i < 80; i++) {
        w[i] = i < 16 ? (words[j + i] | 0) : rotl(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
        var f, k;
        if (i < 20) { f = (b & c) | (~b & d); k = 0x5a827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ed9eba1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc; }
        else { f = b ^ c ^ d; k = 0xca62c1d6; }
        var t = (rotl(a, 5) + f + e + k + w[i]) | 0;
        e = d; d = c; c = rotl(b, 30); b = a; a = t;
      }
      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0; h2 = (h2 + c) | 0; h3 = (h3 + d) | 0; h4 = (h4 + e) | 0;
    }
    return [h0, h1, h2, h3, h4].map(function (x) { return ("00000000" + (x >>> 0).toString(16)).slice(-8); }).join("");
  }
  function keyOf(tpl) { return sha1(tpl).slice(0, 20); }

  // ---------- text <-> template ----------
  // Numbers become {0},{1}..; boundaries between text nodes of one sentence become {#1},{#2}..
  var INLINE = { A:1, B:1, I:1, EM:1, STRONG:1, SPAN:1, SMALL:1, MARK:1, U:1, SUP:1, SUB:1, BR:1, S:1, ABBR:1, LABEL:1, TIME:1, SVG:1, IMG:1, WBR:1 };
  function buildTemplate(czArr) {
    var nums = [];
    var parts = czArr.map(function (c) {
      return c.trim().replace(NUM_RE, function (m) { nums.push(m); return "{" + (nums.length - 1) + "}"; });
    });
    var tpl = parts.reduce(function (acc, part, i) { return i ? acc + "{#" + i + "}" + part : part; }, "");
    return { tpl: tpl, nums: nums };
  }
  function fill(tpl, nums) {
    return tpl.replace(/\{(\d+)\}/g, function (m, i) { return nums[+i] != null ? nums[+i] : m; });
  }
  function splitTr(tr, n) {
    var markers = tr.match(/\{#\d+\}/g) || [];
    if (markers.length !== n - 1) return null;
    for (var i = 0; i < markers.length; i++) if (markers[i] !== "{#" + (i + 1) + "}") return null;
    return tr.split(/\{#\d+\}/);
  }
  function worthTranslating(tpl) {
    var letters = tpl.replace(/\{#?\d+\}/g, "").match(/\p{L}/gu);
    return !!letters && letters.length >= 2 && tpl.length <= 1500;
  }

  // ---------- persistent client cache ----------
  function loadCache() {
    cache = tryJson(store.get(CACHE_PREFIX + lang) || "{}") || {};
  }
  var saveTimer = 0;
  function saveCache() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var keys = Object.keys(cache);
      if (keys.length > 4000) { // keep it bounded
        var trimmed = {};
        keys.slice(-3000).forEach(function (k) { trimmed[k] = cache[k]; });
        cache = trimmed;
      }
      store.set(CACHE_PREFIX + lang, JSON.stringify(cache));
    }, 400);
  }

  // ---------- DOM helpers ----------
  function skipped(el) {
    for (var n = el; n && n.nodeType === 1; n = n.parentNode) {
      if (SKIP_TAGS[n.tagName && n.tagName.toUpperCase()]) return true;
      if (n.matches && n.matches(SKIP_SELECTOR)) return true;
    }
    return false;
  }

  function wrapWs(cz, inner) {
    var lead = (cz.match(/^\s*/) || [""])[0];
    var trail = (cz.match(/\s*$/) || [""])[0];
    return lead + inner + trail;
  }

  function getVal(u, i) {
    if (u.t === "text") return u.nodes[i].nodeValue;
    return u.el.getAttribute(u.t === "meta" ? "content" : u.name);
  }
  function setVal(u, i, v) {
    if (u.t === "text") u.nodes[i].nodeValue = v;
    else u.el.setAttribute(u.t === "meta" ? "content" : u.name, v);
  }

  function makeUnit(t, cz, extra) {
    var b = buildTemplate(cz);
    var u = { t: t, cz: cz, tpl: b.tpl, nums: b.nums, applied: [], dead: false };
    for (var k in extra) u[k] = extra[k];
    tracked.push(new WeakRef(u));
    return u;
  }

  function dissolve(u) {
    u.dead = true;
    if (u.t === "text") {
      u.nodes.forEach(function (n, i) {
        if (unitOf.get(n) === u) unitOf.delete(n);
        if (n.nodeValue !== u.cz[i] && u.applied[i] === n.nodeValue) n.nodeValue = u.cz[i];
      });
    }
  }

  function apply(u) {
    if (u.dead) return;
    var i;
    if (lang === "cs") {
      for (i = 0; i < u.cz.length; i++) if (getVal(u, i) === u.applied[i] && u.applied[i] != null) setVal(u, i, u.cz[i]);
      u.applied = [];
      return;
    }
    var key = keyOf(u.tpl);
    var tr = cache[key];
    if (tr) {
      var parts = u.cz.length > 1 ? splitTr(tr, u.cz.length) : [tr];
      if (!parts) { // markers broken: fall back to translating each node on its own
        failed[key] = true;
        var root = u.root;
        dissolve(u);
        if (root) { root.__rfNoGroup = true; scan(root); }
        return;
      }
      for (i = 0; i < parts.length; i++) {
        var out = wrapWs(u.cz[i], fill(parts[i].trim(), u.nums));
        if (getVal(u, i) !== out) setVal(u, i, out);
        u.applied[i] = out;
      }
    } else if (!failed[key] && !inflight[key]) {
      pending[key] = u.tpl;
      schedule();
    }
  }

  // ---------- scanning ----------
  function allInline(el) {
    var kids = el.children;
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (!INLINE[k.tagName.toUpperCase()] || skipped(k)) return false;
      var tag = k.tagName.toUpperCase();
      if (tag !== "SVG" && tag !== "IMG" && !allInline(k)) return false;
    }
    return true;
  }
  function inlineRoot(el) {
    if (!el || el === document.body || !allInline(el)) return null;
    var r = el;
    while (r.parentElement && r.parentElement !== document.body && allInline(r.parentElement)) r = r.parentElement;
    return r;
  }
  function textNodesIn(root) {
    var out = [];
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    var n;
    while ((n = w.nextNode())) {
      if (n.nodeValue.trim() && !(n.parentNode && n.parentNode.tagName && n.parentNode.tagName.toUpperCase() === "SVG")) out.push(n);
    }
    return out;
  }

  function scanTextNode(node) {
    var parent = node.parentNode;
    if (!parent || parent.nodeType !== 1 || skipped(parent)) return;
    var cur = node.nodeValue;
    var u = unitOf.get(node);
    if (u && !u.dead) {
      var i = u.nodes.indexOf(node);
      if (cur === u.applied[i] || cur === u.cz[i]) return; // ours, or unchanged Czech
      // The app rewrote this node: drop the unit (restoring its siblings) and rebuild from fresh Czech
      var root0 = u.root;
      dissolve(u);
      if (root0 && root0.isConnected) { scan(root0); return; }
    }
    if (!cur || !cur.trim() || lang === "cs") return;
    var root = parent.__rfNoGroup ? null : inlineRoot(parent);
    var nodes = root && !root.__rfNoGroup ? textNodesIn(root) : [node];
    if (nodes.length > 1) {
      nodes.forEach(function (n) { var o = unitOf.get(n); if (o && !o.dead) dissolve(o); });
      nodes = textNodesIn(root); // values restored to Czech
    } else root = null;
    var unit = makeUnit("text", nodes.map(function (n) { return n.nodeValue; }), { nodes: nodes, root: root });
    if (!worthTranslating(unit.tpl)) { unit.dead = true; return; }
    nodes.forEach(function (n) { unitOf.set(n, unit); });
    apply(unit);
  }

  function attrUnit(el, name, t) {
    var key = t === "meta" ? "content" : name;
    var val = el.getAttribute(key);
    if (!val || lang === "cs") return;
    var map = elUnits.get(el);
    if (!map) { map = {}; elUnits.set(el, map); }
    var u = map[key];
    if (u && !u.dead) {
      if (val === u.applied[0] || val === u.cz[0]) return;
      u.dead = true;
    }
    u = makeUnit(t, [val], { el: el, name: name });
    if (!worthTranslating(u.tpl)) { u.dead = true; return; }
    map[key] = u;
    apply(u);
  }

  function scanAttrs(el) {
    if (skipped(el)) return;
    for (var i = 0; i < ATTRS.length; i++) attrUnit(el, ATTRS[i], "attr");
  }

  function scanHead() {
    var t = document.querySelector("title");
    if (t && t.firstChild && t.firstChild.nodeType === 3) scanTextNode(t.firstChild);
    var metas = document.querySelectorAll('meta[name="description"], meta[property="og:title"], meta[property="og:description"]');
    for (var i = 0; i < metas.length; i++) attrUnit(metas[i], "content", "meta");
  }

  function scan(root) {
    if (!root) return;
    if (root.nodeType === 3) { scanTextNode(root); return; }
    if (root.nodeType !== 1) return;
    if (root.tagName === "TITLE") { if (root.firstChild) scanTextNode(root.firstChild); return; }
    if (skipped(root)) return;
    scanAttrs(root);
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) {
        if (n.nodeType === 1) {
          if (SKIP_TAGS[n.tagName.toUpperCase()] || (n.matches && n.matches(SKIP_SELECTOR))) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_SKIP;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var n;
    while ((n = walker.nextNode())) scanTextNode(n);
    var els = root.querySelectorAll("[placeholder],[title],[aria-label],[alt]");
    for (var i = 0; i < els.length; i++) scanAttrs(els[i]);
  }

  // ---------- server round trip ----------
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(flush, 60);
  }

  function flush() {
    var keys = Object.keys(pending);
    if (!keys.length) { reveal(); return; }
    var batch = {};
    var count = 0;
    for (var i = 0; i < keys.length && count < 150; i++, count++) { batch[keys[i]] = pending[keys[i]]; delete pending[keys[i]]; inflight[keys[i]] = true; }
    var myLang = lang;
    var strings = Object.keys(batch).map(function (k) { return batch[k]; });
    fetch("/api/i18n/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: myLang, strings: strings }),
    })
      .then(function (r) { return r.ok ? r.json() : { translations: {} }; })
      .catch(function () { return { translations: {} }; })
      .then(function (res) {
        var tr = (res && res.translations) || {};
        Object.keys(batch).forEach(function (k) {
          delete inflight[k];
          if (tr[k] && myLang === lang) cache[k] = tr[k]; else if (myLang === lang) failed[k] = true;
        });
        if (myLang === lang) { saveCache(); rescanTracked(); }
        if (Object.keys(pending).length) flush(); else reveal();
      });
    if (Object.keys(pending).length) setTimeout(flush, 0);
  }

  function rescanTracked() {
    var alive = [];
    for (var i = 0; i < tracked.length; i++) {
      var u = tracked[i].deref();
      if (!u || u.dead) continue;
      alive.push(tracked[i]);
      apply(u);
    }
    tracked = alive;
  }

  // ---------- flash prevention ----------
  var hideStyle = null;
  function hidePage() {
    if (hideStyle || lang === "cs") return;
    hideStyle = document.createElement("style");
    hideStyle.textContent = "html.rf-i18n-wait body{opacity:0!important}html.rf-i18n-ready body{transition:opacity .15s}";
    document.head.appendChild(hideStyle);
    docEl.classList.add("rf-i18n-wait");
    setTimeout(reveal, 1800);
  }
  function reveal() {
    if (revealed) return;
    revealed = true;
    docEl.classList.remove("rf-i18n-wait");
    docEl.classList.add("rf-i18n-ready");
  }

  // ---------- switcher ----------
  var STYLE = [
    ".rf-lang{position:relative;display:inline-flex;align-items:center;font:600 14px/1 Inter,Helvetica,Arial,sans-serif;z-index:1000}",
    ".rf-lang-btn{display:inline-flex;align-items:center;gap:6px;height:36px;padding:0 12px;border:1px solid rgba(208,212,205,.9);border-radius:999px;background:rgba(255,255,255,.9);color:#0e0f0c;cursor:pointer;font:inherit;white-space:nowrap}",
    ".rf-lang-btn:hover{background:#f7f9f6}",
    ".rf-lang-btn svg{width:16px;height:16px;flex:none}",
    ".rf-lang-menu{position:absolute;top:calc(100% + 8px);right:0;min-width:168px;padding:6px;margin:0;list-style:none;background:#fff;border:1px solid #d0d4cd;border-radius:16px;box-shadow:0 12px 32px rgba(14,15,12,.14)}",
    ".rf-lang-menu[hidden]{display:none}",
    ".rf-lang-menu button{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:9px 12px;border:0;border-radius:10px;background:none;color:#0e0f0c;font:inherit;text-align:left;cursor:pointer}",
    ".rf-lang-menu button:hover{background:#f7f9f6}",
    ".rf-lang-menu button[aria-checked=true]{background:#e2f6d5;color:#163300}",
    ".rf-lang-float{position:fixed;right:16px;bottom:16px}",
    "@media (pointer:coarse){.rf-lang-btn{height:44px;min-width:44px}}",
    ".rf-lang-mobile{margin:0 0 0 auto}.rf-lang-mobile .rf-lang-btn{height:40px}",
    ".rf-lang-float .rf-lang-menu{top:auto;bottom:calc(100% + 8px)}",
  ].join("");
  var GLOBE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/></svg>';
  var switcher = null;

  function buildSwitcher() {
    var wrap = document.createElement("div");
    wrap.className = "rf-lang";
    wrap.setAttribute("translate", "no");
    wrap.innerHTML =
      '<button type="button" class="rf-lang-btn" aria-haspopup="menu" aria-expanded="false" aria-label="Language">' + GLOBE + '<span class="rf-lang-cur"></span></button>' +
      '<ul class="rf-lang-menu" role="menu" hidden>' +
      LANGS.map(function (l) {
        return '<li role="none"><button type="button" role="menuitemradio" data-lang="' + l.code + '" aria-checked="false"><span>' + l.name + "</span></button></li>";
      }).join("") +
      "</ul>";
    var btn = wrap.querySelector(".rf-lang-btn");
    var menu = wrap.querySelector(".rf-lang-menu");
    function close() { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute("aria-expanded", String(!menu.hidden));
    });
    menu.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-lang]");
      if (!b) return;
      close();
      setLang(b.getAttribute("data-lang"));
    });
    document.addEventListener("click", close);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
    return wrap;
  }

  function syncSwitcher() {
    if (!switcher) return;
    var cur = LANGS.filter(function (l) { return l.code === lang; })[0];
    switcher.querySelector(".rf-lang-cur").textContent = cur.code.toUpperCase();
    [].forEach.call(switcher.querySelectorAll("button[data-lang]"), function (b) {
      b.setAttribute("aria-checked", String(b.getAttribute("data-lang") === lang));
    });
  }

  var mobileMq = window.matchMedia ? window.matchMedia("(max-width:1040px)") : null;

  function switcherHost() {
    // Public site: nav.js builds .nx-pill; its .nx-right is hidden on phones, so sit beside the burger there.
    var pill = document.querySelector(".nx-pill");
    if (pill) {
      if (mobileMq && mobileMq.matches && pill.querySelector(".nx-toggle")) return { host: pill, before: pill.querySelector(".nx-toggle"), mobile: true };
      var right = pill.querySelector(".nx-right");
      if (right) return { host: right, before: right.querySelector(".nav-login") || right.firstChild };
    }
    var other = document.querySelector(".nav-cta") || document.querySelector(".nav-end");
    if (other) return { host: other, before: other.querySelector(".nav-login") || other.firstChild };
    return null;
  }

  function mountSwitcher() {
    if (!document.getElementById("rf-lang-style")) {
      var st = document.createElement("style");
      st.id = "rf-lang-style";
      st.textContent = STYLE;
      document.head.appendChild(st);
    }
    if (!switcher) { switcher = buildSwitcher(); syncSwitcher(); }
    var target = switcherHost();
    if (!target) {
      if (switcher.isConnected || !document.body) return;
      switcher.classList.add("rf-lang-float");
      document.body.appendChild(switcher);
      return;
    }
    if (switcher.isConnected && switcher.parentNode === target.host && !switcher.classList.contains("rf-lang-float")) return;
    switcher.classList.remove("rf-lang-float");
    switcher.classList.toggle("rf-lang-mobile", !!target.mobile);
    target.host.insertBefore(switcher, target.before && target.before.parentNode === target.host ? target.before : null);
  }
  if (mobileMq && mobileMq.addEventListener) mobileMq.addEventListener("change", function () { if (switcher) mountSwitcher(); });

  // ---------- public ----------
  function setLang(code) {
    if (CODES.indexOf(code) < 0 || code === lang) return;
    lang = code;
    store.set(STORE_KEY, code);
    cache = {}; failed = {}; inflight = {}; pending = {};
    loadCache();
    docEl.setAttribute("lang", code);
    syncSwitcher();
    if (lang !== "cs") { revealed = false; }
    rescanTracked(); // swaps text to cs or to cached translations of the new language
    if (started) { scan(document.body); scanHead(); }
    if (Object.keys(pending).length) schedule(); else reveal();
    try { window.dispatchEvent(new CustomEvent("rf:langchange", { detail: { lang: code } })); } catch (e) {}
  }

  function start() {
    if (started) return;
    started = true;
    mountSwitcher();
    scanHead();
    scan(document.body);
    if (!Object.keys(pending).length) reveal(); else schedule();
    observer = new MutationObserver(function (muts) {
      var mounted = false;
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === "characterData") { scanTextNode(m.target); continue; }
        if (m.type === "attributes") { if (m.target.nodeType === 1) { if (m.target.tagName === "META") scanHead(); else scanAttrs(m.target); } continue; }
        for (var j = 0; j < m.addedNodes.length; j++) {
          var n = m.addedNodes[j];
          if (n === switcher) continue;
          scan(n);
        }
        if (!mounted && switcher && !switcher.isConnected) { mounted = true; mountSwitcher(); }
      }
      // nav.js builds the header after load: attach the switcher as soon as its host exists
      if (switcher && (!switcher.isConnected || switcher.classList.contains("rf-lang-float") || !switcher.parentNode.matches(".nx-right,.nx-pill,.nav-cta,.nav-end"))) {
        mountSwitcher();
      }
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRS.concat(["content"]),
    });
  }

  window.__rfI18n = { languages: LANGS, get lang() { return lang; }, setLang: setLang };

  docEl.setAttribute("lang", lang);
  loadCache();
  hidePage();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
