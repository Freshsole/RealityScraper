/* Realitify AI chat widget. History lives only in this closure (never stored). Hidden when /api/ai/enabled is 404. */
(function () {
  "use strict";
  var chatId = null;
  var busy = false;
  var els = {};
  var cfg = { logged_in: false, upgrade_url: "/#cenik" };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }

  // Text is inserted as DOM nodes only (never innerHTML): **bold**, "- " bullet lines and http(s)/ /nabidka links.
  var INLINE = /(\*\*[^*]+\*\*|\[[^\]\n]{1,120}\]\((?:https?:\/\/|\/nabidka\?)[^\s)]+\)|https?:\/\/[^\s)]+|\/nabidka\?[^\s)]+)/g;

  function renderInline(parent, text) {
    var last = 0, m;
    INLINE.lastIndex = 0;
    while ((m = INLINE.exec(text))) {
      parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var tok = m[0];
      if (tok.indexOf("**") === 0) {
        parent.appendChild(el("strong", "", tok.slice(2, -2)));
        last = m.index + tok.length;
      } else if (tok.charAt(0) === "[") {
        // [název](adresa): a recommended listing shown as a green link
        var close = tok.lastIndexOf("](");
        var link = el("a", "aic-reco", tok.slice(1, close));
        link.href = tok.slice(close + 2, -1);
        link.target = "_blank";
        link.rel = "noopener";
        parent.appendChild(link);
        last = m.index + tok.length;
      } else {
        var url = tok.replace(/[.,;]+$/, "");
        var a = el("a", "", url.length > 38 ? "odkaz" : url);
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener";
        parent.appendChild(a);
        last = m.index + url.length;
      }
      INLINE.lastIndex = last;
    }
    parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderText(node, text) {
    node.textContent = "";
    text.split("\n").forEach(function (line, i) {
      var bullet = /^\s*(?:[-•*]|\d+[.)])\s+(.*)$/.exec(line);
      if (bullet) {
        var li = el("div", "aic-li");
        renderInline(li, bullet[1]);
        node.appendChild(li);
      } else if (line.trim()) {
        var p = el("div", "aic-p");
        renderInline(p, line);
        node.appendChild(p);
      }
    });
  }

  function money(v) {
    if (typeof v === "number") return v.toLocaleString("cs-CZ") + " Kč";
    return String(v || "");
  }

  function renderCard(item) {
    var card = el("div", "aic-card");
    var pic = el("a", "aic-pic");
    pic.href = item.url;
    pic.target = "_blank";
    pic.rel = "noopener";
    if (item.image_url) {
      var img = el("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = item.image_url;
      img.addEventListener("error", function () { img.remove(); pic.classList.add("no-img"); });
      pic.appendChild(img);
    } else {
      pic.classList.add("no-img");
    }
    card.appendChild(pic);
    var body = el("div", "aic-card-body");
    body.appendChild(el("div", "aic-price", money(item.price)));
    body.appendChild(el("div", "aic-card-title", item.title));
    var meta = [item.disposition, item.area_m2 ? item.area_m2 + " m²" : "", item.locality].filter(Boolean).join(" · ");
    if (meta) body.appendChild(el("div", "aic-meta", meta));
    var actions = el("div", "aic-actions");
    var detail = el("a", "aic-btn", "Detail");
    detail.href = item.url;
    detail.target = "_blank";
    detail.rel = "noopener";
    actions.appendChild(detail);
    if (item.source_url) {
      var src = el("a", "aic-btn ghost", item.portal ? "Na " + item.portal : "Na portálu");
      src.href = item.source_url;
      src.target = "_blank";
      src.rel = "noopener noreferrer";
      actions.appendChild(src);
    }
    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  var CARDS_SHOWN = 5;

  function addCards(items) {
    enterChat();
    var group = el("div", "aic-cards");
    items.forEach(function (item, i) {
      var c = renderCard(item);
      if (i >= CARDS_SHOWN) c.hidden = true;
      group.appendChild(c);
    });
    if (items.length > CARDS_SHOWN) {
      var more = el("button", "aic-more", "Zobrazit další (" + (items.length - CARDS_SHOWN) + ")");
      more.type = "button";
      more.addEventListener("click", function () {
        group.querySelectorAll(".aic-card[hidden]").forEach(function (c) { c.hidden = false; });
        more.remove();
      });
      group.appendChild(more);
    }
    els.log.appendChild(group);
    els.log.scrollTop = els.log.scrollHeight;
  }

  function enterChat() {
    els.panel.classList.add("is-chat");
  }

  function addMsg(role, text) {
    enterChat();
    var n = el("div", "aic-msg " + role);
    renderText(n, text);
    els.log.appendChild(n);
    els.log.scrollTop = els.log.scrollHeight;
    return n;
  }

  function limitMessage(body) {
    enterChat();
    var n = el("div", "aic-msg error");
    n.appendChild(document.createTextNode(body.message || "Denní limit zpráv byl vyčerpán."));
    if (body.upgrade_url) {
      n.appendChild(document.createTextNode(" "));
      var a = el("a", "", "Zobrazit tarify");
      a.href = body.upgrade_url;
      n.appendChild(a);
    }
    els.log.appendChild(n);
    els.log.scrollTop = els.log.scrollHeight;
  }

  async function send(text) {
    busy = true;
    els.send.disabled = true;
    addMsg("user", text);
    enterChat();
    var typing = el("div", "aic-typing");
    typing.setAttribute("aria-label", "píše…");
    typing.innerHTML = "<i></i><i></i><i></i>";
    els.log.appendChild(typing);
    els.log.scrollTop = els.log.scrollHeight;
    var bubble = null, answer = "", navigateTo = null, uiEvents = [];
    try {
      var res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ message: text, chat_id: chatId, ui: uiState() }),
      });
      if (res.status === 429) {
        typing.remove();
        limitMessage(await res.json().catch(function () { return {}; }));
        return;
      }
      if (!res.ok) throw new Error("http " + res.status);
      var reader = res.body.getReader();
      var dec = new TextDecoder();
      var buf = "";
      for (;;) {
        var r = await reader.read();
        if (r.done) break;
        buf += dec.decode(r.value, { stream: true });
        var parts = buf.split("\n\n");
        buf = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          if (parts[i].indexOf("data: ") !== 0) continue;
          var ev = JSON.parse(parts[i].slice(6));
          if (ev.type === "chat") {
            chatId = ev.id;
          } else if (ev.type === "delta") {
            if (!bubble) { typing.remove(); bubble = addMsg("assistant", ""); }
            answer += ev.text;
            renderText(bubble, answer);
            els.log.scrollTop = els.log.scrollHeight;
          } else if (ev.type === "cards") {
            typing.remove();
            addCards(ev.items || []);
            els.log.appendChild(typing);
          } else if (ev.type === "confirm") {
            typing.remove();
            addConfirm(ev.action_id, ev.summary);
            els.log.appendChild(typing);
          } else if (ev.type === "navigate") {
            navigateTo = ev.path;
          } else if (ev.type === "ui") {
            uiEvents.push(ev);
          } else if (ev.type === "error") {
            typing.remove();
            if (ev.chat_deleted) chatId = null;
            addMsg("error", ev.message || "Něco se nepovedlo.");
          }
        }
      }
    } catch (e) {
      addMsg("error", "Bára se nemohla spojit. Zkuste to prosím znovu.");
    } finally {
      typing.remove();
      busy = false;
      els.send.disabled = false;
      refreshRecent();
    }
    var uiPath = await runUiEvents(uiEvents);
    if (uiPath) goTo(uiPath);
    else if (navigateTo) goTo(navigateTo);
  }

  // What the user currently sees (sent with each message so Bára can refer to "this flat", the filters, the comparison).
  function uiState() {
    try {
      if (window.RealitifyUI && typeof window.RealitifyUI.state === "function") return window.RealitifyUI.state();
    } catch (e) {}
    return { page: location.pathname };
  }

  // A command that needs the catalog page waits in sessionStorage until the page has loaded there.
  function stashUi(ev) {
    try {
      var list = JSON.parse(sessionStorage.getItem("aic_pending_ui") || "[]");
      list.push(ev);
      sessionStorage.setItem("aic_pending_ui", JSON.stringify(list.slice(-6)));
    } catch (e) {}
  }

  function runPendingUi() {
    var list = [];
    try {
      list = JSON.parse(sessionStorage.getItem("aic_pending_ui") || "[]");
      sessionStorage.removeItem("aic_pending_ui");
    } catch (e) {}
    if (!list.length || location.pathname.replace(/\/$/, "") !== "/nabidka") return;
    var tries = 0;
    var timer = setInterval(function () {
      tries++;
      if (window.RealitifyUI && window.RealitifyUI.ready && window.RealitifyUI.ready()) {
        clearInterval(timer);
        runUiEvents(list);
      } else if (tries > 40) clearInterval(timer);
    }, 250);
  }

  // Apply UI commands from Bára. Returns a path to navigate to when the current page cannot do it.
  async function runUiEvents(list) {
    var api = window.RealitifyUI;
    var fallback = null;
    for (var i = 0; i < list.length; i++) {
      var ev = list[i];
      try {
        if (ev.op === "catalog_filters") {
          var done = api && (await api.applyFilters(ev.params || {}, !!ev.keep_current));
          if (!done) {
            var q = new URLSearchParams(ev.params || {}).toString();
            fallback = "/nabidka" + (q ? "?" + q : "");
          }
        } else if (ev.op === "view") {
          if (api && api.setView && location.pathname.replace(/\/$/, "") === "/nabidka") api.setView(ev.mode);
          else { stashUi(ev); if (!fallback) fallback = "/nabidka"; }
        } else if (ev.op === "compare") {
          if (api && api.compare && location.pathname.replace(/\/$/, "") === "/nabidka") api.compare(ev.action, ev.urls || []);
          else { stashUi(ev); if (!fallback) fallback = "/nabidka"; }
        } else if (ev.op === "open_listing") {
          if (api && api.openListing && location.pathname.replace(/\/$/, "") === "/nabidka") await api.openListing(ev);
          else {
            var p = new URLSearchParams();
            if (ev.listing_key) p.set("listing_key", ev.listing_key);
            else if (ev.url) p.set("url", ev.url);
            else if (ev.id) p.set("id", ev.id);
            fallback = "/nabidka?" + p.toString();
          }
        }
      } catch (e) {}
    }
    return fallback;
  }

  // Only relative paths on this site. The server whitelists the pages too; this is the second check.
  function goTo(path) {
    if (typeof path !== "string" || path.charAt(0) !== "/" || path.charAt(1) === "/" || path.indexOf("\\") >= 0 || path.indexOf("://") >= 0) return;
    try { sessionStorage.setItem("aic_resume", JSON.stringify({ chat: chatId })); } catch (e) {}
    window.location.assign(path);
  }

  // ---- confirmation cards for actions Bára proposes ---------------------------------------------------------

  async function answerAction(card, id, verb, status) {
    var buttons = card.querySelectorAll("button");
    buttons.forEach(function (b) { b.disabled = true; });
    var out = card.querySelector(".aic-confirm-out");
    try {
      var res = await fetch("/api/chat/actions/" + encodeURIComponent(id) + "/" + verb, { method: "POST", credentials: "same-origin" });
      var data = await res.json().catch(function () { return {}; });
      card.classList.add("done", data.status === "executed" || data.status === "cancelled" ? "ok" : "bad");
      out.textContent = res.ok ? (data.status === "executed" ? "✓ " : "") + (data.message || "") : (data.detail || "Akci se nepodařilo vyřídit.");
      card.querySelector(".aic-confirm-actions").remove();
    } catch (e) {
      out.textContent = "Spojení se nepovedlo. Zkuste to znovu.";
      buttons.forEach(function (b) { b.disabled = false; });
    }
    refreshRecent();
  }

  function addConfirm(id, summary) {
    enterChat();
    var card = el("div", "aic-confirm");
    card.appendChild(el("div", "aic-confirm-h", "Bára navrhuje"));
    card.appendChild(el("div", "aic-confirm-sum", summary));
    card.appendChild(el("div", "aic-confirm-out"));
    var row = el("div", "aic-confirm-actions");
    var yes = el("button", "aic-btn", "Potvrdit");
    yes.type = "button";
    yes.addEventListener("click", function () { answerAction(card, id, "confirm"); });
    var no = el("button", "aic-btn ghost", "Zrušit");
    no.type = "button";
    no.addEventListener("click", function () { answerAction(card, id, "cancel"); });
    row.appendChild(yes);
    row.appendChild(no);
    card.appendChild(row);
    els.log.appendChild(card);
    els.log.scrollTop = els.log.scrollHeight;
  }

  // ---- saved chats ----------------------------------------------------------------------------------------

  function when(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return "";
    var today = new Date();
    if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
    return d.toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric" });
  }

  function newChat() {
    if (busy) return;
    chatId = null;
    els.log.textContent = "";
    els.panel.classList.remove("is-chat");
    els.panel.classList.remove("show-history");
    refreshRecent();
  }

  async function openChat(id) {
    if (busy) return;
    var res = await fetch("/api/chats/" + encodeURIComponent(id), { credentials: "same-origin" });
    if (!res.ok) return;
    var chat = await res.json();
    chatId = chat.id;
    els.log.textContent = "";
    els.panel.classList.remove("show-history");
    chat.messages.forEach(function (m) {
      if (m.cards && m.cards.length) addCards(m.cards);
      if (m.content) addMsg(m.role, m.content);
    });
    (chat.pending || []).forEach(function (p) { addConfirm(p.action_id, p.summary); });
    enterChat();
    els.log.scrollTop = els.log.scrollHeight;
  }

  async function deleteChat(id) {
    if (!window.confirm("Smazat tuto konverzaci?")) return;
    var res = await fetch("/api/chats/" + encodeURIComponent(id), { method: "DELETE", credentials: "same-origin" });
    if (res.ok && id === chatId) newChat();
    loadHistory();
    refreshRecent();
  }

  function chatRow(chat, withDelete) {
    var row = el("div", "aic-row");
    var open = el("button", "aic-row-open");
    open.type = "button";
    open.appendChild(el("span", "aic-row-title", chat.title));
    open.appendChild(el("span", "aic-row-when", when(chat.updated_at)));
    open.addEventListener("click", function () { openChat(chat.id); });
    row.appendChild(open);
    if (withDelete) {
      var del = el("button", "aic-row-del", "×");
      del.type = "button";
      del.setAttribute("aria-label", "Smazat konverzaci");
      del.addEventListener("click", function () { deleteChat(chat.id); });
      row.appendChild(del);
    }
    return row;
  }

  async function fetchChats() {
    try {
      var res = await fetch("/api/chats", { credentials: "same-origin" });
      return res.ok ? (await res.json()).items || [] : [];
    } catch (e) {
      return [];
    }
  }

  async function refreshRecent() {
    if (!els.recent) return;
    var items = (await fetchChats()).slice(0, 2);
    els.recent.textContent = "";
    els.recentWrap.hidden = !items.length;
    items.forEach(function (c) { els.recent.appendChild(chatRow(c, false)); });
  }

  async function loadHistory() {
    var items = await fetchChats();
    els.hist.textContent = "";
    if (!items.length) els.hist.appendChild(el("p", "aic-empty", "Zatím tu nejsou žádné konverzace."));
    items.forEach(function (c) { els.hist.appendChild(chatRow(c, true)); });
  }

  var SUGGESTIONS = [
    ["2+kk v Praze 7", "do 25 tisíc"],
    ["Kolik stojí nájem v Brně?", "průměr a medián"],
    ["Je 25 000 Kč za 2+kk v Brně férová cena?", ""],
    ["Co je nového v Ostravě?", "za posledních 24 hodin"],
  ];

  function build() {
    var fab = el("button", "aic-fab");
    fab.type = "button";
    fab.setAttribute("aria-label", "Otevřít asistentku Báru");
    var fdog = el("span", "aic-fab-dog");
    var fimg = el("img");
    fimg.src = "/static/site/assets/ai-dog.webp";
    fimg.alt = "";
    fimg.width = 48;
    fdog.appendChild(fimg);
    fab.appendChild(fdog);
    fab.appendChild(document.createTextNode("Zeptat se Báry"));

    var panel = el("div", "aic-panel");
    panel.hidden = true;
    els.panel = panel;

    var hero = el("div", "aic-hero");
    ["b1", "b2", "b3", "b4", "b5"].forEach(function (c) { hero.appendChild(el("span", "aic-blob " + c)); });
    var head = el("div", "aic-head");
    head.appendChild(el("span", "aic-title", "Bára · Realitify"));
    var tools = el("div", "aic-tools");
    var hbtn = null, nbtn = null;
    if (cfg.logged_in) {
      hbtn = el("button", "aic-ico");
      hbtn.type = "button";
      hbtn.title = "Historie konverzací";
      hbtn.setAttribute("aria-label", "Historie konverzací");
      hbtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
      nbtn = el("button", "aic-ico");
      nbtn.type = "button";
      nbtn.title = "Nová konverzace";
      nbtn.setAttribute("aria-label", "Nová konverzace");
      nbtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
      tools.appendChild(hbtn);
      tools.appendChild(nbtn);
    }
    var close = el("button", "aic-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Zavřít");
    tools.appendChild(close);
    head.appendChild(tools);
    hero.appendChild(head);
    var dog = el("div", "aic-dog");
    var img = el("img");
    img.src = "/static/site/assets/ai-dog.webp";
    img.alt = "";
    dog.appendChild(img);
    hero.appendChild(dog);
    panel.appendChild(hero);

    var body = el("div", "aic-body");
    if (!cfg.logged_in) {
      var wel = el("div", "aic-welcome");
      wel.appendChild(el("h2", "", "Ahoj, jsem Bára"));
      wel.appendChild(el("p", "", "Pomůžu vám najít byt, porovnat ceny a ohlídat lokality. Pro používání se zaregistrujte zdarma."));
      body.appendChild(wel);
      var cta = el("div", "aic-cta");
      var link = el("a", "", "Zaregistrovat se");
      link.href = "/registrace?next=" + encodeURIComponent(location.pathname + location.search);
      cta.appendChild(link);
      body.appendChild(cta);
    } else {
      var welcome = el("div", "aic-welcome");
      welcome.appendChild(el("h2", "", "Ahoj, jsem Bára"));
      welcome.appendChild(el("p", "", "Pomůžu vám najít byt, porovnat ceny nebo ohlídat lokalitu. Na co se chcete zeptat?"));
      var chips = el("div", "aic-chips");
      SUGGESTIONS.forEach(function (sug) {
        var chip = el("button", "aic-chip", sug[0]);
        chip.type = "button";
        if (sug[1]) chip.appendChild(el("small", "", sug[1]));
        chip.addEventListener("click", function () {
          if (!busy) send(sug[1] && sug[0].indexOf("?") < 0 ? sug[0] + " " + sug[1] : sug[0]);
        });
        chips.appendChild(chip);
      });
      welcome.appendChild(chips);
      els.recentWrap = el("div", "aic-recent");
      els.recentWrap.hidden = true;
      els.recentWrap.appendChild(el("p", "aic-recent-h", "Poslední konverzace"));
      els.recent = el("div", "aic-recent-list");
      els.recentWrap.appendChild(els.recent);
      welcome.appendChild(els.recentWrap);
      body.appendChild(welcome);

      els.hist = el("div", "aic-hist");
      var histWrap = el("div", "aic-histwrap");
      histWrap.appendChild(el("p", "aic-recent-h", "Vaše konverzace"));
      histWrap.appendChild(els.hist);
      body.appendChild(histWrap);

      els.log = el("div", "aic-log");
      body.appendChild(els.log);

      var form = el("form", "aic-form");
      var ta = el("textarea");
      ta.placeholder = "Zeptejte se Báry…";
      ta.maxLength = 1000;
      ta.rows = 1;
      els.send = el("button", "aic-send");
      els.send.type = "submit";
      els.send.setAttribute("aria-label", "Odeslat");
      els.send.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
      form.appendChild(ta);
      form.appendChild(els.send);
      // The input grows with the text (up to the CSS max-height, then it scrolls).
      function fit() {
        ta.style.height = "24px";
        ta.style.height = Math.min(ta.scrollHeight, 120) + "px";
      }
      ta.addEventListener("input", fit);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = ta.value.trim();
        if (!text || busy) return;
        ta.value = "";
        fit();
        send(text);
      });
      ta.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
      });
      body.appendChild(form);
    }
    panel.appendChild(body);

    if (hbtn) {
      hbtn.addEventListener("click", function () {
        var on = panel.classList.toggle("show-history");
        if (on) loadHistory();
      });
      nbtn.addEventListener("click", newChat);
    }
    fab.addEventListener("click", function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) refreshRecent();
    });
    close.addEventListener("click", function () { panel.hidden = true; });
    document.body.appendChild(panel);
    document.body.appendChild(fab);
    // Buttons elsewhere on the page ([data-ai-open]) open the chat; they stay hidden while the AI is off.
    document.querySelectorAll("[data-ai-open]").forEach(function (b) {
      b.hidden = false;
      b.addEventListener("click", function () {
        panel.hidden = false;
        refreshRecent();
        var t = panel.querySelector("textarea");
        if (t) t.focus();
      });
    });
  }

  fetch("/api/ai/enabled", { credentials: "same-origin" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.enabled) return;
      cfg = data;
      build();
      var resume = null;
      try {
        resume = JSON.parse(sessionStorage.getItem("aic_resume") || "null");
        sessionStorage.removeItem("aic_resume");
      } catch (e) {}
      if (resume && resume.chat && cfg.logged_in) {
        els.panel.hidden = false;
        openChat(resume.chat);
      }
      runPendingUi();
    })
    .catch(function () {});
})();
