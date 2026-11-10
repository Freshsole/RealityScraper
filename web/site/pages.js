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
    /* Natural-language matching: fold diacritics, drop filler words, match word starts (Czech endings), rank. */
    var fold = function (s) { return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); };
    var words = function (s) { return fold(s).split(/[^a-z0-9]+/).filter(Boolean); };
    var STOP = {};
    ('a aby ale ani asi ba bez by byl byla bylo byt co copak do dalsi jak jake jaky jaka jakou je jeho jeji jen jsem jsou jste k kam kde kdy kdyz ke kolik kterak ktery ktera ktere kym me mi mit moc mohu moje muj musim my na nad nam nas nebo nic o od on ona oni po pod pokud pri proc protoze s se si sve tak take tam to tom tu ty u uz v ve vam vas vsak z za ze zda').split(' ').forEach(function (w) { STOP[w] = 1; });
    var stem = function (w) { return w.length <= 3 ? w : w.slice(0, Math.max(4, w.length - 2)); };
    var docs = items.map(function (it) {
      return {
        q: words(it.querySelector('.qa-q span').textContent),
        k: words(it.getAttribute('data-kw') || ''),
        a: words(it.querySelector('.qa-a p').textContent)
      };
    });
    var hit = function (list, tok) {
      var st = stem(tok);
      for (var i = 0; i < list.length; i++) {
        if (tok.length <= 3 ? list[i] === tok : list[i].indexOf(st) === 0) return true;
      }
      return false;
    };
    var score = function (d, toks) {
      var total = 0, matched = 0;
      toks.forEach(function (t) {
        var s = hit(d.q, t) ? 3 : hit(d.k, t) ? 2 : hit(d.a, t) ? 1 : 0;
        if (s) { matched++; total += s; }
      });
      return matched / toks.length >= 0.5 ? total + matched : 0;
    };
    var order = items.slice();
    var apply = function () {
      var raw = (search && search.value || '').trim();
      var toks = words(raw).filter(function (w) { return !STOP[w]; });
      if (!toks.length) toks = words(raw);
      var scores = items.map(function (it, i) { return toks.length ? score(docs[i], toks) : 1; });
      var shown = 0, best = -1;
      items.forEach(function (it, i) {
        var ok = (catNow === 'all' || it.getAttribute('data-cat') === catNow) && scores[i] > 0;
        it.hidden = !ok;
        var p = it.querySelector('.qa-a p');
        var marks = raw ? toks.filter(function (t) { return t.length > 2; }) : [];
        p.innerHTML = marks.length && ok
          ? originals[i].replace(new RegExp('(' + marks.map(esc).join('|') + ')', 'gi'), '<mark>$1</mark>')
          : originals[i];
        if (ok) { shown++; if (best < 0 || scores[i] > scores[best]) best = i; }
        if (raw) setOpen(it, false);
      });
      if (raw && best >= 0) setOpen(items[best], true);
      var sorted = raw ? items.map(function (it, i) { return i; }).sort(function (x, y) { return scores[y] - scores[x] || x - y; }) : items.map(function (it, i) { return i; });
      sorted.forEach(function (i) { list.appendChild(items[i]); });
      [].forEach.call(document.querySelectorAll('.help-cat'), function (c) { c.hidden = !c.querySelector('.qa:not([hidden])'); });
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
