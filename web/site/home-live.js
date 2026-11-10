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
    return '<a class="mock-card" href="' + esc(it.url || '/hledat') + '" target="_blank" rel="noopener noreferrer">' +
      '<div class="thumb">' + thumb + '</div>' +
      '<div class="mock-body"><div class="mock-top"><span class="source">' + esc(it.portal || '') + '</span><strong>' + esc(it.price || '') + '</strong></div>' +
      '<div><h3>' + esc(it.name || 'Byt k pronájmu') + '</h3><p>' + esc(it.locality || '') + '</p></div></div></a>';
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

  fetch('/api/public/landing-listings')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var heading = document.getElementById('db-title');
      if (heading && d.title) heading.textContent = d.title;
      var more = document.querySelector('.live-head .search-btn');
      if (more && d.city) more.setAttribute('href', '/hledat?q=' + encodeURIComponent(d.city) + '&offer=pronajem&price_to=22000');
      var items = (d.items || []).slice(0, 3);
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
