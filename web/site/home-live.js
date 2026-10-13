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

  fetch('/api/public/landing-listings')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var heading = document.getElementById('db-title');
      if (heading && d.title) heading.textContent = d.title;
      var items = (d.items || []).slice(0, 3);
      if (!items.length) {
        results.innerHTML = '<p class="live-empty">Aktuální nabídky najdete v <a href="/hledat">katalogu</a>.</p>';
        return;
      }
      results.innerHTML = items.map(card).join('');
    })
    .catch(function () {
      results.innerHTML = '<p class="live-empty">Aktuální nabídky najdete v <a href="/hledat">katalogu</a>.</p>';
    });
})();
