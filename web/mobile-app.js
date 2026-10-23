/* Phone app helpers: catalog filter toggle, active settings pill kept in view. */
(function () {
  var navs = document.querySelector('.set-navs');
  if (navs) {
    var center = function () {
      var on = navs.querySelector('.set-nav.on');
      if (on && navs.scrollWidth > navs.clientWidth) navs.scrollTo({ left: on.offsetLeft - (navs.clientWidth - on.offsetWidth) / 2, behavior: 'auto' });
    };
    new MutationObserver(center).observe(navs, { subtree: true, attributes: true, attributeFilter: ['class'] });
    center();
  }
})();

(function () {
  var filters = document.querySelector('.catalog-filters');
  if (!filters) return;
  var anchor = document.getElementById('cat-place-chips') || filters.querySelector('.catalog-search-wrap');
  if (!anchor) return;

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'm-filter-btn';
  btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = '<span class="m-filter-label">Upřesnit filtry</span><span class="m-filter-count" hidden></span>' +
    '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  anchor.insertAdjacentElement('afterend', btn);

  var count = btn.querySelector('.m-filter-count');
  function refresh() {
    var n = 0;
    filters.querySelectorAll('.catalog-row input').forEach(function (i) { if (i.value) n++; });
    n += filters.querySelectorAll('#cat-hits .chip.on, #cat-dispositions .chip.on, #catalog-views .chip.on').length;
    var sort = document.getElementById('cat-sort');
    if (sort && sort.value && sort.value !== 'newest') n++;
    if (String(n) === count.textContent && count.hidden === !n) return;
    count.hidden = !n;
    count.textContent = n;
  }

  btn.addEventListener('click', function () {
    var open = filters.classList.toggle('m-open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  filters.addEventListener('input', refresh);
  filters.addEventListener('change', refresh);
  filters.addEventListener('click', function () { setTimeout(refresh, 0); });
  new MutationObserver(refresh).observe(filters, { subtree: true, attributes: true, attributeFilter: ['class'] });
  refresh();
})();
