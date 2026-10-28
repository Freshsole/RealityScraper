/* Homepage hero: the paper plane flies its route on load, drawing the dashed trail behind it.
   The route is derived from the laid-out elements (tab notch -> loop beside the photos -> top of the collage). */
(function () {
  'use strict';
  var svg = document.getElementById('hs-route');
  var path = document.getElementById('hs-route-path');
  var reveal = document.getElementById('hs-route-reveal');
  var plane = document.getElementById('hs-plane');
  var hero = document.querySelector('.hero-search');
  if (!svg || !path || !reveal || !plane || !hero) return;
  var len = 0, progress = 0, running = false, done = false;

  function rel(el) {
    var h = hero.getBoundingClientRect(), r = el.getBoundingClientRect();
    return { l: r.left - h.left, t: r.top - h.top, r: r.right - h.left, b: r.bottom - h.top, w: r.width, h: r.height };
  }
  function build() {
    var tabs = document.querySelector('.hs-tabs'), photo = document.querySelector('.hs-photo-2');
    if (!tabs || !photo || !svg.getBoundingClientRect().width) return false;
    var hr = hero.getBoundingClientRect();
    svg.setAttribute('viewBox', '0 0 ' + hr.width + ' ' + hr.height);
    var t = rel(tabs), p = rel(photo);
    var S = { x: t.r + 14, y: t.b - 24 };
    var k = Math.min(1, p.w / 380);
    var rx = 135 * k, ry = 100 * k;
    var cx = p.l - 60 + rx, cy = p.t + p.h * 0.5;
    var nav = document.querySelector('.navbar');
    var E = { x: p.l - 5, y: 58 + (nav ? nav.offsetHeight : 0) };
    var pts = [S, { x: cx - rx - 70 * k, y: cy + 150 * k }];
    for (var a = 180; a <= 540; a += 20) {
      var r = a * Math.PI / 180;
      pts.push({ x: cx + rx * Math.cos(r), y: cy + ry * Math.sin(r) });
    }
    pts.push({ x: cx - rx - 8 * k, y: cy - ry * 1.35 }, E);
    var d = 'M' + pts[0].x.toFixed(1) + ' ' + pts[0].y.toFixed(1);
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
      d += ' C ' + (p1.x + (p2.x - p0.x) / 6).toFixed(1) + ' ' + (p1.y + (p2.y - p0.y) / 6).toFixed(1) + ' ' +
        (p2.x - (p3.x - p1.x) / 6).toFixed(1) + ' ' + (p2.y - (p3.y - p1.y) / 6).toFixed(1) + ' ' + p2.x.toFixed(1) + ' ' + p2.y.toFixed(1);
    }
    path.setAttribute('d', d);
    reveal.setAttribute('d', d);
    len = path.getTotalLength();
    reveal.style.strokeDasharray = String(len);
    return true;
  }
  function place(t) {
    progress = t;
    var l = len * t, p = path.getPointAtLength(l);
    var q = path.getPointAtLength(Math.min(len, l + 2)), back = path.getPointAtLength(Math.max(0, l - 2));
    var ang = Math.atan2(q.y - back.y, q.x - back.x) * 180 / Math.PI;
    plane.setAttribute('transform', 'translate(' + p.x + ' ' + p.y + ') rotate(' + (ang + 45) + ')');
    reveal.style.strokeDashoffset = String(len * (1 - t));
  }
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function ease(x) { return x < .5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2; }
  function run() {
    if (!build()) return;
    if (still) { done = true; place(1); return; }
    place(0);
    var dur = 3600, start = 0;
    running = true;
    function tick(now) {
      if (!start) start = now;
      var t = Math.min(1, (now - start) / dur);
      place(ease(t));
      if (t < 1) requestAnimationFrame(tick); else { running = false; done = true; svg.classList.add('is-landed'); }
    }
    setTimeout(function () { requestAnimationFrame(tick); }, 350);
  }
  var rt = 0;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () { if (!running && done && build()) place(1); }, 150);
  });
  if (document.readyState === 'complete') run(); else window.addEventListener('load', run);
})();

/* Homepage hero: short catalog search that leads into a free account. */
(function () {
  'use strict';
  var form = document.getElementById('hs-form');
  if (!form) return;
  var q = document.getElementById('hs-q');
  var price = document.getElementById('hs-price');
  var areaFrom = document.getElementById('hs-area-from');
  var areaTo = document.getElementById('hs-area-to');
  var suggest = document.getElementById('hs-suggest');
  var modal = document.getElementById('hs-modal');
  var offer = 'pronajem';
  var timer = 0;
  var seq = 0;
  var lastFocus = null;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmt(n) { return Number(n).toLocaleString('cs-CZ'); }

  /* offer tabs */
  form.querySelectorAll('.hs-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      offer = tab.getAttribute('data-offer');
      form.querySelectorAll('.hs-tab').forEach(function (t) {
        var on = t === tab;
        t.classList.toggle('on', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      price.placeholder = offer === 'prodej' ? 'např. 6 000 000' : 'např. 25 000';
      var cur = price.value.replace(/\s/g, '');
      if (cur === (offer === 'prodej' ? '25000' : '6000000')) price.value = offer === 'prodej' ? '6 000 000' : '25 000';
    });
  });

  /* quick chips */
  form.querySelectorAll('.hs-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      q.value = chip.getAttribute('data-q') || '';
      price.value = chip.getAttribute('data-price') ? fmt(chip.getAttribute('data-price')) : '';
      areaFrom.value = chip.getAttribute('data-from') || '';
      areaTo.value = '';
      hideSuggest();
      q.focus();
    });
  });

  price.addEventListener('input', function () {
    var d = price.value.replace(/\D/g, '');
    price.value = d ? fmt(d) : '';
  });

  /* locality suggestions */
  function hideSuggest() { suggest.hidden = true; suggest.innerHTML = ''; }
  q.addEventListener('input', function () {
    clearTimeout(timer);
    var term = q.value.trim();
    if (term.length < 2) { hideSuggest(); return; }
    var mine = ++seq;
    timer = setTimeout(function () {
      fetch('/api/places/search?q=' + encodeURIComponent(term))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (mine !== seq) return;
          var items = (d.items || []).slice(0, 6);
          if (!items.length) { hideSuggest(); return; }
          suggest.innerHTML = items.map(function (it) {
            return '<button type="button" data-v="' + esc(it.label.split(',')[0]) + '">' + esc(it.label) + '</button>';
          }).join('');
          suggest.hidden = false;
        })
        .catch(hideSuggest);
    }, 220);
  });
  suggest.addEventListener('click', function (e) {
    var b = e.target.closest('button');
    if (!b) return;
    q.value = b.getAttribute('data-v');
    hideSuggest();
  });
  document.addEventListener('click', function (e) { if (!e.target.closest('.hs-field-wide')) hideSuggest(); });

  /* search -> teaser -> sign-up prompt */
  function params() {
    var p = new URLSearchParams();
    if (q.value.trim()) p.set('q', q.value.trim());
    p.set('offer', offer);
    var priceNum = price.value.replace(/\D/g, '');
    if (priceNum) p.set('price_to', priceNum);
    if (areaFrom.value) p.set('area_from', areaFrom.value);
    if (areaTo.value) p.set('area_to', areaTo.value);
    return p;
  }

  function summary(p) {
    var bits = [];
    bits.push(offer === 'prodej' ? 'Prodej' : 'Pronájem');
    if (p.get('q')) bits.push(p.get('q'));
    if (p.get('price_to')) bits.push('do ' + fmt(p.get('price_to')) + ' Kč');
    if (p.get('area_from') || p.get('area_to')) {
      bits.push((p.get('area_from') || '0') + (p.get('area_to') ? '–' + p.get('area_to') : '+') + ' m²');
    }
    return bits.map(function (b) { return '<span class="pg-chip">' + esc(b) + '</span>'; }).join('');
  }

  function openModal(p) {
    seq++;
    hideSuggest();
    var next = '/nabidka?' + p.toString();
    document.getElementById('hs-summary').innerHTML = summary(p);
    document.getElementById('hs-register').href = '/registrace?next=' + encodeURIComponent(next);
    document.getElementById('hs-login').href = '/prihlaseni?next=' + encodeURIComponent(next);
    document.getElementById('hs-teaser').innerHTML = '<p class="hs-count">Hledám nabídky…</p>';
    document.getElementById('hs-modal-title').textContent = 'Vytvořte si účet zdarma';
    lastFocus = document.activeElement;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    document.getElementById('hs-register').focus();

    var api = new URLSearchParams(p);
    api.set('limit', '3');
    fetch('/api/public/catalog?' + api.toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var box = document.getElementById('hs-teaser');
        var total = Number(d.total || 0);
        if (!total) {
          box.innerHTML = '<p class="hs-count">Zatím jsme nic nenašli, ale vyhledávání vám uložíme a upozorníme vás, jakmile se objeví shoda.</p>';
          return;
        }
        document.getElementById('hs-modal-title').textContent = 'Našli jsme ' + fmt(total) + ' nabídek';
        box.innerHTML = '<ul class="hs-list">' + (d.items || []).map(function (it) {
          return '<li><span class="hs-loc">' + esc(it.locality || '') + '</span><span class="hs-spec">' + esc((it.disposition || '') + (it.area_m2 ? ' · ' + it.area_m2 + ' m²' : '')) + '</span><strong>' + esc(it.price_label || (it.price_czk ? fmt(it.price_czk) + ' Kč' : '')) + '</strong></li>';
        }).join('') + '</ul><p class="hs-rest">…a další ' + fmt(Math.max(total - 3, 0)) + ' po registraci.</p>';
      })
      .catch(function () {
        document.getElementById('hs-teaser').innerHTML = '';
      });
  }

  function closeModal() {
    modal.hidden = true;
    document.body.style.overflow = '';
    if (lastFocus) lastFocus.focus();
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); hideSuggest(); openModal(params()); });
  document.getElementById('hs-close').addEventListener('click', closeModal);
  modal.addEventListener('mousedown', function (e) { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) closeModal(); });
})();
