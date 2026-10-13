/* /hledat: e-mail gate in front of the catalog frame. */
(function () {
  'use strict';
  var gate = document.getElementById('gate');
  var banner = document.getElementById('hl-banner');
  var wrap = document.getElementById('katalog');
  var frame = document.getElementById('hl-frame');
  var form = document.getElementById('hl-form');
  var input = document.getElementById('hl-email');
  var msg = document.getElementById('hl-msg');
  if (!gate || !wrap || !frame || !form) return;

  function show(unlocked, member) {
    gate.hidden = unlocked;
    wrap.hidden = !unlocked;
    if (banner) banner.hidden = !unlocked || !!member;
    if (unlocked && !frame.getAttribute('src')) {
      frame.setAttribute('src', '/hledat/katalog' + location.search);
    }
  }

  fetch('/api/guest/status', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (d) { show(!!d.unlocked, !!d.member); })
    .catch(function () { show(false); });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var email = (input.value || '').trim();
    msg.textContent = '';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@.]{2,}$/.test(email)) { msg.textContent = 'Zadejte prosím platný e-mail.'; input.focus(); return; }
    var btn = form.querySelector('button');
    btn.disabled = true;
    fetch('/api/guest/access', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.d.detail || 'Něco se nepovedlo, zkuste to prosím znovu.');
      show(true);
    }).catch(function (err) {
      msg.textContent = err.message;
    }).then(function () { btn.disabled = false; });
  });
})();
