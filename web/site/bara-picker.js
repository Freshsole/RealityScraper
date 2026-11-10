/* Avatar picker for the watchdog "Bára": 110 presets + own upload. Saves immediately via /api/auth/avatar. */
(function () {
  "use strict";
  var COUNT = 110;
  var BASE = "/static/site/assets/bara/";

  function pad(n) { return String(n).padStart(3, "0"); }

  function post(body) {
    return fetch("/api/auth/avatar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Avatar se nepodařilo uložit.");
        return d;
      });
    });
  }

  function fileToDataUrl(file) {
    return new Promise(function (resolve, reject) {
      if (!/^image\//.test(file.type)) { reject(new Error("Nahrajte obrázek (PNG, JPG nebo WebP).")); return; }
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        var s = Math.min(img.naturalWidth, img.naturalHeight);
        var c = document.createElement("canvas");
        c.width = c.height = 256;
        c.getContext("2d").drawImage(img, (img.naturalWidth - s) / 2, (img.naturalHeight - s) / 2, s, s, 0, 0, 256, 256);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/webp", 0.9));
      };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("Obrázek se nepodařilo přečíst.")); };
      img.src = url;
    });
  }

  function mount(root, opts) {
    opts = opts || {};
    var current = opts.current || "";
    root.classList.add("bara-picker");
    root.innerHTML =
      '<div class="bara-side">' +
        '<div class="bara-preview" data-preview></div>' +
        '<p class="bara-name">Bára</p>' +
        '<button type="button" class="bara-shuffle" data-shuffle>Náhodný výběr</button>' +
        '<button type="button" class="bara-upload" data-upload>Nahrát vlastní</button>' +
        '<p class="bara-msg" data-msg role="status" aria-live="polite"></p>' +
        '<input type="file" accept="image/*" hidden data-file />' +
      '</div>' +
      '<div class="bara-main">' +
        '<div class="bara-grid" role="listbox" aria-label="Avatary hlídacího psa Báry" data-grid></div>' +
      '</div>';
    var grid = root.querySelector("[data-grid]");
    var preview = root.querySelector("[data-preview]");
    var msg = root.querySelector("[data-msg]");
    var file = root.querySelector("[data-file]");
    var html = "";
    for (var i = 1; i <= COUNT; i++) {
      html += '<button type="button" role="option" class="bara-opt" data-n="' + i + '" aria-label="Bára ' + i + '">' +
        '<img src="' + BASE + pad(i) + '.webp" width="64" height="64" alt="" loading="lazy" decoding="async" /></button>';
    }
    grid.innerHTML = html;

    function paint() {
      // No choice yet = the first preset (the server treats an empty avatar the same way).
      var shown = current || BASE + pad(1) + ".webp";
      preview.innerHTML = '<img src="' + shown + '" alt="" />';
      var m = /\/bara\/(\d{3})\.webp$/.exec(shown);
      grid.querySelectorAll(".bara-opt").forEach(function (b) {
        var on = !!m && Number(m[1]) === Number(b.dataset.n);
        b.classList.toggle("on", on);
        b.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
    function say(t, bad) { msg.textContent = t || ""; msg.classList.toggle("is-bad", !!bad); }
    function apply(promise) {
      say("Ukládám…");
      return promise.then(function (d) {
        current = d.avatar || "";
        paint();
        say("");
        if (opts.onChange) opts.onChange(current);
      }).catch(function (e) { say(e.message || "Avatar se nepodařilo uložit.", true); });
    }

    grid.addEventListener("click", function (e) {
      var b = e.target.closest(".bara-opt");
      if (b) apply(post({ preset: Number(b.dataset.n) }));
    });
    root.querySelector("[data-shuffle]").addEventListener("click", function () {
      apply(post({ preset: 1 + Math.floor(Math.random() * COUNT) }));
    });
    root.querySelector("[data-upload]").addEventListener("click", function () { file.click(); });
    file.addEventListener("change", function () {
      var f = file.files && file.files[0];
      file.value = "";
      if (!f) return;
      apply(fileToDataUrl(f).then(function (u) { return post({ image: u }); }));
    });
    paint();
    return { set: function (url) { current = url || ""; paint(); } };
  }

  window.BaraPicker = { mount: mount, count: COUNT };
})();
