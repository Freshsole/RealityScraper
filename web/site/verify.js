/* /overeni?token=... : confirms the e-mail address and sends the visitor on. */
(function () {
  'use strict';
  var title = document.getElementById('vr-title');
  var lead = document.getElementById('vr-lead');
  var action = document.getElementById('vr-action');
  var resend = document.getElementById('vr-resend');
  var token = new URLSearchParams(location.search).get('token') || '';

  function me() {
    return fetch('/api/auth/me').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }
  function storedNext() {
    try { return localStorage.getItem('rf_register_next') || ''; } catch (e) { return ''; }
  }

  function fail(message) {
    title.textContent = 'ODKAZ NEFUNGUJE';
    lead.textContent = message;
    me().then(function (profile) {
      if (profile && !profile.email_verified) {
        resend.hidden = false;
      } else {
        action.textContent = 'Přihlásit se';
        action.href = '/prihlaseni';
        action.hidden = false;
      }
    });
  }

  if (!token) { fail('V odkazu chybí potvrzovací kód. Otevřete odkaz z e-mailu znovu.'); }
  else {
    fetch('/api/auth/verify?token=' + encodeURIComponent(token))
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.d.detail || 'Odkaz je neplatný nebo vypršel.');
        title.textContent = 'E-MAIL JE POTVRZENÝ';
        lead.textContent = 'Děkujeme. Pokračujeme v nastavení vašeho hlídání.';
        return me().then(function (profile) {
          var next = storedNext();
          var href = profile
            ? '/registrace?resume=1' + (next ? '&next=' + encodeURIComponent(next) : '')
            : '/prihlaseni' + (next ? '?next=' + encodeURIComponent(next) : '');
          action.href = href;
          action.textContent = profile ? 'Pokračovat v registraci' : 'Přihlásit se';
          action.hidden = false;
          setTimeout(function () { location.href = href; }, 2200);
        });
      })
      .catch(function (err) { fail(err.message); });
  }

  resend.addEventListener('click', function () {
    resend.disabled = true;
    fetch('/api/auth/verify/resend', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        lead.textContent = res.ok ? 'Poslali jsme nový odkaz. Zkontrolujte e-mail.' : (res.d.detail || 'Odkaz se nepodařilo poslat.');
        if (!res.ok) resend.disabled = false;
      })
      .catch(function () { resend.disabled = false; });
  });
})();
