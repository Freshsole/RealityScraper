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
    });
  });

  /* quick chips */
  form.querySelectorAll('.hs-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      q.value = chip.getAttribute('data-q') || '';
      price.value = chip.getAttribute('data-price') || '';
      areaFrom.value = chip.getAttribute('data-from') || '';
      areaTo.value = '';
      hideSuggest();
      q.focus();
    });
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
    if (price.value) p.set('price_to', price.value);
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
