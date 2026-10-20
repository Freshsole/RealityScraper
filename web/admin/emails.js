(() => {
  window.AdEmails = (ctx) => {
    const { api, esc, main, fmtN } = ctx;
    const BASE = "/api/admin/emails";
    const PAGE_SIZE = 25;
    const STREAMS = { transactional: "Transakční", alerts: "Alerty", lifecycle: "Lifecycle" };
    const STATUS = {
      queued: "Ve frontě",
      sending: "Odesílá se",
      sent: "Odesláno",
      failed: "Chyba",
      skipped: "Přeskočeno",
      canceled: "Zrušeno",
    };
    const CSTATUS = { draft: "Koncept", scheduled: "Naplánováno", sending: "Odesílá se", sent: "Odesláno", canceled: "Zrušeno" };
    const SKIP = {
      marketing_unsub: "odhlášen z tipů a novinek",
      disabled: "šablona vypnuta",
      no_consent: "bez souhlasu",
      smtp_not_configured: "SMTP není nastavené",
    };

    const toastEl = () => document.getElementById("ad-toast");
    let toastTimer = null;
    const toast = (msg, isErr) => {
      const el = toastEl();
      if (!el) return;
      el.textContent = msg;
      el.hidden = false;
      el.classList.toggle("is-err", !!isErr);
      requestAnimationFrame(() => el.classList.add("is-on"));
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => el.classList.remove("is-on"), 3200);
    };
    const post = (path, body) => api(BASE + path, { method: "POST", body: JSON.stringify(body || {}) });
    const get = (path) => api(BASE + path);

    const fmtDT = (v) => {
      if (!v) return "—";
      const d = new Date(v);
      if (Number.isNaN(d.getTime())) return "—";
      return d.toLocaleString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
    };
    const n = (v) => fmtN(v ?? 0);
    const pill = (cls, text) => `<span class="em-pill em-${esc(cls)}">${esc(text)}</span>`;
    const statusPill = (s, err) =>
      pill(s, STATUS[s] || s) + (err ? ` <span class="em-sub" title="${esc(err)}">${esc(SKIP[err] || err)}</span>` : "");
    const val = (root, name) => (root.querySelector(`[name="${name}"]`) || {}).value || "";
    const subPath = () => location.pathname.replace(/^\/admin\/emaily\/?/, "").replace(/\/+$/, "");

    const tabs = (active) => `
      <nav class="em-tabs">
        <a href="/admin/emaily" class="${active === "sablony" ? "is-on" : ""}">Šablony</a>
        <a href="/admin/emaily/log" class="${active === "log" ? "is-on" : ""}">Log odesílání</a>
        <a href="/admin/emaily/kampane" class="${active === "kampane" ? "is-on" : ""}">Kampaně</a>
      </nav>`;

    const statusBar = (st) => {
      if (!st) return "";
      const s = st.senders || {};
      const q = st.queue || {};
      return `<div class="em-status">
        ${st.only_transactional ? `<span class="em-paused">Pozastaveno: odchází jen transakční e-maily</span>` : ""}
        <span>${pill(st.smtp_configured ? "sent" : "failed", st.smtp_configured ? "SMTP připraveno" : "SMTP není nastavené")}</span>
        <span>Fronta: <b>${n(q.queued)}</b></span>
        <span>Chyby za 24 h: <b>${n(q.failed_24h)}</b></span>
        <span class="em-sub">Odesílatelé: ${Object.entries(s)
          .map(([k, v]) => `${esc(STREAMS[k] || k)}: ${esc(v)}`)
          .join(" · ")}</span>
      </div>
      ${verifyCard(st.verification)}`;
    };

    const verifyCard = (v) => {
      if (!v) return "";
      const on = v.required !== false;
      const meta = v.updated_at
        ? `Naposledy změnil ${esc(v.updated_by || "admin")} · ${esc(new Date(v.updated_at).toLocaleString("cs-CZ"))}`
        : "";
      return `<div class="em-verify ${on ? "" : "is-off"}">
        <div>
          <b>Povinné ověření e-mailu při registraci</b>
          <span class="em-sub">Vypnutí: uživatelé se dostanou do appky hned, e-mail zůstane neověřený (slabší ochrana proti falešným účtům). ${meta}</span>
        </div>
        <span class="em-pill ${on ? "em-sent" : "em-failed"}">${on ? "Zapnuto" : "Vypnuto"}</span>
        <button type="button" class="ad-btn ghost tiny" data-em-verify="${on ? "0" : "1"}">${on ? "Vypnout" : "Zapnout"}</button>
      </div>`;
    };

    main.addEventListener("click", async (ev) => {
      const btn = ev.target.closest("[data-em-verify]");
      if (!btn) return;
      const required = btn.dataset.emVerify === "1";
      if (!required && !window.confirm("Opravdu vypnout povinné ověření e-mailu? Noví uživatelé se dostanou do appky bez potvrzení e-mailu.")) return;
      btn.disabled = true;
      try {
        await api("/api/admin/settings/email-verification", { method: "POST", body: JSON.stringify({ required }) });
        toast(required ? "Ověření e-mailu je povinné." : "Ověření e-mailu je vypnuté.");
        setTimeout(() => location.reload(), 600);
      } catch (err) {
        toast(err.message || "Uložení se nepovedlo.", true);
        btn.disabled = false;
      }
    });

    const head = (title, lead, active, st) => `
      <header class="ad-pagehead">
        <h1 class="ad-h">${esc(title)}</h1>
        <p class="ad-lead">${lead}</p>
      </header>
      ${statusBar(st)}
      ${tabs(active)}`;

    const copyText = async (text) => {
      try {
        await navigator.clipboard.writeText(text);
        toast(`Zkopírováno: ${text}`);
      } catch {
        toast("Kopírování se nepodařilo.", true);
      }
    };

    const debounce = (fn, ms) => {
      let t;
      return (...a) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...a), ms);
      };
    };

    const pager = (page, total, size) => {
      const pages = Math.max(1, Math.ceil(total / size));
      return `<div class="em-pager"><button class="ad-btn ghost tiny" data-pg="-1" ${page <= 1 ? "disabled" : ""}>Předchozí</button>
        <span>${page} / ${pages} · ${n(total)} záznamů</span>
        <button class="ad-btn ghost tiny" data-pg="1" ${page >= pages ? "disabled" : ""}>Další</button></div>`;
    };

    const previewFrame = (id) =>
      `<iframe id="${id}" class="em-frame" sandbox="" title="Náhled e-mailu"></iframe>`;
    const setFrame = (el, html) => {
      if (el) el.srcdoc = html || "<p style='font-family:sans-serif;color:#888;padding:16px'>Bez náhledu</p>";
    };

    /* ------------------------------------------------------------ šablony */
    async function pageList() {
      const [data, st] = await Promise.all([get("/templates"), get("/status").catch(() => null)]);
      const groups = data.groups || {};
      const rows = data.templates || [];
      const byGroup = {};
      rows.forEach((t) => (byGroup[t.group] = byGroup[t.group] || []).push(t));
      const section = (g) => {
        const items = byGroup[g] || [];
        if (!items.length) return "";
        return `<section class="em-group">
          <h2 class="ad-kicker">${esc(g)}. ${esc(groups[g] || items[0].group_name || "")}</h2>
          <article class="ad-card em-card-flush">
            <div class="ad-utbl-wrap"><div class="em-tbl">
              <div class="em-tr em-th"><span>Šablona</span><span>Stream</span><span>Stav</span><span>Odesláno</span><span>Otevřeno</span><span>Poslední</span></div>
              ${items
                .map((t) => {
                  const s = t.stats || {};
                  return `<a class="em-tr" href="/admin/emaily/sablony/${esc(t.key)}">
                    <span class="em-name"><b>${esc(t.number)} ${esc(t.name)}</b><small>${esc(t.trigger || "")}</small></span>
                    <span>${esc(STREAMS[t.stream] || t.stream)}</span>
                    <span>${pill(t.enabled ? "sent" : "canceled", t.enabled ? "Zapnuto" : "Vypnuto")} ${pill(t.customized ? "queued" : "draft", t.customized ? "Upraveno" : "Výchozí")}</span>
                    <span>${n(s.sent)}${s.failed ? ` <em class="em-err">/ ${n(s.failed)} chyb</em>` : ""}</span>
                    <span>${n(s.opened)}${s.sent ? ` <small>(${Math.round((100 * (s.opened || 0)) / s.sent)} %)</small>` : ""}</span>
                    <span>${esc(fmtDT(s.last_sent_at))}</span>
                  </a>`;
                })
                .join("")}
            </div></div>
          </article></section>`;
      };
      main.innerHTML = `${head("E-maily", "Správa všech automatických e-mailů: texty, vypínání, odesílání a log.", "sablony", st)}
        <div class="em-groups">${Object.keys(groups).sort().map(section).join("")}</div>`;
    }

    async function pageTemplate(key) {
      const t = await get(`/templates/${encodeURIComponent(key)}`);
      const vars = t.variables || [];
      const common = t.common_variables || [];
      const varChip = (v) =>
        `<button type="button" class="em-var" data-var="${esc(v.name)}" data-raw="${v.raw ? 1 : 0}" title="${esc(v.description || "")}${v.sample ? " · např. " + esc(v.sample) : ""}">{{${v.raw ? "{" : ""}${esc(v.name)}${v.raw ? "}" : ""}}}</button>`;
      main.innerHTML = `
        <header class="ad-pagehead">
          <a class="ad-back" href="/admin/emaily">← Všechny šablony</a>
          <h1 class="ad-h">${esc(t.number)} ${esc(t.name)}</h1>
          <p class="ad-lead">${esc(STREAMS[t.stream] || t.stream)} · ${esc(t.from || "")}<br><b>Trigger:</b> ${esc(t.trigger || "—")}${t.condition ? `<br><b>Podmínka:</b> ${esc(t.condition)}` : ""}</p>
        </header>
        <div class="em-edit" id="em-edit">
          <article class="ad-card em-form">
            <label class="em-toggle"><input type="checkbox" name="enabled" ${t.enabled ? "checked" : ""} /> <span>Šablona je zapnutá (vypnutá se neodesílá, v logu bude „přeskočeno“)</span></label>
            <label class="cms-field"><span>Předmět</span><input name="subject" value="${esc(t.subject)}" /></label>
            <label class="cms-field"><span>Preheader</span><input name="preheader" value="${esc(t.preheader)}" /></label>
            <label class="cms-field"><span>HTML</span><textarea name="html" rows="22" spellcheck="false" class="em-code">${esc(t.html)}</textarea></label>
            <label class="cms-field"><span>Textová verze (prázdné = odvodí se z HTML)</span><textarea name="text_body" rows="6" spellcheck="false" class="em-code">${esc(t.text_body || "")}</textarea></label>
            <div class="em-vars"><span class="ad-kicker">Proměnné šablony</span><div>${vars.map(varChip).join("") || "<small>žádné</small>"}</div>
              <span class="ad-kicker">Společné proměnné</span><div>${common.map(varChip).join("")}</div>
              <small>Klik zkopíruje. <code>{{x}}</code> je escapované, <code>{{{x}}}</code> raw HTML.</small></div>
            <div class="em-actions">
              <button class="ad-btn" id="em-save">Uložit</button>
              <button class="ad-btn ghost" id="em-reset">Obnovit výchozí</button>
              <span class="em-test"><input id="em-test-to" type="email" placeholder="test@adresa.cz" /><button class="ad-btn soft" id="em-test">Poslat test</button></span>
            </div>
          </article>
          <article class="ad-card em-preview">
            <div class="em-prev-head"><span class="ad-kicker">Živý náhled</span><span class="em-sub" id="em-prev-subj"></span></div>
            ${previewFrame("em-frame")}
          </article>
        </div>`;
      const root = main.querySelector("#em-edit");
      const frame = main.querySelector("#em-frame");
      const collect = () => ({
        subject: val(root, "subject"),
        preheader: val(root, "preheader"),
        html: val(root, "html"),
        text_body: val(root, "text_body"),
        enabled: root.querySelector('[name="enabled"]').checked,
      });
      const refresh = debounce(async () => {
        try {
          const p = collect();
          const r = await post(`/templates/${encodeURIComponent(key)}/preview`, { subject: p.subject, preheader: p.preheader, html: p.html });
          setFrame(frame, r.html);
          main.querySelector("#em-prev-subj").textContent = r.subject || "";
        } catch (err) {
          toast(err.message || "Náhled se nepodařil.", true);
        }
      }, 350);
      root.addEventListener("input", refresh);
      refresh();
      root.querySelectorAll(".em-var").forEach((b) =>
        b.addEventListener("click", () => copyText(b.textContent)),
      );
      main.querySelector("#em-save").addEventListener("click", async () => {
        try {
          await post(`/templates/${encodeURIComponent(key)}`, collect());
          toast("Šablona uložena.");
        } catch (err) {
          toast(err.message || "Uložení se nepodařilo.", true);
        }
      });
      main.querySelector("#em-reset").addEventListener("click", async () => {
        if (!confirm("Smazat vlastní úpravy a vrátit výchozí šablonu?")) return;
        try {
          await post(`/templates/${encodeURIComponent(key)}/reset`);
          toast("Obnoveno na výchozí.");
          await pageTemplate(key);
        } catch (err) {
          toast(err.message || "Obnovení se nepodařilo.", true);
        }
      });
      main.querySelector("#em-test").addEventListener("click", async () => {
        const to = main.querySelector("#em-test-to").value.trim();
        if (!to) return toast("Zadejte e-mail pro test.", true);
        try {
          const p = collect();
          await post(`/templates/${encodeURIComponent(key)}/test`, { to, subject: p.subject, preheader: p.preheader, html: p.html });
          toast(`Test odeslán na ${to}.`);
        } catch (err) {
          toast(err.message || "Test se nepodařilo odeslat.", true);
        }
      });
    }

    /* --------------------------------------------------------------- log */
    async function pageLog() {
      const [tpl, st] = await Promise.all([get("/templates"), get("/status").catch(() => null)]);
      const q = new URLSearchParams(location.search);
      const state = {
        template: q.get("template") || "",
        status: q.get("status") || "",
        stream: q.get("stream") || "",
        campaign: q.get("campaign") || "",
        q: q.get("q") || "",
        page: 1,
      };
      const opt = (v, text, cur) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(text)}</option>`;
      main.innerHTML = `${head("Log odesílání", "Každý odeslaný, čekající i neodeslaný e-mail.", "log", st)}
        <div id="em-logroot"><article class="ad-card em-card-flush">
          <div class="em-filters">
            <select id="f-template">${opt("", "Všechny šablony", state.template)}${(tpl.templates || []).map((t) => opt(t.key, `${t.number} ${t.name}`, state.template)).join("")}</select>
            <select id="f-status">${opt("", "Všechny stavy", state.status)}${Object.entries(STATUS).map(([k, v]) => opt(k, v, state.status)).join("")}</select>
            <select id="f-stream">${opt("", "Všechny streamy", state.stream)}${Object.entries(STREAMS).map(([k, v]) => opt(k, v, state.stream)).join("")}</select>
            <input id="f-q" placeholder="Hledat e-mail nebo předmět" value="${esc(state.q)}" />
          </div>
          <div id="em-counts" class="em-counts"></div>
          <div class="ad-utbl-wrap"><div class="em-tbl" id="em-log"></div></div>
          <div id="em-pager"></div>
        </article>
        <div id="em-modal" class="em-modal" hidden></div></div>`;
      const logRoot = main.querySelector("#em-logroot");
      const load = async () => {
        const p = new URLSearchParams({ page: state.page, page_size: PAGE_SIZE });
        ["template", "status", "stream", "campaign", "q"].forEach((k) => state[k] && p.set(k, state[k]));
        const d = await get(`/log?${p}`);
        const c = d.counts || {};
        main.querySelector("#em-counts").innerHTML = ["queued", "sent", "failed", "skipped"]
          .map((k) => `<span>${pill(k, STATUS[k])} <b>${n(c[k])}</b></span>`)
          .join("");
        main.querySelector("#em-log").innerHTML =
          `<div class="em-tr em-th em-log-tr"><span>Kdy</span><span>Šablona</span><span>Příjemce</span><span>Předmět</span><span>Stav</span><span>Otevř./klik</span><span></span></div>` +
          ((d.items || [])
            .map(
              (r) => `<div class="em-tr em-log-tr" data-id="${r.id}">
                <span>${esc(fmtDT(r.sent_at || r.created_at))}</span>
                <span>${esc(r.template_name || r.template_key || "Kampaň")}</span>
                <span class="em-trunc">${esc(r.to_email)}</span>
                <span class="em-trunc">${esc(r.subject)}</span>
                <span>${statusPill(r.status, r.error)}</span>
                <span>${r.opened_at ? "✓" : "–"} / ${r.clicked_at ? "✓" : "–"}</span>
                <span>${r.status === "failed" || r.status === "skipped" ? `<button class="ad-btn ghost tiny" data-retry="${r.id}">Znovu</button>` : ""}</span>
              </div>`,
            )
            .join("") || `<div class="ad-utbl-empty">Žádné záznamy.</div>`);
        main.querySelector("#em-pager").innerHTML = pager(state.page, d.total || 0, PAGE_SIZE);
      };
      const reload = () => load().catch((e) => toast(e.message, true));
      ["f-template", "f-status", "f-stream"].forEach((id) =>
        main.querySelector("#" + id).addEventListener("change", (e) => {
          state[id.slice(2)] = e.target.value;
          state.page = 1;
          reload();
        }),
      );
      main.querySelector("#f-q").addEventListener(
        "input",
        debounce((e) => {
          state.q = e.target.value.trim();
          state.page = 1;
          reload();
        }, 300),
      );
      logRoot.addEventListener("click", async (e) => {
        const pg = e.target.closest("[data-pg]");
        if (pg) {
          state.page = Math.max(1, state.page + Number(pg.dataset.pg));
          return reload();
        }
        const retry = e.target.closest("[data-retry]");
        if (retry) {
          try {
            await post(`/log/${retry.dataset.retry}/retry`);
            toast("Zařazeno k opakovanému odeslání.");
            reload();
          } catch (err) {
            toast(err.message, true);
          }
          return;
        }
        if (e.target.closest("[data-close]") || e.target.id === "em-modal") {
          main.querySelector("#em-modal").hidden = true;
          return;
        }
        const row = e.target.closest(".em-log-tr[data-id]");
        if (row) {
          try {
            const r = await get(`/log/${row.dataset.id}`);
            const modal = main.querySelector("#em-modal");
            modal.innerHTML = `<div class="em-modal-card ad-card">
              <div class="em-prev-head"><b>${esc(r.subject)}</b><button class="ad-btn ghost tiny" data-close>Zavřít</button></div>
              <p class="em-sub">${esc(r.to_email)} · ${esc(STREAMS[r.stream] || r.stream)} · ${esc(STATUS[r.status] || r.status)} · pokusů ${n(r.attempts)}${r.error ? ` · ${esc(r.error)}` : ""}<br>
              Vytvořeno ${esc(fmtDT(r.created_at))} · odesláno ${esc(fmtDT(r.sent_at))} · otevřeno ${esc(fmtDT(r.opened_at))} (${n(r.open_count)}×) · kliknuto ${esc(fmtDT(r.clicked_at))} (${n(r.click_count)}×)</p>
              ${previewFrame("em-log-frame")}</div>`;
            modal.hidden = false;
            setFrame(modal.querySelector("#em-log-frame"), r.html || `<pre style="font-family:sans-serif;white-space:pre-wrap;padding:16px">${esc(r.text_body || "")}</pre>`);
          } catch (err) {
            toast(err.message, true);
          }
        }
      });
      await reload();
    }

    /* ------------------------------------------------------------ kampaně */
    async function pageCampaigns() {
      const [d, st] = await Promise.all([get("/campaigns"), get("/status").catch(() => null)]);
      const items = d.items || d.campaigns || (Array.isArray(d) ? d : []);
      main.innerHTML = `${head("Kampaně", "Hromadné e-maily podle segmentu: textové nebo HTML.", "kampane", st)}
        <div class="em-actions"><a class="ad-btn" href="/admin/emaily/kampane/nova">Nová kampaň</a></div>
        <article class="ad-card em-card-flush"><div class="ad-utbl-wrap"><div class="em-tbl">
          <div class="em-tr em-th em-camp-tr"><span>Kampaň</span><span>Stav</span><span>Plán</span><span>Příjemců</span><span>Odesláno</span><span>Otevřeno</span><span>Kliknuto</span></div>
          ${
            items
              .map((c) => {
                const s = c.stats || c;
                return `<a class="em-tr em-camp-tr" href="/admin/emaily/kampane/${c.id}">
                  <span class="em-name"><b>${esc(c.name || "Bez názvu")}</b><small>${esc(c.subject || "")}</small></span>
                  <span>${pill(c.status === "sent" ? "sent" : c.status === "canceled" ? "canceled" : c.status === "draft" ? "draft" : "queued", CSTATUS[c.status] || c.status)}</span>
                  <span>${esc(fmtDT(c.scheduled_at))}</span>
                  <span>${n(s.total)}</span><span>${n(s.sent)}${s.failed ? ` <em class="em-err">/ ${n(s.failed)}</em>` : ""}</span><span>${n(s.opened)}</span><span>${n(s.clicked)}</span>
                </a>`;
              })
              .join("") || `<div class="ad-utbl-empty">Zatím žádné kampaně.</div>`
          }
        </div></div></article>`;
    }

    async function pageCampaign(id) {
      const isNew = id === "nova";
      const c = isNew
        ? { name: "", mode: "text", subject: "", preheader: "", body: "", segment: {}, status: "draft", scheduled_at: "" }
        : await get(`/campaigns/${encodeURIComponent(id)}`);
      const seg = Object.assign({ plans: [], verified: "any", has_dog: "any", registered_from: "", registered_to: "", locality: "", active_days: "", include_unsubscribed: false }, c.segment || {});
      const locked = ["sending", "sent", "canceled"].includes(c.status);
      const st = c.stats || c;
      const dt = c.scheduled_at ? String(c.scheduled_at).slice(0, 16) : "";
      const sel = (name, cur, opts) =>
        `<select name="${name}">${opts.map(([v, t]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${t}</option>`).join("")}</select>`;
      main.innerHTML = `
        <header class="ad-pagehead">
          <a class="ad-back" href="/admin/emaily/kampane">← Všechny kampaně</a>
          <h1 class="ad-h">${isNew ? "Nová kampaň" : esc(c.name || "Kampaň")}</h1>
          <p class="ad-lead">${pill(c.status === "sent" ? "sent" : "draft", CSTATUS[c.status] || c.status)}
          ${!isNew ? ` Příjemců ${n(st.total)} · odesláno ${n(st.sent)} · chyb ${n(st.failed)} · otevřeno ${n(st.opened)} · kliknuto ${n(st.clicked)}` : ""}</p>
        </header>
        <div class="em-edit" id="em-edit">
          <article class="ad-card em-form">
            <fieldset ${locked ? "disabled" : ""} class="em-fieldset">
              <label class="cms-field"><span>Název (interní)</span><input name="name" value="${esc(c.name)}" /></label>
              <label class="cms-field"><span>Režim</span>${sel("mode", c.mode, [["text", "Text"], ["html", "HTML"]])}</label>
              <label class="cms-field"><span>Předmět</span><input name="subject" value="${esc(c.subject)}" /></label>
              <label class="cms-field"><span>Preheader</span><input name="preheader" value="${esc(c.preheader)}" /></label>
              <label class="cms-field"><span>Obsah <small>(proměnné např. {{osloveni}}, {{jmeno}})</small></span><textarea name="body" rows="14" class="em-code" spellcheck="false">${esc(c.body)}</textarea></label>
              <h2 class="ad-kicker">Segment příjemců</h2>
              <div class="em-seg">
                <div class="cms-field"><span>Tarif</span><div class="em-checks">${["free", "start", "pro"].map((p) => `<label><input type="checkbox" name="plan_${p}" ${(seg.plans || []).includes(p) ? "checked" : ""} /> ${p === "free" ? "Zdarma" : p === "start" ? "Start" : "PRO"}</label>`).join("")}<small>nic = všechny</small></div></div>
                <label class="cms-field"><span>Ověřený e-mail</span>${sel("verified", seg.verified, [["any", "Libovolný"], ["yes", "Ověřený"], ["no", "Neověřený"]])}</label>
                <label class="cms-field"><span>Má hlídacího psa</span>${sel("has_dog", seg.has_dog, [["any", "Libovolně"], ["yes", "Ano"], ["no", "Ne"]])}</label>
                <label class="cms-field"><span>Registrace od</span><input type="date" name="registered_from" value="${esc(seg.registered_from || "")}" /></label>
                <label class="cms-field"><span>Registrace do</span><input type="date" name="registered_to" value="${esc(seg.registered_to || "")}" /></label>
                <label class="cms-field"><span>Lokalita psa</span><input name="locality" value="${esc(seg.locality || "")}" /></label>
                <label class="cms-field"><span>Aktivní za posledních (dní)</span><input type="number" min="0" name="active_days" value="${esc(seg.active_days ?? "")}" /></label>
              </div>
              <div class="em-audience"><button type="button" class="ad-btn soft" id="em-aud">Spočítat příjemce</button><span id="em-aud-out" class="em-sub"></span></div>
              <label class="cms-field"><span>Naplánovat na (prázdné = odeslat ihned)</span><input type="datetime-local" name="scheduled_at" value="${esc(dt)}" /></label>
            </fieldset>
            <div class="em-actions">
              ${locked ? "" : `<button class="ad-btn ghost" id="em-save">Uložit koncept</button><button class="ad-btn" id="em-send">Odeslat / naplánovat</button>`}
              ${["scheduled", "sending"].includes(c.status) ? `<button class="ad-btn danger" id="em-cancel">Zrušit</button>` : ""}
              <span class="em-test"><input id="em-test-to" type="email" placeholder="test@adresa.cz" /><button class="ad-btn soft" id="em-test">Poslat test</button></span>
            </div>
          </article>
          <article class="ad-card em-preview">
            <div class="em-prev-head"><span class="ad-kicker">Náhled</span></div>
            ${previewFrame("em-frame")}
          </article>
        </div>`;
      const root = main.querySelector("#em-edit");
      const frame = main.querySelector("#em-frame");
      const collect = () => {
        const f = (k) => val(root, k);
        const activeRaw = f("active_days");
        return {
          id: isNew ? undefined : c.id,
          name: f("name"),
          mode: f("mode"),
          subject: f("subject"),
          preheader: f("preheader"),
          body: f("body"),
          scheduled_at: f("scheduled_at") ? new Date(f("scheduled_at")).toISOString() : null,
          segment: {
            plans: ["free", "start", "pro"].filter((p) => root.querySelector(`[name="plan_${p}"]`).checked),
            verified: f("verified"),
            has_dog: f("has_dog"),
            registered_from: f("registered_from"),
            registered_to: f("registered_to"),
            locality: f("locality").trim(),
            active_days: activeRaw === "" ? null : Number(activeRaw),
            include_unsubscribed: false,
          },
        };
      };
      const paint = () => {
        const p = collect();
        if (p.mode === "html") setFrame(frame, p.body);
        else
          setFrame(
            frame,
            `<div style="font-family:Inter,Arial,sans-serif;font-size:15px;line-height:1.6;color:#163300;padding:24px;white-space:pre-wrap">${esc(p.body)}</div>`,
          );
      };
      root.addEventListener("input", debounce(paint, 250));
      paint();
      const save = async () => {
        const r = await post("/campaigns", collect());
        return r.id ?? (r.campaign || {}).id ?? c.id;
      };
      main.querySelector("#em-aud").addEventListener("click", async () => {
        const out = main.querySelector("#em-aud-out");
        out.textContent = "Počítám…";
        try {
          const r = await post("/campaigns/audience", { segment: collect().segment });
          out.textContent = `${n(r.count)} příjemců` + (r.sample?.length ? ` · např. ${r.sample.slice(0, 3).map((s) => s.email).join(", ")}` : "");
        } catch (err) {
          out.textContent = err.message;
        }
      });
      main.querySelector("#em-save")?.addEventListener("click", async () => {
        try {
          const newId = await save();
          toast("Koncept uložen.");
          if (isNew && newId) {
            history.replaceState({}, "", `/admin/emaily/kampane/${newId}`);
            await pageCampaign(String(newId));
          }
        } catch (err) {
          toast(err.message || "Uložení se nepodařilo.", true);
        }
      });
      main.querySelector("#em-send")?.addEventListener("click", async () => {
        try {
          const p = collect();
          const a = await post("/campaigns/audience", { segment: p.segment });
          const when = p.scheduled_at ? `naplánovat na ${fmtDT(p.scheduled_at)}` : "odeslat ihned";
          if (!confirm(`Opravdu ${when} ${n(a.count)} příjemcům?`)) return;
          const newId = await save();
          await post(`/campaigns/${newId}/send`);
          toast(p.scheduled_at ? "Kampaň naplánována." : "Kampaň se odesílá.");
          history.replaceState({}, "", `/admin/emaily/kampane/${newId}`);
          await pageCampaign(String(newId));
        } catch (err) {
          toast(err.message || "Odeslání se nepodařilo.", true);
        }
      });
      main.querySelector("#em-cancel")?.addEventListener("click", async () => {
        if (!confirm("Zrušit kampaň? Nezaslané e-maily se neodešlou.")) return;
        try {
          await post(`/campaigns/${c.id}/cancel`);
          toast("Kampaň zrušena.");
          await pageCampaign(String(c.id));
        } catch (err) {
          toast(err.message, true);
        }
      });
      main.querySelector("#em-test").addEventListener("click", async () => {
        const to = main.querySelector("#em-test-to").value.trim();
        if (!to) return toast("Zadejte e-mail pro test.", true);
        try {
          const newId = c.id || (await save());
          if (c.id) await save().catch(() => {});
          await post(`/campaigns/${newId}/test`, { to });
          toast(`Test odeslán na ${to}.`);
        } catch (err) {
          toast(err.message || "Test se nepodařilo odeslat.", true);
        }
      });
    }

    async function pageEmails() {
      const sub = subPath();
      if (!sub) return pageList();
      if (sub === "log") return pageLog();
      if (sub === "kampane") return pageCampaigns();
      if (sub.startsWith("kampane/")) return pageCampaign(sub.slice(8));
      if (sub.startsWith("sablony/")) return pageTemplate(decodeURIComponent(sub.slice(8)));
      return pageList();
    }

    return { emaily: pageEmails };
  };
})();
