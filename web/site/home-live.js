/* Homepage blocks that must only show real catalog data. */
(function () {
  'use strict';
  var results = document.getElementById('db-results');
  if (!results) return;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function card(it) {
    var thumb = it.image
      ? '<img src="' + esc(it.image) + '" width="378" height="165" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" />'
      : '<div class="thumb-empty" aria-hidden="true"></div>';
    var pr = String(it.price || '').split('/');
    var main = pr[0].trim();
    var unit = pr[1] ? '/ ' + pr[1].trim() : '';
    return '<a class="mock-card" href="' + esc(it.url || '/hledat') + '" target="_blank" rel="noopener noreferrer">' +
      '<div class="thumb">' + thumb + (it.portal ? '<span class="src-badge">' + esc(it.portal) + '</span>' : '') + '</div>' +
      '<div class="mock-body"><div class="mock-price"><b>' + esc(main) + '</b>' + (unit ? '<span>' + esc(unit) + '</span>' : '') + '</div>' +
      '<div class="mock-txt"><h3>' + esc(it.name || 'Byt k pronájmu') + '</h3><p>' + esc(it.locality || '') + '</p></div></div></a>';
  }

  function soldCard() {
    return '<div class="mock-card is-sold" role="status">' +
      '<div class="thumb"><picture><source srcset="/static/site/assets/sold-dog.webp" type="image/webp" />' +
      '<img src="/static/site/assets/sold-dog.png" width="378" height="165" alt="" decoding="async" /></picture></div>' +
      '<div class="mock-body"><div><h3>Tenhle byt už je pryč</h3>' +
      '<p>Někdo byl rychlejší. S hlídačem Realitify byste to stihli.</p></div></div></div>';
  }

  /* Once the section is in view, show the 3rd listing for 2 s, then swap it for the "gone" card. */
  function soldTease() {
    var section = document.getElementById('databaze') || results;
    var done = false;
    function run() {
      if (done) return;
      done = true;
      setTimeout(function () {
        var third = results.children[2];
        if (!third) return;
        third.classList.add('is-leaving');
        setTimeout(function () {
          var tmp = document.createElement('div');
          tmp.innerHTML = soldCard();
          var el = tmp.firstChild;
          el.classList.add('is-entering');
          third.replaceWith(el);
          requestAnimationFrame(function () { requestAnimationFrame(function () { el.classList.remove('is-entering'); }); });
        }, 350);
      }, 2000);
    }
    if (!('IntersectionObserver' in window)) { run(); return; }
    var io = new IntersectionObserver(function (es) {
      if (es.some(function (e) { return e.isIntersecting; })) { io.disconnect(); run(); }
    }, { threshold: 0.4 });
    io.observe(results);
  }


  /* Desktop slider: no scrollbar; drag to grab, arrows + dots for pages (same pattern as the app carousels). */
  (function slider() {
    var mq = window.matchMedia ? window.matchMedia('(min-width:861px)') : { matches: true, addEventListener: function () {} };
    var pager = document.createElement('div');
    pager.className = 'results-pager';
    pager.hidden = true;
    results.parentNode.insertBefore(pager, results.nextSibling);
    var VISIBLE = 3; // cards in view; the slider moves one card at a time
    function cards() { return [].slice.call(results.children).filter(function (c) { return c.classList.contains('mock-card'); }); }
    function pages() { return Math.max(1, cards().length - (VISIBLE - 1)); }
    function pageW() { var l = cards(); return l.length > 1 ? l[1].offsetLeft - l[0].offsetLeft : results.clientWidth; }
    function current() { return Math.min(pages() - 1, Math.round(results.scrollLeft / Math.max(1, pageW()))); }
    function goTo(i) {
      var list = cards();
      var target = list[Math.max(0, Math.min(pages() - 1, i))];
      if (target) results.scrollTo({ left: target.offsetLeft - results.offsetLeft - parseFloat(getComputedStyle(results).paddingLeft || 0), behavior: 'smooth' });
    }
    function render() {
      var n = pages();
      var cur = current();
      pager.hidden = !mq.matches || n <= 1;
      // One dot per step; the slider (and the active dot) moves one card at a time.
      pager.innerHTML = '<div class="results-dots">' + Array.apply(null, Array(n)).map(function (_, i) {
        return '<button type="button" class="results-dot' + (i === cur ? ' on' : '') + '" data-page="' + i + '" aria-label="Inzerát ' + (i + 1) + '"></button>';
      }).join('') + '</div>';
    }
    pager.addEventListener('click', function (e) {
      var dot = e.target.closest('[data-page]');
      if (dot) goTo(Number(dot.getAttribute('data-page')));
    });
    var raf = 0;
    results.addEventListener('scroll', function () { cancelAnimationFrame(raf); raf = requestAnimationFrame(render); }, { passive: true });
    window.addEventListener('resize', render);
    if (mq.addEventListener) mq.addEventListener('change', render);
    new MutationObserver(function () { render(); }).observe(results, { childList: true });

    /* grab to drag: eased follow (rAF), velocity-aware snap to the nearest card on release */
    var down = false, startX = 0, startLeft = 0, moved = false, target = 0, cur = 0, loop = 0, snapTimer = 0;
    var lastX = 0, lastT = 0, vel = 0;
    function maxLeft() { return results.scrollWidth - results.clientWidth; }
    function tick() {
      cur += (target - cur) * 0.3;
      if (Math.abs(target - cur) < 0.3) cur = target;
      results.scrollLeft = cur;
      loop = (down || cur !== target) ? requestAnimationFrame(tick) : 0;
    }
    results.addEventListener('pointerdown', function (e) {
      if (!mq.matches || (e.pointerType === 'touch') || (e.button != null && e.button !== 0) || pages() <= 1) return;
      clearTimeout(snapTimer);
      down = true; moved = false; startX = e.clientX; startLeft = results.scrollLeft;
      lastX = e.clientX; lastT = performance.now(); vel = 0;
      results.classList.add('is-dragging');
      cur = target = startLeft;
    });
    results.addEventListener('pointermove', function (e) {
      if (!down) return;
      var dx = e.clientX - startX;
      if (!moved && Math.abs(dx) < 5) return;
      if (!moved) { moved = true; results.setPointerCapture && results.setPointerCapture(e.pointerId); }
      var now = performance.now();
      if (now > lastT) vel = 0.8 * vel + 0.2 * ((e.clientX - lastX) / (now - lastT));
      lastX = e.clientX; lastT = now;
      var t = startLeft - dx, m = maxLeft();
      if (t < 0) t = t * 0.35;            // soft edge resistance
      else if (t > m) t = m + (t - m) * 0.35;
      target = t;
      if (!loop) loop = requestAnimationFrame(tick);
    });
    function end() {
      if (!down) return;
      down = false;
      if (!moved) { results.classList.remove('is-dragging'); return; }
      cancelAnimationFrame(loop); loop = 0;
      var step = pageW();
      var projected = results.scrollLeft - vel * 180; // flick carries it a bit further
      goTo(Math.round(projected / Math.max(1, step)));
      snapTimer = setTimeout(function () { results.classList.remove('is-dragging'); }, 600);
    }
    results.addEventListener('pointerup', end);
    results.addEventListener('pointercancel', end);
    results.addEventListener('click', function (e) { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);
    results.addEventListener('dragstart', function (e) { e.preventDefault(); });
    render();
  })();

  fetch('/api/public/landing-listings')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var heading = document.getElementById('db-title');
      if (heading && d.title) heading.textContent = d.title;
      var more = document.querySelector('.live-head .search-btn');
      if (more && d.city) more.setAttribute('href', '/hledat?q=' + encodeURIComponent(d.city) + '&offer=pronajem&price_to=22000');
      var items = (d.items || []).slice(0, 6);
      if (!items.length) {
        results.innerHTML = '<p class="live-empty">Aktuální nabídky najdete v <a href="/hledat">katalogu</a>.</p>';
        return;
      }
      results.innerHTML = items.map(card).join('');
      if (items.length >= 3) soldTease();
    })
    .catch(function () {
      results.innerHTML = '<p class="live-empty">Aktuální nabídky najdete v <a href="/hledat">katalogu</a>.</p>';
    });
})();
