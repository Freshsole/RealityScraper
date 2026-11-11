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

/* Phones: status + hits chips (Aktivní … Zásahy) live at the top of the "Všechny filtry" sheet. */
(function () {
  var mq = window.matchMedia('(max-width: 900px)');
  var status = document.getElementById('cat-status');
  var hits = document.getElementById('cat-hits');
  var scroll = document.querySelector('#filters-modal .filters-scroll');
  if (!status || !hits || !scroll) return;
  var h2 = scroll.querySelector('h2');
  var home = status.parentNode;
  var marker = document.createComment('cat-status-home');
  home.insertBefore(marker, status);
  function place() {
    if (mq.matches) {
      if (status.parentNode !== scroll) {
        h2.insertAdjacentElement('afterend', hits);
        h2.insertAdjacentElement('afterend', status);
      }
    } else if (status.parentNode === scroll) {
      marker.parentNode.insertBefore(status, marker.nextSibling);
      status.insertAdjacentElement('afterend', hits);
    }
  }
  place();
  (mq.addEventListener ? mq.addEventListener('change', place) : mq.addListener(place));
})();

/* Phones: fullscreen map gets a button back to the cards. */
(function () {
  var wrap = document.querySelector('.catalog-map-wrap');
  if (!wrap || wrap.querySelector('.m-map-back')) return;
  var b = document.createElement('button');
  b.type = 'button';
  b.className = 'm-map-back';
  b.textContent = 'Zobrazit nabídky';
  b.addEventListener('click', function () {
    var g = document.querySelector('#catalog-view-switch [data-cv="grid"]');
    if (g) g.click();
  });
  wrap.appendChild(b);
})();

/* Phones, settings: the pill row becomes a burger menu in the top bar (like the marketing site). */
(function () {
  var navs = document.querySelector('.set-navs');
  var end = document.querySelector('.nav-end');
  if (!navs || !end) return;
  var out = document.getElementById('set-logout');

  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'm-burger';
  btn.setAttribute('aria-label', 'Otevřít menu');
  btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = '<span></span>';
  end.appendChild(btn);

  var menu = document.createElement('div');
  menu.className = 'm-set-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'menu');
  document.body.appendChild(menu);

  function build() {
    menu.innerHTML = '';
    navs.querySelectorAll('.set-nav').forEach(function (src) {
      var a = document.createElement('a');
      a.className = 'm-set-item' + (src.classList.contains('on') ? ' on' : '');
      a.href = src.getAttribute('href');
      a.setAttribute('role', 'menuitem');
      a.innerHTML = src.innerHTML;
      menu.appendChild(a);
    });
    if (out) {
      var o = document.createElement('button');
      o.type = 'button';
      o.className = 'm-set-item m-set-out';
      o.innerHTML = out.innerHTML;
      o.addEventListener('click', function () { close(); out.click(); });
      menu.appendChild(o);
    }
  }
  function close() {
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('m-set-open');
  }
  btn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (menu.hidden) {
      build();
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      document.body.classList.add('m-set-open');
    } else close();
  });
  document.addEventListener('click', function (e) { if (!menu.hidden && !menu.contains(e.target)) close(); });
  menu.addEventListener('click', function (e) { if (e.target.closest('a.m-set-item')) close(); });
  window.addEventListener('popstate', close);
  window.addEventListener('pushstate', close);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  window.addEventListener('resize', function () { if (window.innerWidth > 900) close(); });
})();
