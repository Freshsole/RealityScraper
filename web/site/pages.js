/* Interactions for content pages: reveal-on-scroll, count-up, copy, table sort, FAQ. */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* reveal + stat bars */
  var targets = [].slice.call(document.querySelectorAll('.reveal, .stat-card, .step, .feature-card'));
  document.querySelectorAll('.stat-bar i').forEach(function (i) { i.style.setProperty('--w', i.getAttribute('data-w') || '.5'); });
  if ('IntersectionObserver' in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in-view');
        countUp(e.target);
        io.unobserve(e.target);
      });
    }, { threshold: 0.15 });
    targets.forEach(function (t) { io.observe(t); });
  } else {
    targets.forEach(function (t) { t.classList.add('in-view'); });
  }

  function countUp(root) {
    root.querySelectorAll('[data-count]').forEach(function (el) {
      var to = parseFloat(el.getAttribute('data-count'));
      if (!isFinite(to) || reduce) return;
      var fmt = new Intl.NumberFormat('cs-CZ');
      var t0 = performance.now();
      (function tick(now) {
        var p = Math.min(1, (now - t0) / 900);
        el.textContent = fmt.format(Math.round(to * (1 - Math.pow(1 - p, 3))));
        if (p < 1) requestAnimationFrame(tick);
      })(t0);
    });
  }

  /* copy buttons */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-copy]');
    if (!btn) return;
    var text = btn.getAttribute('data-copy');
    var done = function () {
      var old = btn.textContent;
      btn.textContent = 'Zkopírováno';
      btn.classList.add('is-done');
      setTimeout(function () { btn.textContent = old; btn.classList.remove('is-done'); }, 1600);
    };
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
  });

  /* sortable tables */
  document.querySelectorAll('.data-table').forEach(function (table) {
    var heads = [].slice.call(table.querySelectorAll('th[data-sort]'));
    heads.forEach(function (th, _i) {
      th.addEventListener('click', function () {
        var col = th.cellIndex;
        var asc = !(th.classList.contains('is-sorted') && !th.classList.contains('asc'));
        heads.forEach(function (h) { h.classList.remove('is-sorted', 'asc'); });
        th.classList.add('is-sorted');
        if (asc) th.classList.add('asc');
        var body = table.tBodies[0];
        var rows = [].slice.call(body.rows);
        var num = function (r) { return parseFloat((r.cells[col].getAttribute('data-v') || r.cells[col].textContent).replace(/[^\d,.-]/g, '').replace(',', '.')) || 0; };
        rows.sort(function (a, b) { return asc ? num(a) - num(b) : num(b) - num(a); });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });

  /* FAQ: accordion, search, category filter, deep link */
  var list = document.querySelector('.qa-list');
  if (list) {
    var items = [].slice.call(list.querySelectorAll('.qa'));
    var search = document.getElementById('faq-q');
    var cats = [].slice.call(document.querySelectorAll('.faq-cat'));
    var empty = document.getElementById('faq-empty');
    var catNow = 'all';
    var originals = items.map(function (it) { return it.querySelector('.qa-a p').innerHTML; });

    var setOpen = function (it, open) {
      it.classList.toggle('is-open', open);
      it.querySelector('.qa-q').setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    items.forEach(function (it) {
      it.querySelector('.qa-q').addEventListener('click', function () {
        var open = !it.classList.contains('is-open');
        items.forEach(function (o) { setOpen(o, o === it && open); });
        if (open && it.id) history.replaceState(null, '', '#' + it.id);
      });
    });
    var esc = function (s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
    var apply = function () {
      var q = (search && search.value || '').trim().toLowerCase();
      var shown = 0;
      items.forEach(function (it, i) {
        var text = (it.textContent || '').toLowerCase();
        var ok = (catNow === 'all' || it.getAttribute('data-cat') === catNow) && (!q || text.indexOf(q) !== -1);
        it.hidden = !ok;
        var p = it.querySelector('.qa-a p');
        p.innerHTML = q && ok ? originals[i].replace(new RegExp('(' + esc(q) + ')', 'gi'), '<mark>$1</mark>') : originals[i];
        if (ok) shown++;
        if (ok && q) setOpen(it, true);
      });
      if (empty) empty.hidden = shown !== 0;
    };
    if (search) search.addEventListener('input', apply);
    cats.forEach(function (c) {
      c.addEventListener('click', function () {
        catNow = c.getAttribute('data-cat');
        cats.forEach(function (x) { x.classList.toggle('is-on', x === c); });
        apply();
      });
    });
    var hash = location.hash.slice(1);
    var target = hash && document.getElementById(hash);
    if (target && target.classList.contains('qa')) { setOpen(target, true); setTimeout(function () { target.scrollIntoView({ block: 'center' }); }, 100); }
    else if (items[0]) setOpen(items[0], true);
  }
})();
