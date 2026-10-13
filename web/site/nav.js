/* Floating glass navigation pill with mega menu, scroll-spy and mobile accordion.
 * Upgrades the static <header class="navbar"> found on every site page; without
 * JS the original markup keeps working. */
(function () {
  'use strict';

  var header = document.querySelector('header.navbar');
  if (!header || header.getAttribute('data-nx')) return;
  var brand = header.querySelector('.brand');
  var cta = header.querySelector('.nav-cta .pill');
  var login = header.querySelector('.nav-login');
  if (!brand || !cta) return;

  var SCROLL_THRESHOLD = 24;
  var MAX_VISIBLE = 4;
  var TRIGGER = 99;
  var MOBILE_MQ = '(max-width: 860px)';

  var path = location.pathname.replace(/\/+$/, '') || '/';
  var isHome = path === '/';

  function href(h) {
    return isHome && h.indexOf('/#') === 0 ? h.slice(1) : h;
  }

  /* ---------- content ---------- */
  /* Sections of the current page feed both the bar (scroll-spy) and the
   * "Na této stránce" category. Title = data-nav | .eyebrow | h2; only sections
   * with a short label (data-nav or eyebrow) go to the bar. */
  var CURATED_HOME = {
    'jak-to-funguje': ['Jak to funguje', 'Od nastavení hlídání po první upozornění'],
    'databaze': ['Databáze bytů', 'Nabídky ze všech českých portálů na jednom místě'],
    'srovnani': ['Srovnání', 'Realitify oproti ručnímu hlídání inzerátů'],
    'cenik': ['Ceník', 'Tarify od 0 Kč, bez platební karty'],
    'hry': ['Hry', 'Zkuste trefit cenu dřív, než byt zmizí'],
    'faq': ['FAQ', 'Odpovědi na nejčastější otázky']
  };

  function clean(t) { return (t || '').replace(/\s+/g, ' ').trim(); }
  function titleCase(t) {
    t = clean(t);
    return t.length > 4 && t === t.toUpperCase() ? t.charAt(0) + t.slice(1).toLowerCase() : t;
  }
  function collectSections() {
    var root = document.querySelector('main, .page') || document.body;
    var out = [];
    [].slice.call(root.querySelectorAll('section')).forEach(function (el, i) {
      if (el.closest('header, footer, .nx-pill') || el.hidden) return;
      var cur = isHome && CURATED_HOME[el.id];
      var nav = el.getAttribute('data-nav');
      var eyebrow = el.querySelector('.eyebrow');
      var h2 = el.querySelector('h2');
      var title = cur ? cur[0] : (nav || (eyebrow && eyebrow.textContent) || (h2 && h2.textContent));
      title = titleCase(title);
      if (!title) return;
      if (!el.id) el.id = 'sec-' + i;
      var short = cur ? cur[0] : (nav || (eyebrow && eyebrow.textContent) || '');
      short = titleCase(short.split(' / ')[0].split(' · ')[0]);
      var desc = cur ? cur[1] : (el.getAttribute('data-nav-desc') || (h2 ? titleCase(h2.textContent) : ''));
      out.push({ id: el.id, el: el, title: title, short: short, desc: desc === title ? '' : desc });
    });
    if (isHome) out = out.filter(function (s) { return CURATED_HOME[s.id]; });
    return out;
  }
  var SECTIONS = collectSections();
  var BAR_SECTIONS = SECTIONS.filter(function (s) { return s.short; });
  if (BAR_SECTIONS.length < 2 && SECTIONS.length > 2) {
    // No eyebrow labels: fall back to the section headings, shortened for the bar.
    BAR_SECTIONS = SECTIONS.map(function (s) {
      var t = s.title.length > 24 ? s.title.slice(0, 23).replace(/\s+\S*$/, '') + '…' : s.title;
      return { id: s.id, el: s.el, title: s.title, short: t, desc: s.desc };
    });
  }

  var PAGE_LINKS = [
    { title: 'Nabídky', href: '/hledat', path: '/hledat' },
    { title: 'Index nájmů', href: '/index', path: '/index' },
    { title: 'Vaše úspěchy', href: '/uspechy', path: '/uspechy' },
    { title: 'Ceník', href: '/#cenik', path: null },
    { title: 'O nás', href: '/o-nas', path: '/o-nas' }
  ];

  var CATEGORIES = [
    {
      key: 'browse', title: 'Prohlížejte nabídky', desc: 'Katalog bytů a data trhu', icon: 'search',
      items: [
        { title: 'Nabídky', desc: 'Aktivní inzeráty z 9 portálů s mapou a filtry', href: '/hledat' },
        { title: 'Index nájmů', desc: 'Denní souhrn cen pronájmů', href: '/index' },
        { title: 'Ceny nájmů v Praze', desc: 'Medián, Kč/m² a vývoj trhu', href: '/trh/praha/pronajem' },
        { title: 'Pronájem Praha', desc: 'Aktuální nabídky a ceny v Praze', href: '/pronajem-praha' },
        { title: 'Pronájem Brno', desc: 'Aktuální nabídky a ceny v Brně', href: '/pronajem-brno' }
      ]
    },
    {
      key: 'watch', title: 'Hlídání nových bytů', desc: 'Upozornění dřív než ostatní', icon: 'bell',
      items: [
        { title: 'Jak to funguje', desc: 'Od nastavení po první upozornění', href: '/#jak-to-funguje' },
        { title: 'Ceník', desc: 'Tarify od 0 Kč', href: '/#cenik' },
        { title: 'Vyzkoušet zdarma', desc: 'Registrace trvá 30 sekund', href: '/registrace' },
        { title: 'Přihlásit se', desc: 'Vstup do vašeho účtu', href: '/prihlaseni' },
        { title: 'MCP pro AI', desc: 'Napojení Realitify na AI asistenty', href: '/mcp-docs' }
      ]
    },
    {
      key: 'about', title: 'O Realitify', desc: 'Příběhy, tým a kontakt', icon: 'info',
      items: [
        { title: 'Vaše úspěchy', desc: 'Příběhy lidí, kteří našli byt', href: '/uspechy' },
        { title: 'O nás', desc: 'Kdo za Realitify stojí', href: '/o-nas' },
        { title: 'Hry', desc: 'Higher / Lower a tip nájmu', href: '/hry' },
        { title: 'Kontakt', desc: 'Napište nám', href: '/kontakt' },
        { title: 'FAQ', desc: 'Časté otázky', href: '/#faq' }
      ]
    }
  ];

  if (SECTIONS.length > 1) {
    CATEGORIES.unshift({
      key: 'page', title: 'Na této stránce', desc: 'Sekce aktuální stránky', icon: 'list',
      items: SECTIONS.map(function (s) {
        return { title: s.title, desc: s.desc, href: '#' + s.id, id: s.id };
      })
    });
  }

  var ICONS = {
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>'
  };
  function icon(name) {
    return '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>';
  }
  var CHEV = '<svg class="nx-chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  /* ---------- state ---------- */
  var useSections = BAR_SECTIONS.length > 1;
  var barLinks = useSections
    ? BAR_SECTIONS.map(function (x) { return { title: x.short, href: '#' + x.id, id: x.id }; })
    : PAGE_LINKS.map(function (l) { return { title: l.title, href: href(l.href), path: l.path }; });

  var secEls = SECTIONS.map(function (x) { return x.el; });
  var barEls = BAR_SECTIONS.map(function (x) { return x.el; });
  var currentSec = -1;
  var activeIdx = -1;
  var hovered = null;
  var cat = SECTIONS.length > 1 ? 'page' : 'browse';
  var panelCat = cat;
  var megaOpen = false;
  var panelOpen = false;
  var closeTimer = 0;
  var tx = 0;
  var ready = false;

  if (!useSections) {
    barLinks.forEach(function (l, i) { if (l.path && l.path === path) activeIdx = i; });
  }

  /* ---------- markup ---------- */
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  var loginHref = login ? login.getAttribute('href') : '/prihlaseni';
  var ctaHref = cta.getAttribute('href');
  var ctaText = cta.textContent.trim();
  var brandHtml = brand.outerHTML;

  var linksHtml = barLinks.map(function (l) {
    return '<a class="nx-link" href="' + esc(l.href) + '">' + esc(l.title) + '</a>';
  }).join('');

  header.innerHTML =
    '<div class="nx-pill">' +
      brandHtml +
      '<div class="nx-right">' +
        '<nav class="nx-menu" aria-label="Hlavní navigace">' +
          '<span class="nx-highlight" aria-hidden="true"></span>' +
          '<button type="button" class="nx-trigger" aria-expanded="false" aria-haspopup="true">Prozkoumat ' + CHEV + '</button>' +
          '<div class="nx-viewport"><div class="nx-track">' + linksHtml + '</div></div>' +
        '</nav>' +
        (login ? '<a class="nav-login" href="' + esc(loginHref) + '">' + login.innerHTML + '</a>' : '') +
        '<a class="pill pill-nav" href="' + esc(ctaHref) + '">' + esc(ctaText) + '</a>' +
      '</div>' +
      '<div class="nx-mega" role="menu"><div class="nx-cats"></div><div class="nx-items"></div></div>' +
      '<button type="button" class="nx-toggle" aria-expanded="false" aria-controls="nx-panel" aria-label="Otevřít menu"><span></span></button>' +
      '<div class="nx-panel" id="nx-panel" hidden></div>' +
    '</div>' +
    '<div class="nx-backdrop" hidden></div>';

  header.classList.add('nx');
  header.setAttribute('data-nx', '1');

  var pill = header.querySelector('.nx-pill');
  var menu = header.querySelector('.nx-menu');
  var hl = header.querySelector('.nx-highlight');
  var trigger = header.querySelector('.nx-trigger');
  var viewport = header.querySelector('.nx-viewport');
  var track = header.querySelector('.nx-track');
  var linkEls = [].slice.call(header.querySelectorAll('.nx-link'));
  var mega = header.querySelector('.nx-mega');
  var catsEl = header.querySelector('.nx-cats');
  var itemsEl = header.querySelector('.nx-items');
  var toggle = header.querySelector('.nx-toggle');
  var panel = header.querySelector('.nx-panel');
  var backdrop = header.querySelector('.nx-backdrop');

  function itemHtml(it, num) {
    var cur = it.id && SECTIONS[currentSec] && SECTIONS[currentSec].id === it.id;
    return '<a class="nx-item' + (cur ? ' is-current' : '') + '" role="menuitem" href="' + esc(href(it.href)) + '"' + (cur ? ' aria-current="true"' : '') + '>' +
      '<span class="nx-item-num">' + String(num).padStart(2, '0') + '</span>' +
      '<span class="nx-item-body"><span class="nx-item-title">' + esc(it.title) + '</span><span class="nx-item-desc">' + esc(it.desc) + '</span></span></a>';
  }

  function renderMega() {
    catsEl.innerHTML = CATEGORIES.map(function (c) {
      return '<button type="button" class="nx-cat' + (c.key === cat ? ' is-on' : '') + '" data-cat="' + c.key + '">' +
        '<span class="nx-cat-icon">' + icon(c.icon) + '</span>' +
        '<span class="nx-cat-text"><strong>' + esc(c.title) + '</strong><small>' + esc(c.desc) + '</small></span></button>';
    }).join('');
    var cur = CATEGORIES.filter(function (c) { return c.key === cat; })[0] || CATEGORIES[0];
    itemsEl.innerHTML = cur.items.map(function (it, i) { return itemHtml(it, i + 1); }).join('');
  }

  function renderPanel() {
    var html = '<nav>' + CATEGORIES.map(function (c) {
      var open = c.key === panelCat;
      return '<div class="nx-acc' + (open ? ' is-open' : '') + '">' +
        '<button type="button" class="nx-acc-head" data-acc="' + c.key + '" aria-expanded="' + open + '">' +
          '<span class="nx-cat-icon">' + icon(c.icon) + '</span><span>' + esc(c.title) + '</span>' + CHEV + '</button>' +
        '<div class="nx-acc-body">' + c.items.map(function (it, i) { return itemHtml(it, i + 1); }).join('') + '</div></div>';
    }).join('') + '</nav>' +
      '<div class="nx-panel-cta"><a class="pill pill-lg" href="' + esc(ctaHref) + '">' + esc(ctaText) + '</a>' +
      (login ? '<a class="nx-panel-login" href="' + esc(loginHref) + '">' + login.textContent.trim() + '</a>' : '') + '</div>';
    panel.innerHTML = html;
  }

  /* ---------- carousel window + highlight ---------- */
  function winStart() {
    var n = linkEls.length;
    if (n <= MAX_VISIBLE) return 0;
    return Math.max(0, Math.min(activeIdx - 1, n - MAX_VISIBLE));
  }

  function layout() {
    var n = linkEls.length;
    if (!n) return;
    var start = winStart();
    var end = Math.min(n, start + MAX_VISIBLE) - 1;
    var first = linkEls[start];
    var last = linkEls[end];
    tx = first.offsetLeft;
    viewport.style.width = (last.offsetLeft + last.offsetWidth - first.offsetLeft) + 'px';
    track.style.transform = 'translateX(' + (-tx) + 'px)';
    linkEls.forEach(function (el, i) {
      var visible = i >= start && i <= end;
      el.tabIndex = visible ? 0 : -1;
      el.setAttribute('aria-hidden', visible ? 'false' : 'true');
    });
  }

  function place() {
    var idx = megaOpen ? TRIGGER : (hovered !== null ? hovered : activeIdx);
    var el = idx === TRIGGER ? trigger : linkEls[idx];
    if (!el) { hl.style.opacity = '0'; return; }
    var left = idx === TRIGGER ? trigger.offsetLeft : viewport.offsetLeft + el.offsetLeft - tx;
    hl.style.width = el.offsetWidth + 'px';
    hl.style.transform = 'translateX(' + left + 'px)';
    hl.style.opacity = '1';
    if (!ready) {
      ready = true;
      requestAnimationFrame(function () { menu.classList.add('is-ready'); });
    }
  }

  function refresh() {
    linkEls.forEach(function (el, i) {
      var on = i === activeIdx;
      el.classList.toggle('is-active', on);
      if (on) el.setAttribute('aria-current', 'true'); else el.removeAttribute('aria-current');
    });
    layout();
    place();
  }

  /* ---------- scroll: narrow pill + scroll-spy ---------- */
  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      header.classList.toggle('is-scrolled', window.scrollY > SCROLL_THRESHOLD);
      if (!secEls.length) return;
      var line = window.innerHeight * 0.4;
      var cur = -1;
      for (var i = 0; i < secEls.length; i++) {
        if (secEls[i].getBoundingClientRect().top <= line) cur = i;
      }
      var bar = -1;
      if (useSections) {
        for (var j = 0; j < barEls.length; j++) {
          if (barEls[j].getBoundingClientRect().top <= line) bar = j;
        }
      }
      if (cur !== currentSec || (useSections && bar !== activeIdx)) {
        currentSec = cur;
        if (useSections) activeIdx = bar;
        refresh();
        if (megaOpen || panelOpen) { renderMega(); renderPanel(); }
      }
    });
  }

  /* ---------- mega menu (desktop) ---------- */
  function setMega(open) {
    if (megaOpen === open) return;
    megaOpen = open;
    header.classList.toggle('is-mega', open);
    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) renderMega();
    place();
  }
  function openMega() { clearTimeout(closeTimer); setMega(true); }
  function closeMegaSoon() { clearTimeout(closeTimer); closeTimer = setTimeout(function () { setMega(false); }, 160); }

  trigger.addEventListener('mouseenter', openMega);
  trigger.addEventListener('click', function () { clearTimeout(closeTimer); setMega(!megaOpen); });
  mega.addEventListener('mouseenter', function () { clearTimeout(closeTimer); });
  mega.addEventListener('mouseleave', closeMegaSoon);
  menu.addEventListener('mouseleave', function () { hovered = null; place(); if (megaOpen) closeMegaSoon(); });
  linkEls.forEach(function (el, i) {
    el.addEventListener('mouseenter', function () { hovered = i; place(); if (megaOpen) closeMegaSoon(); });
  });

  catsEl.addEventListener('mouseover', function (e) {
    var b = e.target.closest('.nx-cat');
    if (b && b.getAttribute('data-cat') !== cat) { cat = b.getAttribute('data-cat'); renderMega(); }
  });
  catsEl.addEventListener('click', function (e) {
    var b = e.target.closest('.nx-cat');
    if (b) { cat = b.getAttribute('data-cat'); renderMega(); }
  });
  itemsEl.addEventListener('click', function () { setMega(false); });

  /* ---------- mobile panel ---------- */
  var savedOverflow = '';
  function setPanel(open) {
    if (panelOpen === open) return;
    panelOpen = open;
    header.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.setAttribute('aria-label', open ? 'Zavřít menu' : 'Otevřít menu');
    panel.hidden = !open;
    backdrop.hidden = !open;
    if (open) {
      renderPanel();
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = savedOverflow;
    }
  }
  toggle.addEventListener('click', function () { setPanel(!panelOpen); });
  backdrop.addEventListener('click', function () { setPanel(false); });
  panel.addEventListener('click', function (e) {
    var head = e.target.closest('.nx-acc-head');
    if (head) {
      panelCat = head.getAttribute('data-acc');
      renderPanel();
      return;
    }
    if (e.target.closest('a')) setPanel(false);
  });

  /* ---------- global listeners ---------- */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    setMega(false);
    setPanel(false);
  });
  document.addEventListener('mousedown', function (e) {
    if (megaOpen && !pill.contains(e.target)) setMega(false);
  });
  var mq = window.matchMedia(MOBILE_MQ);
  function onMq() {
    if (!mq.matches) setPanel(false); else setMega(false);
    layout();
    place();
  }
  if (mq.addEventListener) mq.addEventListener('change', onMq); else if (mq.addListener) mq.addListener(onMq);

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', function () { layout(); place(); }, { passive: true });

  /* ---------- init ---------- */
  refresh();
  onScroll();
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () { layout(); place(); });
  }
  requestAnimationFrame(function () { layout(); place(); });
})();
