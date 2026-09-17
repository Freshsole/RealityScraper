(() => {
  const $ = (id) => document.getElementById(id);
  const main = $("ad-main");
  let me = null;
  let liveTimer = null;

  const stopLive = () => {
    if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
    }
  };

  const ic = (name, w = 18, h = 18) => {
    const box = w <= 12 ? "ad-ico-12" : w <= 16 && name === "tag" ? "ad-ico-tag" : "ad-ico-18";
    return `<span class="ad-ico ${box}"><img src="/static/admin/assets/${name}.svg" width="${w}" height="${h}" alt="" /></span>`;
  };

  const fmtN = (value) =>
    String(value ?? "")
      .replace(/\s/g, "")
      .replace(/\B(?=(\d{3})+(?!\d))/g, " ");

  const demo = (src, note) =>
    src === "demo" ? `<span class="ad-demo" title="${esc(note || "ukázková data")}">DEMO</span>` : "";

  const esc = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const ico = (name, w, h = w) =>
    `<span class="ad-ico" style="width:${w}px;height:${w}px"><img src="/static/admin/assets/${name}.svg" width="${w}" height="${h}" alt="" /></span>`;

  const auditHtml = (row) => {
    const actor = esc(row.actor || "Admin");
    const text = esc(row.text || "");
    const withMail = text.replace(
      /([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})/g,
      `<span class="ad-audit-mail">$1</span>`,
    );
    return `<b>${actor}</b> ${withMail}`;
  };

  const metric = (item) => {
    const val = item.value ?? item;
    const source = item.source || "live";
    const shown = typeof val === "number" && Number.isInteger(val) ? fmtN(val) : esc(val);
    return `${shown}${demo(source, item.note)}`;
  };

  const spark = (points, color = "#163300", axisMax) => {
    const rows = points || [];
    if (!rows.length) return "";
    const max = axisMax || Math.max(...rows, 1);
    const w = 680;
    const h = 200;
    const step = w / Math.max(1, rows.length - 1);
    const y = (n) => h - (n / max) * (h - 4);
    const d = rows.map((n, i) => `${i === 0 ? "M" : "L"}${i * step},${y(n)}`).join(" ");
    const fill = `${d} L${(rows.length - 1) * step},${h} L0,${h} Z`;
    const grid = [0, 50, 100, 150, 200]
      .map((yy) => `<line x1="0" y1="${yy}" x2="${w}" y2="${yy}" stroke="#e8ebe6" stroke-width="1"/>`)
      .join("");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${grid}<path d="${fill}" fill="#9fe870" fill-opacity="0.15"/><path d="${d}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  };

  const niceMax = (n) => {
    const v = Math.max(1, Number(n) || 0);
    const mag = 10 ** Math.floor(Math.log10(v));
    const norm = v / mag;
    const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
    return nice * mag;
  };

  const dayAgoLabel = (ago) => {
    if (ago <= 0) return "Dnes";
    if (ago === 1) return "Včera";
    return `Před ${ago} dny`;
  };

  const trafficChart = (points) => {
    const rows = points || [];
    const top = niceMax(Math.max(...rows, 1));
    const ticks = [top, Math.round(top * 0.75), Math.round(top * 0.5), Math.round(top * 0.25), 0];
    return `<div class="ad-chart-body">
      <div class="ad-yaxis">${ticks.map((n) => `<span>${esc(fmtN(n))}</span>`).join("")}</div>
      <div class="ad-chart-plot" data-max="${top}">
        ${spark(rows, "#163300", top)}
        <div class="ad-chart-guide" hidden></div>
        <div class="ad-chart-dot" hidden></div>
        <div class="ad-chart-tip" hidden></div>
      </div>
    </div>
    <div class="ad-axis"><span>Před 30 dny</span><span>Před 15 dny</span><span>Dnes</span></div>`;
  };

  const lineChart = (points, labels) => {
    const rows = points || [];
    const top = niceMax(Math.max(...rows, 1));
    const ticks = [top, Math.round(top * 0.75), Math.round(top * 0.5), Math.round(top * 0.25), 0];
    return `<div class="ad-chart-body">
      <div class="ad-yaxis">${ticks.map((n) => `<span>${esc(fmtN(n))}</span>`).join("")}</div>
      <div class="ad-chart-plot" data-max="${top}">
        ${spark(rows, "#163300", top)}
        <div class="ad-chart-guide" hidden></div>
        <div class="ad-chart-dot" hidden></div>
        <div class="ad-chart-tip" hidden></div>
      </div>
    </div>
    <div class="ad-axis">${(labels || []).map((label) => `<span>${esc(label)}</span>`).join("")}</div>`;
  };

  const bindTrafficHover = (root, points, unit, labelFor) => {
    const plot = root.querySelector(".ad-chart-plot");
    const tip = root.querySelector(".ad-chart-tip");
    const guide = root.querySelector(".ad-chart-guide");
    const dot = root.querySelector(".ad-chart-dot");
    if (!plot || !tip) return;
    const rows = points || [];
    const top = Number(plot.dataset.max) || Math.max(...rows, 1);
    const hide = () => {
      tip.hidden = true;
      if (guide) guide.hidden = true;
      if (dot) dot.hidden = true;
    };
    const show = (event) => {
      if (!rows.length) return;
      const rect = plot.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const idx = Math.round((x / Math.max(rect.width, 1)) * Math.max(rows.length - 1, 0));
      const i = Math.max(0, Math.min(rows.length - 1, idx));
      const ago = rows.length - 1 - i;
      const leftPct = (i / Math.max(rows.length - 1, 1)) * 100;
      const yPct = 100 - (rows[i] / top) * ((rect.height - 4) / Math.max(rect.height, 1)) * 100;
      tip.hidden = false;
      const when = labelFor ? labelFor(i, rows) : dayAgoLabel(ago);
      tip.innerHTML = `<strong>${esc(fmtN(rows[i]))} ${esc(unit)}</strong><span>${esc(when)}</span>`;
      const tipW = tip.offsetWidth || 120;
      const px = (leftPct / 100) * rect.width;
      const clamped = Math.max(tipW / 2 + 4, Math.min(rect.width - tipW / 2 - 4, px));
      tip.style.left = `${clamped}px`;
      if (guide) {
        guide.hidden = false;
        guide.style.left = `${leftPct}%`;
      }
      if (dot) {
        dot.hidden = false;
        dot.style.left = `${leftPct}%`;
        dot.style.top = `${yPct}%`;
      }
    };
    plot.addEventListener("mousemove", show);
    plot.addEventListener("mouseleave", hide);
  };

  const barList = (rows, key = "name", val = "share") =>
    (rows || [])
      .map(
        (row) => `<div class="ad-flow-item">
          <div class="ad-flow-row"><span>${esc(row[key])}</span><span>${esc(row[val])}%</span></div>
          <div class="ad-bar thin"><i style="width:${Number(row[val]) || 0}%"></i></div></div>`,
      )
      .join("");

  async function api(path, opts) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    let response;
    try {
      response = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        ...opts,
        signal: opts?.signal || controller.signal,
      });
    } catch (err) {
      if (err?.name === "AbortError") {
        throw new Error("Server je vytížený. Zkus stránku obnovit za chvíli.");
      }
      throw err;
    } finally {
      clearTimeout(timeout);
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((item) => item.msg || item).join(" ")
            : data.message || "Chyba";
      const err = new Error(message);
      err.status = response.status;
      throw err;
    }
    return data;
  }

  function showLogin() {
    $("ad-app").classList.add("ad-hidden");
    $("ad-login").classList.remove("ad-hidden");
  }

  function showApp() {
    $("ad-login").classList.add("ad-hidden");
    $("ad-app").classList.remove("ad-hidden");
    $("ad-avatar").textContent = me.initials || "AD";
    $("ad-side-av").textContent = me.initials || "AD";
    $("ad-side-name").textContent = me.name || "Admin";
    const navName = $("ad-nav-name");
    if (navName) navName.textContent = me.name || "Admin";
  }

  function navOn(route) {
    document.querySelectorAll("#ad-links a").forEach((a) => {
      const cms = String(route || "").startsWith("cms");
      a.classList.toggle(
        "is-on",
        a.dataset.route === route ||
          (route === "uzivatel" && a.dataset.route === "uzivatele") ||
          (cms && a.dataset.route === "cms"),
      );
    });
  }

  const deviceLabel = (value) => {
    const key = String(value || "").toLowerCase();
    if (key === "desktop") return "Desktop";
    if (key === "mobil") return "Mobil";
    if (key === "tablet") return "Tablet";
    return value || "—";
  };

  const liveBlock = (live) => {
    const data = live || {};
    const rows = data.visitors || [];
    const n = Number(data.n) || 0;
    const signed = Number(data.signed_in) || 0;
    return `<div class="ad-chart-head">
        <h2 class="ad-sec">Live návštěvníci</h2>
        <span class="ad-live-meta"><span class="ad-live-dot"></span>${esc(fmtN(n))} online${signed ? ` · ${esc(fmtN(signed))} ${signed === 1 ? "účet" : "účty"}` : ""}</span>
      </div>
      <table class="ad-table ad-live-table">
        <thead><tr><th>Účet</th><th>Stránka</th><th>Zařízení</th><th>Místo</th><th>IP</th><th>Aktivita</th></tr></thead>
        <tbody>${
          rows
            .map((row) => {
              const account = row.signed_in
                ? `<a class="ad-live-acc" href="/admin/uzivatele/local"><strong>${esc(row.name || "Účet")}</strong><span>${esc(row.email)}</span></a>`
                : `<span class="ad-live-anon">Anonymní</span>`;
              return `<tr>
                <td>${account}</td>
                <td>${esc(row.page)}</td>
                <td>${esc(deviceLabel(row.device))}</td>
                <td>${esc(row.place)}</td>
                <td>${esc(row.ip || "—")}</td>
                <td>${esc(row.ago)}</td>
              </tr>`;
            })
            .join("") || `<tr><td colspan="6">Teď nikdo není na webu. Otevři úvodní stránku nebo katalog v jiném okně.</td></tr>`
        }</tbody>
      </table>`;
  };

  function kpis(items) {
    const cards = (items || []).map((item) => {
      const tone =
        item.delta_tone === "na" ? " na" : item.delta_tone === "down" ? " down" : item.delta_tone === "bad" ? " bad" : "";
      return `<article class="ad-card ad-kpi"><div class="lbl">${esc(item.label)}</div>
        <div class="val"><strong>${metric(item)}</strong>${item.delta ? `<span class="ad-delta${tone}">${esc(item.delta)}</span>` : ""}</div></article>`;
    });
    const rows = [];
    for (let i = 0; i < cards.length; i += 3) {
      rows.push(`<div class="ad-grid ad-grid-3">${cards.slice(i, i + 3).join("")}</div>`);
    }
    return `<div class="ad-kpis">${rows.join("")}</div>`;
  }

  async function pageOverview() {
    const data = await api("/api/admin/overview");
    const d = data.devices || {};
    const desk = Number(d.desktop) || 0;
    const mob = Number(d.mobil) || 0;
    const tab = Number(d.tablet) || 0;
    const donut = `conic-gradient(#163300 0 ${desk}%, #9fe870 ${desk}% ${desk + mob}%, #e2f6d5 ${desk + mob}% 100%)`;
    const countryRows = (data.countries.rows || []).map((row) => (Array.isArray(row) ? { name: row[0], share: row[1] } : row));
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Přehled provozu</h1>
        <p class="ad-lead">Analytický přehled aktivity platformy za posledních 30 dní.</p>
      </header>
      ${kpis(data.kpis)}
      <article class="ad-card ad-live" id="live-pane">${liveBlock(data.live)}</article>
      <div class="ad-row-2">
        <article class="ad-card ad-traffic">
          <div class="ad-chart-head">
            <h2 class="ad-sec">Návštěvnost / signupy v čase</h2>
            <button class="ad-dd" type="button" id="traffic-mode">Návštěvy stránek ${ic("chevron", 12, 12)}</button>
          </div>
          <div id="traffic-pane">${trafficChart(data.traffic.points)}</div>
        </article>
        <article class="ad-card ad-funnel">
          <h2 class="ad-sec">User flow (30d)</h2>
          <div class="ad-flow">${(data.flow || [])
            .map(
              (row) => `<div class="ad-flow-item">
                <div class="ad-flow-row"><span>${esc(row.label)}</span><span>${esc(row.value)}%</span></div>
                <div class="ad-bar"><i style="width:${Number(row.value) || 0}%"></i></div>
              </div>`,
            )
            .join("")}</div>
        </article>
      </div>
      <div class="ad-row-3">
        <article class="ad-card ad-country-card">
          <h2 class="ad-sec sm">Top země</h2>
          <div class="ad-country">${countryRows.length ? barList(countryRows) : `<p class="ad-k">Zatím žádné návštěvy.</p>`}</div>
        </article>
        <article class="ad-card ad-devices">
          <h2 class="ad-sec sm">Zařízení</h2>
          <div class="ad-donut-wrap">
            <div class="ad-donut" style="background:${donut}"></div>
            <div class="ad-donut-label"><strong>100%</strong><span>Podíl</span></div>
          </div>
          <div class="ad-legend">
            <div><span class="ad-leg-l"><span class="ad-dot" style="background:#163300"></span>Desktop</span><span class="ad-leg-n">${desk}%</span></div>
            <div><span class="ad-leg-l"><span class="ad-dot" style="background:#9fe870"></span>Mobil</span><span class="ad-leg-n">${mob}%</span></div>
            <div><span class="ad-leg-l"><span class="ad-dot" style="background:#e2f6d5"></span>Tablet</span><span class="ad-leg-n">${tab}%</span></div>
          </div>
        </article>
        <article class="ad-card ad-cities-card">
          <h2 class="ad-sec sm">Top města</h2>
          <table class="ad-table">
            <thead><tr><th>Město</th><th>Země</th><th>Návštěvníci</th></tr></thead>
            <tbody>${(data.cities.rows || [])
              .map((row) => `<tr><td>${esc(row.name)}</td><td>${esc(row.country)}</td><td>${esc(fmtN(row.n))}</td></tr>`)
              .join("") || `<tr><td colspan="3">Zatím žádné návštěvy s městem.</td></tr>`}</tbody>
          </table>
        </article>
      </div>`;
    const pane = document.getElementById("traffic-pane");
    const paint = (points, unit) => {
      pane.innerHTML = trafficChart(points);
      bindTrafficHover(pane, points, unit);
    };
    paint(data.traffic.points, "návštěv");
    const btn = document.getElementById("traffic-mode");
    let mode = "views";
    btn?.addEventListener("click", () => {
      mode = mode === "views" ? "signups" : "views";
      btn.innerHTML = `${mode === "views" ? "Návštěvy stránek" : "Registrace"} ${ic("chevron", 12, 12)}`;
      paint(mode === "views" ? data.traffic.points : data.traffic.signups, mode === "views" ? "návštěv" : "registrací");
    });
    const livePane = document.getElementById("live-pane");
    stopLive();
    liveTimer = setInterval(async () => {
      try {
        const fresh = await api("/api/admin/overview");
        if (livePane) livePane.innerHTML = liveBlock(fresh.live);
      } catch {
        /* keep last snapshot */
      }
    }, 10000);
  }

  async function pageUsers() {
    const data = await api("/api/admin/users");
    const rows = data.users || [];
    const total = Number(data.total) || rows.length;
    const mark = (on) =>
      on
        ? `<span class="ad-mark on">✓</span>`
        : `<span class="ad-mark off">✗</span>`;
    const withinDays = (iso, days) => {
      if (!iso) return false;
      const t = Date.parse(iso);
      if (Number.isNaN(t)) return false;
      return Date.now() - t <= days * 86400000;
    };
    const sameYear = (iso) => {
      const t = Date.parse(iso);
      if (Number.isNaN(t)) return false;
      return new Date(t).getFullYear() === new Date().getFullYear();
    };
    const userRow = (row) => `<div class="ad-utbl-row" data-email="${esc(row.email)}" data-name="${esc(row.name)}" data-status="${row.blocked ? "blocked" : "active"}" data-plan="${esc(row.plan || "free")}" data-discord="${row.discord ? "1" : "0"}" data-wa="${row.whatsapp ? "1" : "0"}" data-registered="${esc(row.registered_at || "")}" data-activity="${esc(row.activity_at || "")}">
        <label class="ad-check"><input type="checkbox" /><span></span></label>
        <span class="ad-utbl-mail">${esc(row.email)}</span>
        <span>${esc(row.name)}</span>
        <span class="muted">${esc(row.registered)}</span>
        <span><span class="ad-status${row.blocked ? " bad" : ""}">${esc(row.status)}</span></span>
        <span>${esc(row.plan_label || "Zdarma")}</span>
        <span class="num">${esc(row.monitors)}</span>
        <span>${mark(row.discord)}</span>
        <span>${mark(row.whatsapp)}</span>
        <span class="muted">${esc(row.activity)}</span>
        <span class="num ad-utbl-notif">${esc(row.notif || row.hits || "0 / 0")}</span>
        <a class="ad-detail" href="/admin/uzivatele/${esc(row.id || "local")}">Detail →</a>
      </div>`;
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Správa uživatelů</h1>
        <p class="ad-lead">Přehled a správa všech registrovaných uživatelů platformy.</p>
      </header>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Vyhledávání a filtry</h2>
        <div class="ad-ufilters">
          <div class="ad-ufield grow">
            <label>Vyhledat uživatele</label>
            <div class="ad-uinput">
              <span class="ad-ico ad-ico-search"><img src="/static/admin/assets/search.svg" width="14" height="14" alt="" /></span>
              <input id="u-q" type="search" placeholder="Hledat podle e-mailu..." />
            </div>
          </div>
          <div class="ad-ufield">
            <label>Stav účtu</label>
            <div class="ad-uselect">
              <select id="u-status">
                <option value="">Všechny</option>
                <option value="active">Aktivní</option>
                <option value="blocked">Zablokovaný</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Kanály</label>
            <div class="ad-uselect">
              <select id="u-chan">
                <option value="">Všechny</option>
                <option value="discord">Discord</option>
                <option value="whatsapp">WhatsApp</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Předplatné</label>
            <div class="ad-uselect">
              <select id="u-plan">
                <option value="">Všechny</option>
                <option value="free">Zdarma</option>
                <option value="start">Start</option>
                <option value="pro">PRO</option>
                <option value="individual">INDIVIDUAL</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Registrace</label>
            <div class="ad-uselect">
              <select id="u-reg">
                <option value="">Kdykoli</option>
                <option value="7">Posledních 7 dní</option>
                <option value="30">Posledních 30 dní</option>
                <option value="90">Posledních 90 dní</option>
                <option value="year">Letos</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Aktivita</label>
            <div class="ad-uselect">
              <select id="u-act">
                <option value="">Všichni</option>
                <option value="1">Dnes</option>
                <option value="7">Posledních 7 dní</option>
                <option value="30">Posledních 30 dní</option>
                <option value="idle">Neaktivní 30+ dní</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
        </div>
        <div class="ad-ufilter-actions">
          <button class="ad-btn" type="button" id="u-filter">Filtrovat</button>
          <button class="ad-reset" type="button" id="u-reset">Resetovat</button>
        </div>
        <div class="ad-umeta">
          <p>Zobrazeno <strong id="u-shown">${rows.length}</strong> z ${esc(fmtN(total))} uživatelů</p>
          <div class="ad-umeta-btns">
            <button class="ad-btn outline" type="button">Hromadné akce ›</button>
            <button class="ad-btn soft" type="button" id="u-csv">Exportovat CSV</button>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Tabulka uživatelů</h2>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl">
            <div class="ad-utbl-head">
              <label class="ad-check"><input type="checkbox" id="u-all" /><span></span></label>
              <span>E-mail</span>
              <span>Jméno</span>
              <span>Registrace</span>
              <span>Stav</span>
              <span>Předplatné</span>
              <span class="num">Monit.</span>
              <span>Disc.</span>
              <span>WhA.</span>
              <span>Aktivita</span>
              <span class="num ad-utbl-notif">Notifikace</span>
              <span class="num">Detail</span>
            </div>
            <div id="u-rows">${rows.map(userRow).join("") || ""}</div>
            <div class="ad-utbl-empty" id="u-empty" ${rows.length ? "hidden" : ""}>${rows.length ? "Žádný uživatel neodpovídá filtru." : "Žádný registrovaný účet."}</div>
          </div>
        </div>
        <div class="ad-upager">
          <p id="u-pager">Zobrazeno ${rows.length ? "1–" + rows.length : "0"} z ${esc(fmtN(total))} uživatelů</p>
          <div class="ad-pages">
            <button type="button" class="is-on">1</button>
            <button type="button" class="next" disabled>→</button>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Audit log (poslední akce)</h2>
        <div class="ad-audit">${
          (data.audit || [])
            .map(
              (row, i) => `<div class="ad-audit-row">
                <i class="${i === 0 ? "hot" : ""}"></i>
                <strong>${esc(row.at)}</strong>
                <p>${auditHtml(row)}</p>
              </div>`,
            )
            .join("") || `<p class="ad-k">Zatím žádné zásahy.</p>`
        }</div>
      </article>`;

    const applyFilter = () => {
      const q = ($("u-q").value || "").trim().toLowerCase();
      const status = $("u-status").value;
      const chan = $("u-chan").value;
      const plan = $("u-plan").value;
      const reg = $("u-reg").value;
      const act = $("u-act").value;
      let shown = 0;
      document.querySelectorAll("#u-rows .ad-utbl-row").forEach((el) => {
        const email = (el.dataset.email || "").toLowerCase();
        const name = (el.dataset.name || "").toLowerCase();
        const hay = `${email} ${name} ${el.textContent || ""}`.toLowerCase();
        const okQ = !q || hay.includes(q);
        const okS = !status || el.dataset.status === status;
        const okC =
          !chan ||
          (chan === "discord" && el.dataset.discord === "1") ||
          (chan === "whatsapp" && el.dataset.wa === "1");
        const okP = !plan || el.dataset.plan === plan;
        const okR =
          !reg ||
          (reg === "year" ? sameYear(el.dataset.registered) : withinDays(el.dataset.registered, Number(reg)));
        let okA = true;
        if (act === "idle") okA = !withinDays(el.dataset.activity, 30);
        else if (act) okA = withinDays(el.dataset.activity, Number(act));
        const on = okQ && okS && okC && okP && okR && okA;
        el.hidden = !on;
        if (on) shown += 1;
      });
      const shownEl = $("u-shown");
      if (shownEl) shownEl.textContent = String(shown);
      const pager = $("u-pager");
      if (pager) pager.textContent = `Zobrazeno ${shown ? "1–" + shown : "0"} z ${fmtN(total)} uživatelů`;
      const empty = $("u-empty");
      if (empty) {
        empty.hidden = shown > 0;
        if (!rows.length) empty.textContent = "Žádný registrovaný účet.";
        else empty.textContent = "Žádný uživatel neodpovídá filtru.";
      }
    };
    $("u-filter")?.addEventListener("click", applyFilter);
    $("u-q")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        applyFilter();
      }
    });
    $("u-reset")?.addEventListener("click", () => {
      $("u-q").value = "";
      $("u-status").value = "";
      $("u-chan").value = "";
      $("u-plan").value = "";
      $("u-reg").value = "";
      $("u-act").value = "";
      applyFilter();
    });
    $("u-all")?.addEventListener("change", () => {
      const on = $("u-all").checked;
      document.querySelectorAll("#u-rows .ad-utbl-row input[type=checkbox]").forEach((box) => {
        box.checked = on;
      });
    });
    $("u-csv")?.addEventListener("click", () => {
      const header = "email,name,registered,status,plan,monitors,discord,whatsapp,activity,notifications\n";
      const body = rows
        .map(
          (row) =>
            [row.email, row.name, row.registered, row.status, row.plan_label, row.monitors, row.discord, row.whatsapp, row.activity, row.notif || row.hits]
              .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
              .join(","),
        )
        .join("\n");
      const blob = new Blob([header + body], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "uzivatele.csv";
      a.click();
    });
  }

  async function pageUser() {
    const id = location.pathname.replace(/^\/admin\/uzivatele\/?/, "") || "local";
    const data = await api(`/api/admin/users/${encodeURIComponent(id)}`);
    const u = data.user;
    const disc = data.discord || {};
    const wa = data.whatsapp || {};
    const bill = data.billing || {};
    const invoices = (bill.history || []).filter(
      (row) => row.pdf || row.url || row.number || ["Faktura", "Upgrade", "Platba"].includes(row.kind),
    );
    const roleLabel = u.role === "admin" ? "Admin" : "Běžný uživatel";
    const payLabel = bill.comp ? "Bez platby (grant)" : "Platí (Stripe)";
    const period = bill.period_end && bill.period_end !== "—" ? bill.period_end : "";
    const invRow = (row) => {
      const link = row.pdf || row.url;
      return `<div class="ad-mtbl-row">
                <span class="muted">${esc(row.at)}</span>
                <strong>${esc(row.number || row.note || "—")}</strong>
                <span>${esc(row.kind)}</span>
                <span>${esc(row.plan)}</span>
                <span class="price">${esc(row.amount)}</span>
                <span>${nstat(row.ok !== false, row.status || "—")}</span>
                ${
                  link
                    ? `<a class="ad-detail" href="${esc(link)}" target="_blank" rel="noreferrer">Faktura →</a>`
                    : `<span class="muted">—</span>`
                }
              </div>`;
    };
    const kv = (label, value) =>
      `<div class="ad-kv"><span>${label}</span><strong>${value}</strong></div>`;
    const statusPill = (label, bad, warn) =>
      `<span class="ad-status${bad ? " bad" : warn ? " warn" : ""}">${esc(label)}</span>`;
    const nstat = (ok, label) =>
      `<span class="ad-nstat${ok ? "" : " fail"}">${ico(ok ? "check" : "x", 14)} ${esc(label)}</span>`;
    main.innerHTML = `
      <header class="ad-pagehead ad-ud-head">
        <a class="ad-back" href="/admin/uzivatele">${ico("arrow-left", 14)} Zpět na seznam</a>
        <h1 class="ad-h">Detail uživatele</h1>
      </header>
      <div class="ad-ud-top">
        <article class="ad-card ad-ud-info">
          <h2 class="ad-sec">Základní údaje</h2>
          <div class="ad-kvlist">
            ${kv("Jméno", esc(u.name))}
            ${kv("E-mail", esc(u.email))}
            ${kv("ID účtu", esc(u.id))}
            ${kv("Registrace", esc(u.registered))}
            <div class="ad-kv"><span>Stav</span>${statusPill(u.status, u.blocked)}</div>
            ${kv("Role", esc(roleLabel))}
            ${kv("Předplatné", esc(u.plan_label || bill.label || "Zdarma"))}
            ${kv("Platba tarifu", esc(payLabel))}
            ${kv("Poslední aktivita", esc(u.activity))}
            ${kv("Limit monitorů", esc(u.watch_limit))}
          </div>
        </article>
        <article class="ad-card ad-ud-actions">
          <h2 class="ad-sec">Akce</h2>
          <div class="ad-act-stack">
            <button class="ad-act-btn warn" type="button" data-act="block">${u.blocked ? "Odblokovat účet" : "Zablokovat účet"}</button>
            <button class="ad-act-btn mute" type="button" data-act="unlink_discord">Odpojit Discord</button>
            <button class="ad-act-btn${wa.linked ? " mute" : " off"}" type="button" data-act="unlink_whatsapp"${wa.linked ? "" : " disabled"}>Odpojit WhatsApp</button>
            <button class="ad-act-btn" type="button" data-act="reset_password">Reset hesla</button>
            <button class="ad-act-btn" type="button" data-act="watch_limit">Upravit limit monitorů</button>
          </div>
          <button class="ad-gdpr" type="button" data-act="delete_user">${ico("alert-triangle", 16)} Smazat účet (GDPR)</button>
        </article>
      </div>
      <article class="ad-card ad-bill-card">
        <h2 class="ad-sec">Správa předplatného a role</h2>
        <div class="ad-bill-split">
          <div class="ad-kvlist">
            <div class="ad-kv"><span>Tarif</span>${statusPill(bill.label || u.plan_label || "Zdarma", false)}</div>
            <div class="ad-kv"><span>Platba</span><strong>${esc(payLabel)}</strong></div>
            ${kv("Období do", esc(period || "—"))}
            ${bill.pending_label ? kv("Naplánováno", esc(bill.pending_label)) : ""}
          </div>
          <div class="ad-kvlist">
            <div class="ad-kv"><span>Role</span>${statusPill(roleLabel, false)}</div>
            ${kv("Admin přístup", u.role === "admin" ? "Ano" : "Ne")}
          </div>
        </div>
        <div class="ad-ufilters">
          <div class="ad-ufield grow">
            <label>Nový tarif</label>
            <div class="ad-uselect">
              <select id="u-plan-set">
                <option value="free"${u.plan === "free" ? " selected" : ""}>Zdarma</option>
                <option value="start"${u.plan === "start" ? " selected" : ""}>Start (149 Kč)</option>
                <option value="pro"${u.plan === "pro" ? " selected" : ""}>PRO (349 Kč)</option>
                <option value="individual"${u.plan === "individual" ? " selected" : ""}>INDIVIDUAL</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield grow">
            <label>Způsob změny</label>
            <div class="ad-uselect">
              <select id="u-pay-set">
                <option value="charge"${bill.comp ? "" : " selected"}>Strhnout poměrnou část</option>
                <option value="grant"${bill.comp ? " selected" : ""}>Bez platby (grant)</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Role uživatele</label>
            <div class="ad-uselect">
              <select id="u-role-set">
                <option value="user"${u.role !== "admin" ? " selected" : ""}>Běžný uživatel</option>
                <option value="admin"${u.role === "admin" ? " selected" : ""}>Admin</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
        </div>
        <div class="ad-ufilter-actions">
          <button class="ad-btn" type="button" id="u-plan-save">Uložit tarif</button>
          <button class="ad-btn outline" type="button" id="u-role-save">Uložit roli</button>
        </div>
        <h2 class="ad-sec">Faktury</h2>
        <div class="ad-mtbl ad-bill-tbl">
          <div class="ad-mtbl-head">
            <span>Datum</span><span>Faktura</span><span>Typ</span><span>Tarif</span><span>Částka</span><span>Stav</span><span>PDF</span>
          </div>
          ${invoices.map(invRow).join("") || `<p class="ad-k">Zatím žádné faktury.</p>`}
        </div>
      </article>
      <article class="ad-card">
        <h2 class="ad-sec">Monitory uživatele</h2>
        <div class="ad-mtbl">
          <div class="ad-mtbl-head">
            <span>Název monitoru</span><span>Lokalita</span><span>Cena</span><span>Velikost</span><span>Stav</span><span>Vytvořen</span>
          </div>
          ${(data.monitors || [])
            .map(
              (row) => `<div class="ad-mtbl-row">
                <strong>${esc(row.name)}</strong>
                <span class="muted">${esc(row.locality)}</span>
                <span class="price">${esc(row.price)}</span>
                <span class="muted">${esc(row.disposition)}</span>
                <span>${statusPill(row.status, false, !row.enabled)}</span>
                <span class="muted">${esc(row.created)}</span>
              </div>`,
            )
            .join("") || `<p class="ad-k">Žádné monitory.</p>`}
        </div>
      </article>
      <div class="ad-ch-split">
        <article class="ad-card ad-ch-card">
          <h2 class="ad-sec ad-ch-title">${ico("discord", 24)} Discord</h2>
          <div class="ad-kvlist sm">
            <div class="ad-kv"><span>Stav</span>${statusPill(disc.linked ? "Propojeno" : "Nepropojeno", !disc.linked)}</div>
            ${kv("Kanál vytvořen", esc(disc.channel))}
            ${kv("Datum propojení", esc(disc.at))}
            ${kv("Server ID", esc(disc.server))}
          </div>
        </article>
        <article class="ad-card ad-ch-card">
          <h2 class="ad-sec ad-ch-title">${ico("phone", 24)} WhatsApp</h2>
          <div class="ad-kvlist sm">
            <div class="ad-kv"><span>Stav</span>${statusPill(wa.linked ? "Propojeno" : "Nepropojeno", !wa.linked)}</div>
            ${kv("Telefonní číslo", esc(wa.phone))}
            ${kv("Datum propojení", esc(wa.at))}
          </div>
        </article>
      </div>
      <article class="ad-card">
        <h2 class="ad-sec">Historie notifikací (poslední)</h2>
        <div class="ad-ntbl">
          <div class="ad-ntbl-head">
            <span>Datum</span><span>Kanál</span><span>Monitor</span><span>Stav doručení</span><span>Detail</span>
          </div>
          ${(data.notifications || [])
            .map(
              (row) => `<div class="ad-ntbl-row">
                <span class="muted">${esc(row.at)}</span>
                <strong>${esc(row.channel)}</strong>
                <span class="mon">${esc(row.monitor)}</span>
                ${nstat(row.ok !== false, row.status || "Úspěšné")}
                <span class="muted det">${esc(row.detail)}</span>
              </div>`,
            )
            .join("") || `<p class="ad-k">Žádné notifikace.</p>`}
        </div>
      </article>
      <div class="ad-ud-logs">
        <article class="ad-card">
          <h2 class="ad-sec">Timeline aktivity</h2>
          <div class="ad-tl" id="u-timeline">${
            [...(data.timeline || [])]
              .sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")))
              .map(
                (row, i, all) => `<div class="ad-tl-item">
                  <div class="ad-tl-rail"><i></i>${i < all.length - 1 ? "<b></b>" : ""}</div>
                  <div><strong>${esc(row.title)}</strong><span>${esc(row.at)}</span></div>
                </div>`,
              )
              .join("") || `<p class="ad-k">Žádná aktivita.</p>`
          }</div>
        </article>
        <article class="ad-card ad-ud-audit">
          <h2 class="ad-sec">Audit log administračních zásahů</h2>
          <div class="ad-ud-notes">${
            (data.audit || [])
              .map(
                (row) => `<div class="ad-ud-note">
                  <time>${esc(row.at)}</time>
                  <p>${auditHtml(row)}</p>
                </div>`,
              )
              .join("") || `<p class="ad-k">Žádné zásahy.</p>`
          }</div>
        </article>
      </div>`;
    const timelineEl = $("u-timeline");
    if (timelineEl) timelineEl.scrollTop = 0;
    main.querySelectorAll("[data-act]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.act;
        const body = { action };
        if (action === "watch_limit") {
          const limit = prompt("Limit monitorů", String(u.watch_limit));
          if (limit == null) return;
          body.limit = limit;
        }
        if (action === "block" && u.blocked) body.action = "unblock";
        try {
          await api("/api/admin/action", { method: "POST", body: JSON.stringify(body) });
          pageUser();
        } catch (err) {
          alert(err.message);
        }
      });
    });
    $("u-plan-save")?.addEventListener("click", async () => {
      const plan = $("u-plan-set")?.value;
      const pay = $("u-pay-set")?.value || "grant";
      try {
        const result = await api("/api/admin/action", {
          method: "POST",
          body: JSON.stringify({ action: "set_plan", plan, charge: pay === "charge" }),
        });
        if (result.message) alert(result.message);
        pageUser();
      } catch (err) {
        alert(err.message);
      }
    });
    $("u-role-save")?.addEventListener("click", async () => {
      const role = $("u-role-set")?.value || "user";
      try {
        await api("/api/admin/action", { method: "POST", body: JSON.stringify({ action: "set_role", role }) });
        pageUser();
      } catch (err) {
        alert(err.message);
      }
    });
  }

  async function pageMonitors() {
    const data = await api("/api/admin/monitors");
    const rows = data.monitors || [];
    const total = Number(data.total) || rows.length;
    const shareList = (items) =>
      (items || [])
        .map((row) => {
          const tone = row.tone || "green";
          return `<div class="ad-share-item">
            <div class="ad-share-row"><span>${esc(row.name || row.label)}</span><span>${esc(row.share)}%</span></div>
            <div class="ad-bar ad-bar-${tone}"><i style="width:${Number(row.share) || 0}%"></i></div>
          </div>`;
        })
        .join("");
    const locOpts = (data.locality_options || [])
      .map((name) => `<option value="${esc(name)}">${esc(name)}</option>`)
      .join("");
    const withinDays = (iso, days) => {
      if (!iso) return false;
      const t = Date.parse(iso);
      if (Number.isNaN(t)) return false;
      return Date.now() - t <= days * 86400000;
    };
    const sameYear = (iso) => {
      const t = Date.parse(iso);
      if (Number.isNaN(t)) return false;
      return new Date(t).getFullYear() === new Date().getFullYear();
    };
    const monitorRow = (row) => `<div class="ad-utbl-row" data-id="${esc(row.id)}" data-name="${esc(row.name)}" data-email="${esc(row.email)}" data-status="${row.enabled ? "active" : "paused"}" data-locality="${esc(row.locality)}" data-offer="${esc(row.offer)}" data-created="${esc(row.created_at || "")}">
        <span class="ad-utbl-mail">${esc(row.name)}</span>
        <span>${esc(row.email)}</span>
        <span>${esc(row.locality)}</span>
        <span>${esc(row.offer)}</span>
        <span>${esc(row.disposition)}</span>
        <span class="price">${esc(row.price)}</span>
        <span><span class="ad-status${row.enabled ? "" : " warn"}">${esc(row.status)}</span></span>
        <span class="muted">${esc(row.created)}</span>
        <span class="num">${esc(row.hits)}</span>
        <a class="ad-detail" href="/nabidka?monitor_id=${esc(row.id)}">Detail →</a>
      </div>`;
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Správa monitorů</h1>
        <p class="ad-lead">Přehled všech hlídacích profilů na platformě.</p>
      </header>
      ${kpis(data.kpis)}
      <div class="ad-mon-split">
        <article class="ad-card ad-users-card">
          <h2 class="ad-sec">Top lokality</h2>
          <div class="ad-share">${shareList(data.localities)}</div>
        </article>
        <article class="ad-card ad-users-card">
          <h2 class="ad-sec">Typ & dispozice</h2>
          <div class="ad-mon-type">
            <div class="ad-mon-type-block">
              <p class="ad-subk">Typ nabídky</p>
              <div class="ad-share">${shareList(data.offers)}</div>
            </div>
            <div class="ad-mon-type-block">
              <p class="ad-subk">Dispozice</p>
              <div class="ad-share">${shareList(data.dispositions)}</div>
            </div>
          </div>
        </article>
      </div>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Vyhledávání a filtry</h2>
        <div class="ad-ufilters">
          <div class="ad-ufield grow">
            <label>Vyhledat monitor</label>
            <div class="ad-uinput">
              <span class="ad-ico ad-ico-search"><img src="/static/admin/assets/search.svg" width="14" height="14" alt="" /></span>
              <input id="m-q" type="search" placeholder="Hledat podle názvu nebo uživatele..." />
            </div>
          </div>
          <div class="ad-ufield fixed">
            <label>Stav</label>
            <div class="ad-uselect">
              <select id="m-status">
                <option value="">Všechny</option>
                <option value="active">Aktivní</option>
                <option value="paused">Pozastavený</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield fixed">
            <label>Lokalita</label>
            <div class="ad-uselect">
              <select id="m-loc">
                <option value="">Všechny</option>
                ${locOpts}
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield fixed">
            <label>Typ</label>
            <div class="ad-uselect">
              <select id="m-offer">
                <option value="">Všechny</option>
                <option value="Pronájem">Pronájem</option>
                <option value="Prodej">Prodej</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
          <div class="ad-ufield fixed">
            <label>Vytvořen</label>
            <div class="ad-uselect">
              <select id="m-created">
                <option value="">Kdykoli</option>
                <option value="7">Posledních 7 dní</option>
                <option value="30">Posledních 30 dní</option>
                <option value="90">Posledních 90 dní</option>
                <option value="year">Letos</option>
              </select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
          </div>
        </div>
        <div class="ad-ufilter-actions">
          <button class="ad-btn" type="button" id="m-filter">Filtrovat</button>
          <button class="ad-reset" type="button" id="m-reset">Resetovat</button>
        </div>
        <div class="ad-umeta">
          <p>Zobrazeno <strong id="m-shown">${rows.length}</strong> z ${esc(fmtN(total))} monitorů</p>
          <div class="ad-umeta-btns">
            <button class="ad-btn soft" type="button" id="m-csv">Exportovat CSV</button>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Tabulka monitorů</h2>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl ad-mon-tbl">
            <div class="ad-utbl-head">
              <span>Název monitoru</span>
              <span>Uživatel</span>
              <span>Lokalita</span>
              <span>Typ</span>
              <span>Dispozice</span>
              <span>Cenový rozsah</span>
              <span>Stav</span>
              <span>Vytvořen</span>
              <span class="num">Notif.</span>
              <span class="num">Detail</span>
            </div>
            <div id="m-rows">${rows.map(monitorRow).join("") || ""}</div>
            <div class="ad-utbl-empty" id="m-empty" ${rows.length ? "hidden" : ""}>${rows.length ? "Žádný monitor neodpovídá filtru." : "Zatím žádné monitory."}</div>
          </div>
        </div>
        <div class="ad-upager">
          <p id="m-pager">Zobrazeno ${rows.length ? "1–" + rows.length : "0"} z ${esc(fmtN(total))} monitorů</p>
          <div class="ad-pages">
            <button type="button" class="is-on">1</button>
            <button type="button" class="next" disabled>→</button>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Cenový přehled hlídaných nabídek</h2>
        <div class="ad-mon-price">
          <div>
            <p class="ad-subk">Pronájem (měsíční částka)</p>
            <div class="ad-share">${shareList(data.rent)}</div>
          </div>
          <div>
            <p class="ad-subk">Prodej (celková částka)</p>
            <div class="ad-share">${shareList(data.sale)}</div>
          </div>
        </div>
      </article>`;

    const applyFilter = () => {
      const q = ($("m-q").value || "").trim().toLowerCase();
      const status = $("m-status").value;
      const loc = $("m-loc").value;
      const offer = $("m-offer").value;
      const created = $("m-created").value;
      let shown = 0;
      document.querySelectorAll("#m-rows .ad-utbl-row").forEach((el) => {
        const hay = `${el.dataset.name || ""} ${el.dataset.email || ""} ${el.textContent || ""}`.toLowerCase();
        const okQ = !q || hay.includes(q);
        const okS = !status || el.dataset.status === status;
        const okL = !loc || el.dataset.locality === loc;
        const okO = !offer || el.dataset.offer === offer;
        const okC =
          !created ||
          (created === "year" ? sameYear(el.dataset.created) : withinDays(el.dataset.created, Number(created)));
        const on = okQ && okS && okL && okO && okC;
        el.hidden = !on;
        if (on) shown += 1;
      });
      const shownEl = $("m-shown");
      if (shownEl) shownEl.textContent = String(shown);
      const pager = $("m-pager");
      if (pager) pager.textContent = `Zobrazeno ${shown ? "1–" + shown : "0"} z ${fmtN(total)} monitorů`;
      const empty = $("m-empty");
      if (empty) {
        empty.hidden = shown > 0;
        empty.textContent = rows.length ? "Žádný monitor neodpovídá filtru." : "Zatím žádné monitory.";
      }
    };
    $("m-filter")?.addEventListener("click", applyFilter);
    $("m-q")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        applyFilter();
      }
    });
    $("m-reset")?.addEventListener("click", () => {
      $("m-q").value = "";
      $("m-status").value = "";
      $("m-loc").value = "";
      $("m-offer").value = "";
      $("m-created").value = "";
      applyFilter();
    });
    $("m-csv")?.addEventListener("click", () => {
      const header = "name,email,locality,offer,disposition,price,status,created,notifications\n";
      const visible = new Set(
        [...document.querySelectorAll("#m-rows .ad-utbl-row")].filter((el) => !el.hidden).map((el) => el.dataset.id),
      );
      const body = rows
        .filter((row) => visible.has(String(row.id)))
        .map((row) =>
          [row.name, row.email, row.locality, row.offer, row.disposition, row.price, row.status, row.created, row.hits]
            .map((cell) => `"${String(cell ?? "").replace(/"/g, '""')}"`)
            .join(","),
        )
        .join("\n");
      const blob = new Blob([header + body], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "monitory.csv";
      a.click();
      URL.revokeObjectURL(a.href);
    });
  }

  async function pageNotify() {
    const data = await api("/api/admin/notifications");
    const logs = data.logs || [];
    const broadcasts = data.broadcasts || [];
    const users = Number(data.users) || 0;
    const PAGE = 8;
    const withinDays = (iso, days) => {
      if (!iso) return false;
      const t = Date.parse(iso);
      if (Number.isNaN(t)) return false;
      return Date.now() - t <= days * 86400000;
    };
    const chanIco = (name) => {
      const key = String(name || "").toLowerCase();
      const file = key.includes("mail") || key.includes("e-mail") ? "mail" : key.includes("whats") ? "phone" : "discord";
      return `<span class="ad-ico ad-ico-14"><img src="/static/admin/assets/${file}.svg" width="14" height="14" alt="" /></span>`;
    };
    const logRow = (row) => `<div class="ad-utbl-row" data-user="${esc(row.user)}" data-monitor="${esc(row.monitor)}" data-channel="${esc(row.channel)}" data-status="${row.ok ? "ok" : "bad"}" data-kind="${esc(row.kind_key || "")}" data-created="${esc(row.at_iso || "")}">
        <span class="muted">${esc(row.at)}</span>
        <span class="ad-utbl-mail">${esc(row.user)}</span>
        <span>${esc(row.monitor)}</span>
        <span class="ad-nchan">${chanIco(row.channel)}${esc(row.channel)}</span>
        <span><span class="ad-kind${row.kind_key === "bulk" ? " bulk" : ""}">${esc(row.kind)}</span></span>
        <span class="ad-nstat ${row.ok ? "ok" : "bad"}">${row.ok ? `<span class="ad-ico ad-ico-14"><img src="/static/admin/assets/check.svg" width="14" height="14" alt="" /></span>` : `<span class="ad-ico ad-ico-14"><img src="/static/admin/assets/x-circle.svg" width="14" height="14" alt="" /></span>`}${esc(row.status)}</span>
        <span class="muted">${esc(row.error)}</span>
      </div>`;
    const histRow = (row) => `<div class="ad-utbl-row">
        <span class="muted">${esc(row.at)}</span>
        <span>${esc(row.actor)}</span>
        <span class="ad-utbl-mail">${esc(row.subject)}</span>
        <span class="muted">${esc(row.channel)}</span>
        <span class="num">${esc(fmtN(row.recipients ?? users))}</span>
        <span class="num ad-okn">${esc(fmtN(row.delivered))}</span>
        <span class="num ad-badn">${esc(fmtN(row.failed))}</span>
      </div>`;
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Správa notifikací</h1>
        <p class="ad-lead">Přehled odeslaných notifikací a hromadné zasílání zpráv.</p>
      </header>
      <section class="ad-nsec">
        <h2 class="ad-kicker">1. KPI statistiky</h2>
        ${kpis(data.kpis)}
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">2. Odeslat hromadnou notifikaci</h2>
        <article class="ad-card ad-users-card">
          <div class="ad-bc-row">
            <div class="ad-ufield">
              <label>Příjemci</label>
              <div class="ad-uselect">
                <select id="bc-aud">
                  <option value="all">Všichni uživatelé</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield">
              <label>Kanál</label>
              <div class="ad-uselect">
                <select id="bc-ch">
                  <option value="all">Všechny kanály</option>
                  <option value="email">E-mail</option>
                  <option value="discord">Discord</option>
                  <option value="whatsapp">WhatsApp</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
          </div>
          <div class="ad-ufield">
            <label>Předmět</label>
            <div class="ad-uinput"><input id="bc-sub" type="text" placeholder="Zadejte předmět e-mailu nebo nadpis zprávy..." /></div>
          </div>
          <div class="ad-ufield">
            <label>Zpráva</label>
            <textarea class="ad-narea" id="bc-body" placeholder="Napište text zprávy zde... (použijte markdown pro formátování, je-li podporováno kanálem)"></textarea>
          </div>
          <div class="ad-bc-foot">
            <div class="ad-bc-left">
              <button class="ad-toggle on" type="button" id="bc-now" aria-pressed="true"><i></i></button>
              <span class="ad-now-lbl">Odeslat ihned</span>
              <span class="ad-now-hint" id="bc-hint">Naplánovat odeslání na konkrétní čas</span>
              <input id="bc-when" class="ad-when" type="datetime-local" hidden />
            </div>
            <div class="ad-bc-actions">
              <button class="ad-btn outline" type="button" id="bc-preview">Náhled</button>
              <button class="ad-btn" type="button" id="bc-send">Odeslat notifikaci</button>
            </div>
          </div>
          <div class="ad-nwarn">
            <span class="ad-ico ad-ico-14"><img src="/static/admin/assets/alert-triangle.svg" width="14" height="14" alt="" /></span>
            <p>Tato zpráva bude odeslána <strong id="bc-count">${esc(fmtN(users))}</strong> uživatelům.</p>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">3. Historie hromadných zpráv</h2>
        <article class="ad-card ad-users-card">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-hist-tbl">
              <div class="ad-utbl-head">
                <span>Datum</span><span>Odesílatel</span><span>Předmět</span><span>Kanál</span>
                <span class="num">Příjemců</span><span class="num">Doručeno</span><span class="num">Selhalo</span>
              </div>
              <div>${broadcasts.map(histRow).join("") || ""}</div>
              <div class="ad-utbl-empty" ${broadcasts.length ? "hidden" : ""}>Zatím žádné hromadné zprávy.</div>
            </div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">4. Vyhledávání a filtry</h2>
        <article class="ad-card ad-users-card">
          <div class="ad-ufilters">
            <div class="ad-ufield grow">
              <label>Vyhledat v logu</label>
              <div class="ad-uinput">
                <span class="ad-ico ad-ico-search"><img src="/static/admin/assets/search.svg" width="14" height="14" alt="" /></span>
                <input id="n-q" type="search" placeholder="Hledat podle uživatele nebo monitoru..." />
              </div>
            </div>
            <div class="ad-ufield fixed">
              <label>Kanál</label>
              <div class="ad-uselect">
                <select id="n-ch">
                  <option value="">Všechny</option>
                  <option value="E-mail">E-mail</option>
                  <option value="Discord">Discord</option>
                  <option value="WhatsApp">WhatsApp</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield fixed">
              <label>Stav</label>
              <div class="ad-uselect">
                <select id="n-st">
                  <option value="">Všechny</option>
                  <option value="ok">Doručeno</option>
                  <option value="bad">Selhalo</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield fixed">
              <label>Období</label>
              <div class="ad-uselect">
                <select id="n-when">
                  <option value="">Všechny</option>
                  <option value="1">Dnes</option>
                  <option value="7">Posledních 7 dní</option>
                  <option value="30">Posledních 30 dní</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield fixed">
              <label>Typ</label>
              <div class="ad-uselect">
                <select id="n-kind">
                  <option value="">Všechny</option>
                  <option value="auto">Auto</option>
                  <option value="bulk">Hromadná</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
          </div>
          <div class="ad-ufilter-actions">
            <button class="ad-btn" type="button" id="n-filter">Filtrovat</button>
            <button class="ad-reset" type="button" id="n-reset">Resetovat</button>
            <p class="ad-nmeta">Zobrazeno <strong id="n-shown">${logs.length}</strong> z ${esc(fmtN(logs.length))}</p>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">5. Log notifikací</h2>
        <article class="ad-card ad-users-card">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-nlog-tbl">
              <div class="ad-utbl-head">
                <span>Datum/čas</span><span>Uživatel</span><span>Monitor</span><span>Kanál</span><span>Typ</span><span>Stav</span><span>Chyba</span>
              </div>
              <div id="n-rows">${logs.map(logRow).join("") || ""}</div>
              <div class="ad-utbl-empty" id="n-empty" ${logs.length ? "hidden" : ""}>Zatím žádné notifikace.</div>
            </div>
          </div>
          <div class="ad-upager">
            <p id="n-pager">Zobrazeno ${logs.length ? "1–" + Math.min(PAGE, logs.length) : "0"} z ${esc(fmtN(logs.length))} notifikací</p>
            <div class="ad-pages" id="n-pages"></div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">6. Přehled kanálů</h2>
        <div class="ad-nch-row">${(data.channels || [])
          .map(
            (ch) => `<article class="ad-card ad-nch">
              <div class="ad-nch-head">
                <h3 class="ad-sec">${esc(ch.name)}</h3>
                <span class="ad-ico ad-ico-18"><img src="/static/admin/assets/${esc(ch.icon || "mail")}.svg" width="18" height="18" alt="" /></span>
              </div>
              <div class="ad-nch-kv"><span>Odesláno 30d</span><strong>${esc(fmtN(ch.sent))}</strong></div>
              <div class="ad-nch-kv"><span>Selhalo</span><strong class="ad-badn">${esc(fmtN(ch.failed))}</strong></div>
              <div class="ad-nch-kv"><span>Úspěšnost</span><strong class="ad-okn">${esc(ch.ok)}%</strong></div>
              <div class="ad-bar ad-bar-ok"><i style="width:${Number(ch.ok) || 0}%"></i></div>
            </article>`,
          )
          .join("")}</div>
      </section>
      <div class="ad-preview" id="bc-modal" hidden>
        <div class="ad-preview-card">
          <h2 class="ad-sec">Náhled</h2>
          <p class="ad-k" id="bc-prev-meta"></p>
          <p class="ad-prev-sub" id="bc-prev-sub"></p>
          <p class="ad-prev-body" id="bc-prev-body"></p>
          <button class="ad-btn" type="button" id="bc-prev-close">Zavřít</button>
        </div>
      </div>`;

    const nowBtn = $("bc-now");
    const when = $("bc-when");
    nowBtn?.addEventListener("click", () => {
      const on = !nowBtn.classList.contains("on");
      nowBtn.classList.toggle("on", on);
      nowBtn.setAttribute("aria-pressed", on ? "true" : "false");
      if (when) when.hidden = on;
    });
    const payload = () => ({
      subject: $("bc-sub").value,
      body: $("bc-body").value,
      channel: $("bc-ch").value,
      audience: $("bc-aud").value,
      immediate: nowBtn?.classList.contains("on"),
      scheduled_at: $("bc-when")?.value || "",
    });
    $("bc-preview")?.addEventListener("click", () => {
      const item = payload();
      $("bc-prev-meta").textContent = `${$("bc-aud").selectedOptions[0]?.text || ""} · ${$("bc-ch").selectedOptions[0]?.text || ""} · ${item.immediate ? "ihned" : item.scheduled_at || "naplánováno"}`;
      $("bc-prev-sub").textContent = item.subject || "(bez předmětu)";
      $("bc-prev-body").textContent = item.body || "(prázdná zpráva)";
      $("bc-modal").hidden = false;
    });
    $("bc-prev-close")?.addEventListener("click", () => {
      $("bc-modal").hidden = true;
    });
    $("bc-send")?.addEventListener("click", async () => {
      const item = payload();
      if (!item.subject.trim() && !item.body.trim()) {
        alert("Doplň předmět nebo text zprávy.");
        return;
      }
      try {
        await api("/api/admin/broadcast", { method: "POST", body: JSON.stringify(item) });
        pageNotify();
      } catch (err) {
        alert(err.message);
      }
    });

    let page = 1;
    const applyFilter = () => {
      const q = ($("n-q").value || "").trim().toLowerCase();
      const ch = $("n-ch").value;
      const st = $("n-st").value;
      const whenF = $("n-when").value;
      const kind = $("n-kind").value;
      const rows = [...document.querySelectorAll("#n-rows .ad-utbl-row")];
      let match = 0;
      rows.forEach((el) => {
        const hay = `${el.dataset.user || ""} ${el.dataset.monitor || ""}`.toLowerCase();
        const okQ = !q || hay.includes(q);
        const okC = !ch || el.dataset.channel === ch;
        const okS = !st || el.dataset.status === st;
        const okK = !kind || el.dataset.kind === kind;
        const okW = !whenF || withinDays(el.dataset.created, Number(whenF));
        const on = okQ && okC && okS && okK && okW;
        el.dataset.match = on ? "1" : "0";
        if (on) match += 1;
      });
      const pages = Math.max(1, Math.ceil(match / PAGE));
      if (page > pages) page = 1;
      let shown = 0;
      let idx = 0;
      rows.forEach((el) => {
        const on = el.dataset.match === "1";
        if (!on) {
          el.hidden = true;
          return;
        }
        const start = (page - 1) * PAGE;
        const vis = idx >= start && idx < start + PAGE;
        el.hidden = !vis;
        if (vis) shown += 1;
        idx += 1;
      });
      const shownEl = $("n-shown");
      if (shownEl) shownEl.textContent = String(match);
      const pager = $("n-pager");
      const from = match ? (page - 1) * PAGE + 1 : 0;
      const to = match ? (page - 1) * PAGE + shown : 0;
      if (pager) pager.textContent = `Zobrazeno ${from ? from + "–" + to : "0"} z ${fmtN(logs.length)} notifikací`;
      const empty = $("n-empty");
      if (empty) {
        empty.hidden = match > 0;
        empty.textContent = logs.length ? "Žádná notifikace neodpovídá filtru." : "Zatím žádné notifikace.";
      }
      const host = $("n-pages");
      if (host) {
        const buttons = [];
        const maxBtn = Math.min(pages, 5);
        for (let i = 1; i <= maxBtn; i += 1) {
          buttons.push(`<button type="button" data-page="${i}" class="${i === page ? "is-on" : ""}">${i}</button>`);
        }
        if (pages > 5) buttons.push(`<span>…</span><button type="button" data-page="${pages}">${pages}</button>`);
        buttons.push(`<button type="button" class="next" data-page="${Math.min(pages, page + 1)}" ${page >= pages ? "disabled" : ""}>→</button>`);
        host.innerHTML = buttons.join("");
        host.querySelectorAll("button[data-page]").forEach((btn) => {
          btn.addEventListener("click", () => {
            page = Number(btn.dataset.page) || 1;
            applyFilter();
          });
        });
      }
    };
    $("n-filter")?.addEventListener("click", () => {
      page = 1;
      applyFilter();
    });
    $("n-q")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        page = 1;
        applyFilter();
      }
    });
    $("n-reset")?.addEventListener("click", () => {
      $("n-q").value = "";
      $("n-ch").value = "";
      $("n-st").value = "";
      $("n-when").value = "";
      $("n-kind").value = "";
      page = 1;
      applyFilter();
    });
    applyFilter();
  }

  async function pageOps() {
    const data = await api("/api/admin/ops");
    const sc = data.scraper || {};
    const logs = data.logs || [];
    const d = data.dedupe || {};
    const counts = d.counts || {};
    const scan = d.scan_stats || {};
    const last = d.last_stats || {};
    const hourOpts = Array.from({ length: 24 }, (_, hour) => {
      const selected = Number(d.hour) === hour ? " selected" : "";
      return `<option value="${hour}"${selected}>${String(hour).padStart(2, "0")}:00</option>`;
    }).join("");
    const apiPts = data.api_series?.points || [];
    const errPts = data.err_series?.points || [];
    const apiLabels = data.api_series?.labels || ["00:00", "08:00", "16:00", "24:00"];
    const errLabels = data.err_series?.labels || ["Před 30 dny", "Před 15 dny", "Dnes"];
    const PAGE = 8;
    const lvlClass = (level) => {
      const key = String(level || "").toLowerCase();
      if (key === "error") return "err";
      if (key === "warning") return "warn";
      return "info";
    };
    const svcCard = (s) => `<article class="ad-card ad-svc-card">
        <div class="ad-svc-top"><span>${esc(s.name)}</span><i class="ad-ok${s.ok ? "" : " off"}"></i></div>
        <div class="ad-svc-mid"><span class="ad-run${s.ok ? "" : " off"}">${s.ok ? "Běží" : "Down"}</span><span class="ad-svc-lat">${esc(s.latency)}</span></div>
        <p class="ad-svc-up">Uptime: <b class="${s.ok ? "" : "bad"}">${esc(s.uptime)}</b></p>
      </article>`;
    const srcRow = (s) => `<div class="ad-utbl-row">
        <span>${esc(s.name)}</span>
        <span><span class="ad-pill${s.ok ? "" : " warn"}">${esc(s.status)}</span></span>
        <span class="num">${esc(fmtN(s.n))}</span>
        <span class="muted">${esc(s.ago)}</span>
      </div>`;
    const logRow = (row) => `<div class="ad-utbl-row">
        <span class="muted">${esc(row.at)}</span>
        <span><span class="ad-lvl ${lvlClass(row.level)}">${esc(row.level)}</span></span>
        <span>${esc(row.service)}</span>
        <span>${esc(row.message)}</span>
      </div>`;
    const jobRow = (row) => `<div class="ad-utbl-row">
        <span>${esc(row.name)}</span>
        <span class="muted">${esc(row.interval)}</span>
        <span class="muted">${esc(row.last)}</span>
        <span class="muted">${esc(row.duration)}</span>
        <span class="ad-nstat ${row.ok ? "ok" : "bad"}">${esc(row.status)}</span>
        <span class="muted">${esc(row.next)}</span>
      </div>`;
    const tickRow = (row) => `<div class="ad-utbl-row">
        <span class="muted">${esc(row.at_rel || row.at || "—")}</span>
        <span>${esc(row.kind || "minute")}</span>
        <span class="num">${esc(fmtN(row.listings || 0))}</span>
        <span class="num">${esc(fmtN(row.new || 0))}</span>
        <span class="num">${esc(fmtN(row.updated || 0))}</span>
        <span class="muted">${esc(row.duration || "—")}</span>
        <span class="muted">${esc(row.detail || (row.url ? String(row.url).slice(0, 64) : "—"))}</span>
      </div>`;
    const scheduleRow = (row) => `<div class="ad-scrape-schedule-item" data-id="${esc(row.id || "")}">
        <div><strong>${esc(row.scope === "portal" ? "Portál" : "URL")}</strong><div class="muted">${esc(row.label || row.url || row.portal || "—")}</div></div>
        <div class="muted">${esc(row.run_at_fmt || row.run_at || "—")}</div>
        <div class="muted">${esc(row.run_at_rel || "")}</div>
        <button class="ad-btn outline" type="button" data-cancel-schedule="${esc(row.id || "")}">Zrušit</button>
      </div>`;
    const portalOpts = (data.scrape_portals || [
      { id: "sreality", name: "Sreality" },
      { id: "bezrealitky", name: "Bezrealitky" },
      { id: "idnes", name: "Reality.iDNES" },
      { id: "bazos", name: "Bazoš" },
    ])
      .map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`)
      .join("");
    const sampleRow = (row) => `<div class="ad-utbl-row">
        <span>${esc(row.name || row.canonical || "")}</span>
        <span class="muted">${esc(row.disposition || "—")}${row.area_m2 ? ` · ${esc(row.area_m2)} m²` : ""}</span>
        <span class="num">${esc(fmtN(row.n || 0))}</span>
        <span class="muted">${esc((row.urls || []).length)} portálů</span>
      </div>`;
    const services = [...new Set(logs.map((row) => row.service).filter(Boolean))];
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Provoz</h1>
        <p class="ad-lead">Systémový monitoring, zdraví služeb a provozní přehledy.</p>
      </header>
      <section class="ad-nsec">
        <h2 class="ad-kicker">1. Stav služeb</h2>
        <div class="ad-svc">${(data.services || []).map(svcCard).join("")}</div>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">2. Systémové metriky</h2>
        <div class="ad-ops-metrics">
          <article class="ad-card ad-ops-chart">
            <div class="ad-chart-head"><h3 class="ad-sec">Odezva API (24h)</h3></div>
            ${lineChart(apiPts, apiLabels)}
          </article>
          <article class="ad-card ad-ops-chart">
            <div class="ad-chart-head"><h3 class="ad-sec">Chybovost (30d)</h3><span class="ad-delta">Aktuálně ${esc(data.err_series?.now || "0%")}</span></div>
            ${lineChart(errPts, errLabels)}
          </article>
        </div>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">3. Scraper přehled</h2>
        <article class="ad-card ad-users-card ad-scrape-card">
          <div class="ad-scrape-kpis">
            <article class="ad-mini"><div class="lbl">Spuštění dnes</div><strong>${metric(sc.runs_today)}</strong></article>
            <article class="ad-mini"><div class="lbl">Nových nabídek dnes</div><strong>${metric(sc.new_today)}</strong></article>
            <article class="ad-mini"><div class="lbl">Aktualizovaných</div><strong>${metric(sc.updated)}</strong></article>
            <article class="ad-mini"><div class="lbl">Smazaných/expirovaných</div><strong>${metric(sc.expired)}</strong></article>
            <article class="ad-mini"><div class="lbl">Průměrná doba běhu</div><strong>${metric(sc.avg)}</strong></article>
            <article class="ad-mini"><div class="lbl">Poslední běh</div><strong>${metric(sc.last)}</strong></article>
          </div>
          <div class="ad-scrape-src">
            <h3 class="ad-sec sm">Zdroje dat</h3>
            <div class="ad-utbl-wrap">
              <div class="ad-utbl ad-src-tbl">
                <div class="ad-utbl-head"><span>Zdroj</span><span>Stav</span><span class="num">Nabídek</span><span>Poslední sync</span></div>
                <div>${(data.sources || []).map(srcRow).join("") || ""}</div>
                <div class="ad-utbl-empty" ${(data.sources || []).length ? "hidden" : ""}>Žádné zdroje v katalogu.</div>
              </div>
            </div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">4. Systémové logy</h2>
        <article class="ad-card ad-users-card ad-ops-filters">
          <div class="ad-ufilters">
            <div class="ad-ufield">
              <label>Úroveň</label>
              <div class="ad-uselect">
                <select id="o-lvl">
                  <option value="">Všechny</option>
                  <option value="Info">Info</option>
                  <option value="Warning">Warning</option>
                  <option value="Error">Error</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield">
              <label>Služba</label>
              <div class="ad-uselect">
                <select id="o-svc">
                  <option value="">Všechny</option>
                  ${services.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join("")}
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield">
              <label>Období</label>
              <div class="ad-uselect">
                <select id="o-when">
                  <option value="1">Dnes</option>
                  <option value="24" selected>Posl. 24h</option>
                  <option value="7">Posledních 7 dní</option>
                  <option value="30">Posledních 30 dní</option>
                </select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <div class="ad-ufield ad-ops-go">
              <label>&nbsp;</label>
              <button class="ad-btn" type="button" id="o-filter">Filtrovat</button>
            </div>
          </div>
        </article>
        <article class="ad-card ad-users-card">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-ops-log">
              <div class="ad-utbl-head"><span>Čas</span><span>Úroveň</span><span>Služba</span><span>Zpráva</span></div>
              <div id="o-rows"></div>
              <div class="ad-utbl-empty" id="o-empty">Zatím žádné logy.</div>
            </div>
          </div>
          <div class="ad-upager">
            <p id="o-pager"></p>
            <div class="ad-pages" id="o-pages"></div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">5. Deduplikace inzerátů</h2>
        <article class="ad-card ad-users-card ad-dedupe-card">
          <p class="ad-dedupe-lead">Sloučí stejné byty z různých portálů a smaže zdvojené řádky. Kontrola nic nemění, jen spočítá, kolik duplicit zbývá.</p>
          <div class="ad-scrape-kpis ad-dedupe-kpis">
            <article class="ad-mini"><div class="lbl">Zdvojené inzeráty</div><strong id="d-listing-dups">${esc(fmtN(counts.listing_dup_groups || 0))}</strong></article>
            <article class="ad-mini"><div class="lbl">Zdvojené v katalogu</div><strong id="d-catalog-dups">${esc(fmtN(counts.catalog_dup_groups || 0))}</strong></article>
            <article class="ad-mini"><div class="lbl">Ke sloučení (posl. kontrola)</div><strong id="d-relink">${esc(fmtN((scan.listings_relinkable || 0) + (scan.catalog_relinkable || 0)))}</strong></article>
            <article class="ad-mini"><div class="lbl">Stav</div><strong id="d-status">${esc(d.status_label || "Čeká")}</strong></article>
            <article class="ad-mini"><div class="lbl">Poslední sloučení</div><strong id="d-last">${esc(d.last_run_rel || "—")}</strong></article>
            <article class="ad-mini"><div class="lbl">Poslední kontrola</div><strong id="d-scan">${esc(d.last_scan_rel || "—")}</strong></article>
          </div>
          <p class="ad-err" id="d-err">${esc(d.error || "")}</p>
          <div class="ad-dedupe-actions">
            <button class="ad-btn" type="button" id="d-run" ${d.busy ? "disabled" : ""}>Sloučit teď</button>
            <button class="ad-btn outline" type="button" id="d-scan-btn" ${d.busy ? "disabled" : ""}>Zkontrolovat duplicity</button>
            <label class="ad-dedupe-check"><input type="checkbox" id="d-enabled" ${d.enabled ? "checked" : ""}/> Denní automatické sloučení</label>
            <div class="ad-ufield ad-dedupe-hour">
              <label>Hodina</label>
              <div class="ad-uselect">
                <select id="d-hour">${hourOpts}</select>
                <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
              </div>
            </div>
            <button class="ad-btn soft" type="button" id="d-save">Uložit plán</button>
          </div>
          <p class="ad-dedupe-note" id="d-note">Další běh: ${esc(d.next || "—")}${last.listings_relinked != null ? ` · Poslední běh přepojil ${fmtN(last.listings_relinked || 0)} inzerátů a ${fmtN(last.catalog_relinked || 0)} katalogových záznamů.` : ""}</p>
          <div class="ad-utbl-wrap" id="d-sample-wrap">
            <h3 class="ad-sec sm">Nález z poslední kontroly</h3>
            <div class="ad-utbl ad-dedupe-samples">
              <div class="ad-utbl-head"><span>Byt</span><span>Dispozice</span><span class="num">Záznamů</span><span>Odkazy</span></div>
              <div id="d-samples">${(d.samples || []).map(sampleRow).join("")}</div>
              <div class="ad-utbl-empty" id="d-empty" ${(d.samples || []).length ? "hidden" : ""}>Zatím žádná kontrola, nebo kontrola nenašla duplicity ke sloučení.</div>
            </div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">6. Úlohy a cron joby</h2>
        <article class="ad-card ad-users-card ad-dedupe-card">
          <div class="ad-ops-block">
            <p class="ad-dedupe-lead">${esc(data.scrape_note || "")}</p>
            <div class="ad-utbl-wrap">
              <div class="ad-utbl ad-ops-jobs">
                <div class="ad-utbl-head">
                  <span>Název úlohy</span><span>Interval</span><span>Poslední běh</span><span>Trvání</span><span>Stav</span><span>Další běh</span>
                </div>
                <div>${(data.jobs || []).map(jobRow).join("") || ""}</div>
                <div class="ad-utbl-empty" ${(data.jobs || []).length ? "hidden" : ""}>Žádné naplánované úlohy.</div>
              </div>
            </div>
          </div>
          <div class="ad-ops-block">
            <h3 class="ad-sec sm">Poslední minutové běhy</h3>
            <div class="ad-utbl-wrap">
              <div class="ad-utbl ad-ops-jobs ad-ops-jobs-ticks">
                <div class="ad-utbl-head">
                  <span>Kdy</span><span>Typ</span><span class="num">Listings</span><span class="num">Nové</span><span class="num">Změny</span><span>Trvání</span><span>Deep cover</span>
                </div>
                <div id="o-ticks">${(data.recent_ticks || []).map(tickRow).join("") || ""}</div>
                <div class="ad-utbl-empty" id="o-ticks-empty" ${(data.recent_ticks || []).length ? "hidden" : ""}>Zatím žádný minutový tick.</div>
              </div>
            </div>
          </div>
          <div class="ad-ops-block">
            <h3 class="ad-sec sm">Ruční scrape</h3>
            <p class="ad-dedupe-lead">Spusť scrape konkrétní URL hledání, nebo celý katalog vybraného portálu. Můžeš spustit hned, nebo naplánovat na konkrétní čas.</p>
            <div class="ad-scrape-form">
              <div class="ad-scrape-row">
                <div>
                  <div class="ad-choice" id="o-scope-choice" role="group" aria-label="Rozsah scrape">
                    <button type="button" data-scope="url" class="is-on">URL hledání</button>
                    <button type="button" data-scope="portal">Celý portál</button>
                  </div>
                </div>
                <div>
                  <div class="ad-choice" id="o-when-choice" role="group" aria-label="Kdy spustit">
                    <button type="button" data-when="now" class="is-on">Teď</button>
                    <button type="button" data-when="schedule">Naplánovat</button>
                  </div>
                </div>
              </div>
              <div class="ad-scrape-row" id="o-scrape-fields">
                <div class="ad-ufield url" id="o-url-wrap">
                  <label>URL hledání</label>
                  <div class="ad-uinput"><input id="o-scrape-url" type="url" placeholder="https://www.sreality.cz/hledani/pronajem/byty/ustecky-kraj?velikost=2%2Bkk" /></div>
                </div>
                <div class="ad-ufield portal" id="o-portal-wrap" hidden>
                  <label>Portál</label>
                  <div class="ad-uselect">
                    <select id="o-scrape-portal">${portalOpts}</select>
                    <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
                  </div>
                </div>
                <div class="ad-ufield fixed" id="o-pages-wrap">
                  <label>Max. stránek</label>
                  <div class="ad-uinput"><input id="o-scrape-pages" type="number" min="1" max="200" value="20" /></div>
                </div>
                <div class="ad-ufield when" id="o-schedule-wrap" hidden>
                  <label>Čas spuštění</label>
                  <div class="ad-uinput"><input id="o-scrape-when" type="datetime-local" /></div>
                </div>
              </div>
              <div class="ad-scrape-actions">
                <button class="ad-btn" type="button" id="o-scrape-run">Spustit scrape</button>
                <p class="ad-dedupe-note" id="o-scrape-note"></p>
              </div>
              <div id="o-schedule-list-wrap" ${(data.scrape_schedules || []).length ? "" : "hidden"}>
                <h3 class="ad-sec sm">Naplánované scrapy</h3>
                <div class="ad-scrape-schedule-list" id="o-schedule-list">${(data.scrape_schedules || []).map(scheduleRow).join("")}</div>
              </div>
            </div>
          </div>
        </article>
      </section>`;
    const charts = main.querySelectorAll(".ad-ops-chart");
    if (charts[0]) {
      bindTrafficHover(charts[0], apiPts, "ms", (i, rows) => {
        const hour = Math.round((i / Math.max(rows.length - 1, 1)) * 24);
        return `${String(hour).padStart(2, "0")}:00`;
      });
    }
    if (charts[1]) bindTrafficHover(charts[1], errPts, "chyb");
    let page = 1;
    const inWindow = (iso, when) => {
      if (!iso || !when) return true;
      const stamp = Date.parse(iso);
      if (!Number.isFinite(stamp)) return true;
      if (when === "1") {
        const start = new Date();
        start.setHours(0, 0, 0, 0);
        return stamp >= start.getTime();
      }
      const hours = Number(when) === 24 ? 24 : Number(when) * 24;
      return Date.now() - stamp <= hours * 3600 * 1000;
    };
    const applyFilter = () => {
      const lvl = $("o-lvl")?.value || "";
      const svc = $("o-svc")?.value || "";
      const when = $("o-when")?.value || "24";
      const match = logs.filter((row) => {
        if (lvl && row.level !== lvl) return false;
        if (svc && row.service !== svc) return false;
        if (!inWindow(row.at_iso, when)) return false;
        return true;
      });
      const pages = Math.max(1, Math.ceil(match.length / PAGE));
      if (page > pages) page = pages;
      const slice = match.slice((page - 1) * PAGE, page * PAGE);
      const rows = $("o-rows");
      if (rows) rows.innerHTML = slice.map(logRow).join("");
      const empty = $("o-empty");
      if (empty) {
        empty.hidden = slice.length > 0;
        empty.textContent = logs.length ? "Žádný záznam neodpovídá filtru." : "Zatím žádné logy.";
      }
      const from = slice.length ? (page - 1) * PAGE + 1 : 0;
      const to = slice.length ? (page - 1) * PAGE + slice.length : 0;
      const pager = $("o-pager");
      if (pager) pager.textContent = `Zobrazeno ${from ? from + "–" + to : "0"} z ${fmtN(match.length)} záznamů`;
      const host = $("o-pages");
      if (host) {
        const buttons = [];
        const maxBtn = Math.min(pages, 5);
        for (let i = 1; i <= maxBtn; i += 1) buttons.push(`<button type="button" data-page="${i}" class="${i === page ? "is-on" : ""}">${i}</button>`);
        if (pages > 5) buttons.push(`<span>…</span><button type="button" data-page="${pages}">${pages}</button>`);
        buttons.push(`<button type="button" class="next" data-page="${Math.min(pages, page + 1)}" ${page >= pages ? "disabled" : ""}>→</button>`);
        host.innerHTML = buttons.join("");
        host.querySelectorAll("button[data-page]").forEach((btn) => {
          btn.addEventListener("click", () => {
            page = Number(btn.dataset.page) || 1;
            applyFilter();
          });
        });
      }
    };
    $("o-filter")?.addEventListener("click", () => {
      page = 1;
      applyFilter();
    });
    applyFilter();
    const paintDedupe = (item) => {
      const c = item.counts || {};
      const s = item.scan_stats || {};
      const set = (id, value) => {
        const el = $(id);
        if (el) el.textContent = value;
      };
      set("d-listing-dups", fmtN(c.listing_dup_groups || 0));
      set("d-catalog-dups", fmtN(c.catalog_dup_groups || 0));
      set("d-relink", fmtN((s.listings_relinkable || 0) + (s.catalog_relinkable || 0)));
      set("d-status", item.status_label || "Čeká");
      set("d-last", item.last_run_rel || "—");
      set("d-scan", item.last_scan_rel || "—");
      const err = $("d-err");
      if (err) err.textContent = item.error || "";
      const note = $("d-note");
      const st = item.last_stats || {};
      if (note) {
        note.textContent = `Další běh: ${item.next || "—"}${
          st.listings_relinked != null
            ? ` · Poslední běh přepojil ${fmtN(st.listings_relinked || 0)} inzerátů a ${fmtN(st.catalog_relinked || 0)} katalogových záznamů.`
            : ""
        }`;
      }
      const samples = $("d-samples");
      if (samples) samples.innerHTML = (item.samples || []).map(sampleRow).join("");
      const empty = $("d-empty");
      if (empty) empty.hidden = Boolean((item.samples || []).length);
      ["d-run", "d-scan-btn", "d-save"].forEach((id) => {
        const btn = $(id);
        if (btn) btn.disabled = Boolean(item.busy);
      });
    };
    const pollDedupe = async () => {
      if (currentRoute() !== "provoz") return;
      try {
        const fresh = await api("/api/admin/dedupe");
        paintDedupe(fresh);
        if (fresh.busy) setTimeout(pollDedupe, 2500);
      } catch {
        setTimeout(pollDedupe, 4000);
      }
    };
    $("d-run")?.addEventListener("click", async () => {
      try {
        paintDedupe(await api("/api/admin/dedupe/run", { method: "POST" }));
        pollDedupe();
      } catch (err) {
        alert(err.message);
      }
    });
    $("d-scan-btn")?.addEventListener("click", async () => {
      try {
        paintDedupe(await api("/api/admin/dedupe/scan", { method: "POST" }));
        pollDedupe();
      } catch (err) {
        alert(err.message);
      }
    });
    $("d-save")?.addEventListener("click", async () => {
      try {
        const fresh = await api("/api/admin/dedupe/schedule", {
          method: "POST",
          body: JSON.stringify({
            enabled: Boolean($("d-enabled")?.checked),
            hour: Number($("d-hour")?.value || 3),
          }),
        });
        paintDedupe(fresh);
      } catch (err) {
        alert(err.message);
      }
    });
    $("o-scrape-run")?.addEventListener("click", async () => {
      const btn = $("o-scrape-run");
      const note = $("o-scrape-note");
      const scope = $("o-scope-choice")?.querySelector("button.is-on")?.dataset.scope || "url";
      const when = $("o-when-choice")?.querySelector("button.is-on")?.dataset.when || "now";
      const url = String($("o-scrape-url")?.value || "").trim();
      const portal = String($("o-scrape-portal")?.value || "").trim();
      const maxPages = Number($("o-scrape-pages")?.value || 20);
      const runAt = String($("o-scrape-when")?.value || "").trim();
      if (scope === "url" && !url) {
        alert("Vlož URL hledání");
        return;
      }
      if (scope === "portal" && !portal) {
        alert("Vyber portál");
        return;
      }
      if (when === "schedule" && !runAt) {
        alert("Zvol čas naplánování");
        return;
      }
      if (btn) btn.disabled = true;
      if (note) note.textContent = when === "schedule" ? "Ukládám plán…" : "Zařazuji do fronty…";
      try {
        const res = await api("/api/admin/scrape-url", {
          method: "POST",
          body: JSON.stringify({
            scope,
            when,
            url,
            portal,
            max_pages: maxPages,
            run_at: runAt,
          }),
        });
        const r = res.result || {};
        const paintSchedules = (rows) => {
          const list = $("o-schedule-list");
          const wrap = $("o-schedule-list-wrap");
          if (list) list.innerHTML = (rows || []).map(scheduleRow).join("");
          if (wrap) wrap.hidden = !(rows || []).length;
          bindCancelSchedule();
        };
        if (note) {
          if (r.scheduled) {
            note.textContent = `Naplánováno na ${esc(r.job?.run_at || runAt)}.`;
          } else if (scope === "portal") {
            note.textContent = r.queued
              ? `Celý katalog ${esc(portal)} zařazen do fronty.`
              : `Katalog sync ${esc(portal)} spuštěn.`;
          } else {
            note.textContent = r.queued
              ? `Zařazeno do fronty (${esc(r.url || url)}). Objeví se v „Poslední minutové běhy“.`
              : `Hotovo: ${fmtN(r.listings || 0)} listingů · nové ${fmtN(r.new || 0)} · změny ${fmtN(r.updated || 0)}`;
          }
        }
        paintSchedules(res.ops?.scrape_schedules || []);
        const ticks = $("o-ticks");
        if (ticks && res.ops?.recent_ticks) {
          ticks.innerHTML = res.ops.recent_ticks.map(tickRow).join("");
          const empty = $("o-ticks-empty");
          if (empty) empty.hidden = Boolean(res.ops.recent_ticks.length);
        }
        if (when === "now" && scope === "url") {
          let left = 8;
          const poll = async () => {
            if (currentRoute() !== "provoz" || left-- <= 0) return;
            try {
              const fresh = await api("/api/admin/ops");
              const box = $("o-ticks");
              if (box) box.innerHTML = (fresh.recent_ticks || []).map(tickRow).join("");
              paintSchedules(fresh.scrape_schedules || []);
              const done = (fresh.recent_ticks || []).some(
                (row) => row.kind === "manual_url" && row.url && String(row.url).includes(url.slice(0, 40))
              );
              if (!done) setTimeout(poll, 4000);
              else if (note) note.textContent = "Scrape dokončen — viz tabulka minutových běhů.";
            } catch {
              setTimeout(poll, 5000);
            }
          };
          setTimeout(poll, 3000);
        }
      } catch (err) {
        if (note) note.textContent = "";
        alert(err.message);
      } finally {
        if (btn) btn.disabled = false;
      }
    });
    const syncScrapeForm = () => {
      const scope = $("o-scope-choice")?.querySelector("button.is-on")?.dataset.scope || "url";
      const when = $("o-when-choice")?.querySelector("button.is-on")?.dataset.when || "now";
      const urlWrap = $("o-url-wrap");
      const portalWrap = $("o-portal-wrap");
      const pagesWrap = $("o-pages-wrap");
      const scheduleWrap = $("o-schedule-wrap");
      const runBtn = $("o-scrape-run");
      if (urlWrap) urlWrap.hidden = scope !== "url";
      if (portalWrap) portalWrap.hidden = scope !== "portal";
      if (pagesWrap) pagesWrap.hidden = scope !== "url";
      if (scheduleWrap) scheduleWrap.hidden = when !== "schedule";
      if (runBtn) runBtn.textContent = when === "schedule" ? "Naplánovat scrape" : scope === "portal" ? "Spustit katalog" : "Spustit scrape";
    };
    const bindChoice = (id, key) => {
      $(id)?.querySelectorAll("button").forEach((btn) => {
        btn.addEventListener("click", () => {
          $(id)?.querySelectorAll("button").forEach((item) => item.classList.toggle("is-on", item === btn));
          syncScrapeForm();
        });
      });
    };
    const bindCancelSchedule = () => {
      $("o-schedule-list")?.querySelectorAll("[data-cancel-schedule]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.getAttribute("data-cancel-schedule");
          if (!id) return;
          try {
            const res = await api(`/api/admin/scrape-schedule/${encodeURIComponent(id)}`, { method: "DELETE" });
            const list = $("o-schedule-list");
            const wrap = $("o-schedule-list-wrap");
            if (list) list.innerHTML = (res.ops?.scrape_schedules || []).map(scheduleRow).join("");
            if (wrap) wrap.hidden = !(res.ops?.scrape_schedules || []).length;
            bindCancelSchedule();
          } catch (err) {
            alert(err.message);
          }
        });
      });
    };
    bindChoice("o-scope-choice");
    bindChoice("o-when-choice");
    syncScrapeForm();
    bindCancelSchedule();
    if (d.busy) pollDedupe();
  }

  async function pageBilling() {
    const data = await api("/api/admin/billing");
    const rev = data.revenue || {};
    const points = rev.points || [];
    const labels = rev.labels || [];
    const kpis = data.kpis || [];
    const kpiCard = (item) => `<article class="ad-card ad-kpi">
        <div class="lbl">${esc(item.label)}</div>
        <div class="val"><strong>${esc(item.value)}</strong>${
          item.hint
            ? `<span class="ad-kpi-hint">${esc(item.hint)}</span>`
            : item.delta
              ? `<span class="ad-delta${item.delta_tone === "down" ? " bad" : ""}">${esc(item.delta)}</span>`
              : ""
        }</div>
      </article>`;
    const distCard = (row) => `<article class="ad-card ad-dist">
        <div class="ad-dist-top"><span>${esc(row.label)}</span>${
          row.id === "total" ? "" : `<b style="color:${esc(row.color)}">${esc(String(row.share).replace(".", ","))}%</b>`
        }</div>
        <strong>${esc(fmtN(row.n))}</strong>
        <div class="ad-dist-bar">${row.id === "total" ? "" : `<i style="width:${Number(row.bar) || 0}%;background:${esc(row.color)}"></i>`}</div>
      </article>`;
    const metricRow = (row, last) => `<div class="ad-mrow${last ? " last" : ""}">
        <div><p>${esc(row.label)}</p><span>${esc(row.hint || "")}</span></div>
        <strong class="${row.tone === "bad" ? "bad" : ""}">${esc(row.value)}${
          row.arrow
            ? `<span class="ad-ico ad-ico-14"><img src="/static/admin/assets/arrow.svg" width="14" height="14" alt="" /></span>`
            : ""
        }</strong>
      </div>`;
    const planRow = (row) => `<div class="ad-utbl-row${row.total ? " ad-plan-total" : ""}">
        <span>${esc(row.name)}</span>
        <span class="muted">${esc(row.price)}</span>
        <span class="muted">${esc(fmtN(row.active))}</span>
        <span class="num">${esc(row.mrr)}</span>
        <span class="num muted">${esc(row.share)}</span>
        <span class="num ${row.churn_ok || row.churn === "—" ? "muted" : "ad-churn"}">${esc(row.churn)}</span>
      </div>`;
    const txRow = (row) => `<div class="ad-utbl-row">
        <span class="muted">${esc(row.at)}</span>
        <span class="ad-utbl-mail">${esc(row.user)}</span>
        <span>${esc(row.type)}</span>
        <span class="muted">${esc(row.plan)}</span>
        <span class="num">${esc(row.amount)}</span>
        <span class="ad-nstat ${row.ok ? "ok" : row.warn ? "warn" : "bad"}">${esc(row.status)}</span>
      </div>`;
    const conv = data.conversion || [];
    const ret = data.retention || [];
    const plans = data.plans || [];
    const txs = data.transactions || [];
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Fakturace</h1>
        <p class="ad-lead">Přehled příjmů, předplatných a finanční metriky platformy.</p>
      </header>
      <section class="ad-nsec">
        <h2 class="ad-kicker">1. Hlavní KPI</h2>
        <div class="ad-bill-kpis">${kpis.map(kpiCard).join("")}</div>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">2. Rozdělení uživatelů</h2>
        <div class="ad-dist-row">${(data.split || []).map(distCard).join("")}</div>
      </section>
      <article class="ad-card ad-ops-chart ad-rev-card">
        <div class="ad-chart-head">
          <h2 class="ad-sec">Příjmy v čase (posledních 12 měsíců)</h2>
          <span class="ad-max-badge">Max: ${esc(fmtN(rev.max || 0))} Kč</span>
        </div>
        ${lineChart(points, labels)}
      </article>
      <section class="ad-nsec">
        <h2 class="ad-kicker">4. Klíčové metriky</h2>
        <div class="ad-bill-metrics">
          <article class="ad-card">
            <h3 class="ad-sec sm">Konverzní metriky</h3>
            ${conv.map((row, i) => metricRow(row, i === conv.length - 1)).join("")}
          </article>
          <article class="ad-card">
            <h3 class="ad-sec sm">Churn & retence</h3>
            ${ret.map((row, i) => metricRow(row, i === ret.length - 1)).join("")}
          </article>
        </div>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">5. Přehled tarifů</h2>
        <article class="ad-card ad-users-card">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-plan-tbl">
              <div class="ad-utbl-head">
                <span>Tarif</span><span>Cena/měsíc</span><span>Aktivních</span>
                <span class="num">MRR</span><span class="num">% z celkového MRR</span><span class="num">Churn rate</span>
              </div>
              <div>${plans.map(planRow).join("")}</div>
            </div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <div class="ad-tx-head">
          <h2 class="ad-kicker">6. Poslední transakce</h2>
          <button class="ad-csv-link" type="button" id="b-csv">Exportovat CSV</button>
        </div>
        <article class="ad-card ad-users-card">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-tx-tbl">
              <div class="ad-utbl-head">
                <span>Datum</span><span>Uživatel</span><span>Typ</span><span>Tarif</span><span class="num">Částka</span><span>Stav</span>
              </div>
              <div id="b-rows">${txs.map(txRow).join("")}</div>
              <div class="ad-utbl-empty" ${txs.length ? "hidden" : ""}>${data.configured ? "Zatím žádné Stripe faktury." : "Doplň STRIPE_SECRET_KEY, pak se načtou reálné platby."}</div>
            </div>
          </div>
        </article>
      </section>`;
    const chart = main.querySelector(".ad-rev-card");
    if (chart) {
      bindTrafficHover(chart, points, "Kč", (i) => labels[i] || "");
    }
    $("b-csv")?.addEventListener("click", () => {
      const header = "datum,uzivatel,typ,tarif,castka,stav\n";
      const body = txs
        .map((row) =>
          [row.at, row.user, row.type, row.plan, row.amount, row.status]
            .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
            .join(","),
        )
        .join("\n");
      const blob = new Blob([header + body], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "transakce.csv";
      a.click();
    });
  }

  async function pagePromo() {
    const monthQ = new URLSearchParams(location.search).get("month") || "";
    const data = await api(`/api/admin/promo${monthQ ? `?month=${encodeURIComponent(monthQ)}` : ""}`);
    const codes = data.codes || [];
    const campaigns = data.campaigns || [];
    const influencers = data.influencers || [];
    const payouts = data.payouts || [];
    const sources = data.sources || [];
    const months = data.months || [];
    const total = data.payout_total || {};
    const modal = (id, title, fields, submit) => `<div class="ad-preview" id="${id}" hidden>
        <form class="ad-preview-card ad-promo-form" data-kind="${submit}">
          <h2 class="ad-sec">${title}</h2>
          ${fields}
          <p class="ad-err" data-err hidden></p>
          <div class="ad-bc-actions">
            <button class="ad-btn outline" type="button" data-close>Zrušit</button>
            <button class="ad-btn" type="submit">Uložit</button>
          </div>
        </form>
      </div>`;
    const field = (name, label, type, extra = "") => `<div class="ad-ufield">
        <label>${label}</label>
        <div class="ad-uinput"><input name="${name}" type="${type}" ${extra} /></div>
      </div>`;
    main.innerHTML = `
      <header class="ad-pagehead">
        <h1 class="ad-h">Promo & influenceři</h1>
        <p class="ad-lead">Spravujte slevové kódy, promo akce a influencer program na jednom místě.</p>
      </header>
      <article class="ad-card ad-users-card">
        <div class="ad-promo-head">
          <h2 class="ad-sec">Slevové kódy</h2>
          <button class="ad-btn" type="button" data-open="p-code">+ Vytvořit slevový kód</button>
        </div>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl ad-promo-codes">
            <div class="ad-utbl-head"><span>Kód</span><span>Sleva (%)</span><span>Platnost od/do</span><span>Použití</span><span class="num">Stav</span></div>
            <div>${codes
              .map(
                (row) => `<div class="ad-utbl-row">
                <span class="ad-utbl-mail">${esc(row.code)}</span>
                <span><span class="ad-disc">${esc(row.discount)} %</span></span>
                <span class="muted">${esc(row.from)} – ${esc(row.to)}</span>
                <span>${esc(fmtN(row.used))}×</span>
                <span class="num"><button class="ad-toggle${row.on ? " on" : ""}" data-code="${esc(row.code)}" type="button"><i></i></button></span>
              </div>`,
              )
              .join("")}</div>
            <div class="ad-utbl-empty" ${codes.length ? "hidden" : ""}>${data.configured ? "Zatím žádné Stripe slevové kódy." : "Doplň STRIPE_SECRET_KEY."}</div>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <div class="ad-promo-head">
          <h2 class="ad-sec">Promo akce</h2>
          <button class="ad-btn" type="button" data-open="p-camp">+ Nová promo akce</button>
        </div>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl ad-promo-camp">
            <div class="ad-utbl-head"><span>Název</span><span>Období</span><span>Kód</span><span class="num">Nové registrace</span><span class="num">Předplatná</span><span class="num">Konverze</span></div>
            <div>${campaigns
              .map(
                (row) => `<div class="ad-utbl-row">
                <span>${esc(row.name)}</span>
                <span class="muted">${esc(row.period)}</span>
                <span class="muted">${esc(row.code)}</span>
                <span class="num">${esc(fmtN(row.regs))}</span>
                <span class="num">${esc(fmtN(row.paid))}</span>
                <span class="num"><span class="ad-disc">${esc(row.conv)}</span></span>
              </div>`,
              )
              .join("")}</div>
            <div class="ad-utbl-empty" ${campaigns.length ? "hidden" : ""}>Zatím žádné promo akce.</div>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <div class="ad-promo-head">
          <h2 class="ad-sec">Influencer program</h2>
          <button class="ad-btn" type="button" data-open="p-inf">+ Přidat influencera</button>
        </div>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl ad-promo-inf">
            <div class="ad-utbl-head"><span>Jméno</span><span>Kód</span><span>Provize (%)</span><span class="num">Registrace</span><span class="num">Předplatná</span><span class="num">Dlužná částka</span></div>
            <div>${influencers
              .map(
                (row) => `<div class="ad-utbl-row">
                <span>${esc(row.name)}</span>
                <span class="muted">${esc(row.code)}</span>
                <span>${esc(row.cut)} %</span>
                <span class="num">${esc(fmtN(row.regs))}</span>
                <span class="num">${esc(fmtN(row.paid))}</span>
                <span class="num">${esc(row.debt)}</span>
              </div>`,
              )
              .join("")}</div>
            <div class="ad-utbl-empty" ${influencers.length ? "hidden" : ""}>Zatím žádní influenceři.</div>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <div class="ad-promo-head">
          <div>
            <h2 class="ad-sec">Měsíční přehled</h2>
            <p class="ad-k">Přehled výplat a výkonu za konkrétní zúčtovací období.</p>
          </div>
          <div class="ad-month-tools">
            <div class="ad-uselect ad-month-dd">
              <select id="p-month">${months.map((row) => `<option value="${esc(row.id)}" ${row.id === data.month ? "selected" : ""}>${esc(row.label)}</option>`).join("")}</select>
              <span class="ad-ico ad-ico-chevron"><img src="/static/admin/assets/chevron.svg" width="8" height="5" alt="" /></span>
            </div>
            <button class="ad-btn outline" type="button" id="p-csv">Exportovat CSV</button>
          </div>
        </div>
        <div class="ad-utbl-wrap">
          <div class="ad-utbl ad-promo-pay">
            <div class="ad-utbl-head"><span>Influencer</span><span class="num">Registrace</span><span class="num">Předplatná</span><span class="num">Provize</span><span class="num">K vyplacení</span><span class="num">Stav</span></div>
            <div>${payouts
              .map(
                (row) => `<div class="ad-utbl-row">
                <span>${esc(row.name)}</span>
                <span class="num">${esc(fmtN(row.regs))}</span>
                <span class="num">${esc(fmtN(row.paid))}</span>
                <span class="num">${esc(row.cut)}</span>
                <span class="num">${esc(row.amount)}</span>
                <span class="num"><button class="ad-pay${row.ok ? "" : " bad"}" type="button" data-pay="${esc(row.code)}" data-on="${row.ok ? "0" : "1"}">${esc(row.status)}</button></span>
              </div>`,
              )
              .join("")}</div>
            <div class="ad-utbl-row ad-plan-total">
              <span>Celkem (Suma)</span>
              <span class="num">${esc(fmtN(total.regs || 0))}</span>
              <span class="num">${esc(fmtN(total.paid || 0))}</span>
              <span class="num">—</span>
              <span class="num">${esc(total.amount || "0 Kč")}</span>
              <span class="num">—</span>
            </div>
          </div>
        </div>
      </article>
      <article class="ad-card ad-users-card">
        <h2 class="ad-sec">Zdroje registrací</h2>
        <p class="ad-k">Analytický přehled výkonnosti jednotlivých akvizičních kanálů.</p>
        <div class="ad-src-grid">${sources
          .map(
            (s) => `<article class="ad-src-card">
              <div class="ad-dist-top"><span>${esc(s.label)}</span><span class="ad-delta">${esc(s.delta)}</span></div>
              <strong>${esc(fmtN(s.n))}</strong>
              <p class="ad-k">${esc(s.sub)}</p>
            </article>`,
          )
          .join("")}</div>
      </article>
      ${modal(
        "p-code",
        "Nový slevový kód",
        field("code", "Kód", "text", "required placeholder='LETO2026' maxlength='32'") +
          field("discount", "Sleva %", "number", "required min='1' max='100' value='20'") +
          field("from", "Platnost od", "date") +
          field("to", "Platnost do", "date"),
        "discount",
      )}
      ${modal(
        "p-camp",
        "Nová promo akce",
        field("name", "Název", "text", "required placeholder='Srovnávač kampaň'") +
          field("code", "Kód", "text", "required maxlength='32'") +
          field("discount", "Sleva %", "number", "required min='1' max='100' value='20'") +
          field("from", "Od", "date", "required") +
          field("to", "Do", "date", "required"),
        "campaign",
      )}
      ${modal(
        "p-inf",
        "Přidat influencera",
        field("name", "Jméno", "text", "required") +
          field("code", "Kód", "text", "required maxlength='32'") +
          field("discount", "Sleva pro zákazníka %", "number", "required min='1' max='100' value='10'") +
          field("cut", "Provize %", "number", "required min='1' max='100' value='10'"),
        "influencer",
      )}`;
    const reload = (month) => {
      const url = month ? `/admin/promo?month=${encodeURIComponent(month)}` : "/admin/promo";
      history.replaceState({}, "", url);
      pagePromo();
    };
    main.querySelectorAll("[data-open]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const box = $(btn.dataset.open);
        if (box) box.hidden = false;
      });
    });
    main.querySelectorAll("[data-close]").forEach((btn) => {
      btn.addEventListener("click", () => {
        btn.closest(".ad-preview").hidden = true;
      });
    });
    main.querySelectorAll(".ad-promo-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const err = form.querySelector("[data-err]");
        if (err) {
          err.hidden = true;
          err.textContent = "";
        }
        const body = Object.fromEntries(new FormData(form).entries());
        body.kind = form.dataset.kind;
        try {
          await api("/api/admin/promo/create", { method: "POST", body: JSON.stringify(body) });
          pagePromo();
        } catch (exc) {
          if (err) {
            err.hidden = false;
            err.textContent = exc.message;
          } else alert(exc.message);
        }
      });
    });
    main.querySelectorAll("[data-code]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          await api("/api/admin/promo/toggle", {
            method: "POST",
            body: JSON.stringify({ code: btn.dataset.code, on: !btn.classList.contains("on") }),
          });
          pagePromo();
        } catch (exc) {
          alert(exc.message);
        }
      });
    });
    main.querySelectorAll("[data-pay]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api("/api/admin/promo/payout", {
          method: "POST",
          body: JSON.stringify({ code: btn.dataset.pay, month: $("p-month")?.value || data.month, paid: btn.dataset.on === "1" }),
        });
        pagePromo();
      });
    });
    $("p-month")?.addEventListener("change", () => reload($("p-month").value));
    $("p-csv")?.addEventListener("click", () => {
      const header = "influencer,registrace,predplatna,provize,castka,stav\n";
      const body = payouts
        .map((row) =>
          [row.name, row.regs, row.paid, row.cut, row.amount, row.status]
            .map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`)
            .join(","),
        )
        .join("\n");
      const blob = new Blob([header + body], { type: "text/csv;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "vyplaty.csv";
      a.click();
    });
  }

  async function pageGames() {
    const data = await api("/api/admin/games");
    const stats = data.stats || {};
    const top = data.top || [];
    const recent = data.recent || [];
    const ccy = () => `<span class="ad-ccy-pill"><span>CZ</span> Kč</span>`;
    const row = (item, rank) => `<div class="ad-utbl-row">
        <span class="num">${rank != null ? rank : ""}</span>
        <span>${esc(item.player_name || "Anonym")}</span>
        <span class="num ad-game-score">${esc(fmtN(item.score || 0))}</span>
        <span class="num">${esc(String(item.accuracy ?? 0).replace(".", ","))} %</span>
        <span class="num muted">${esc(item.created_at || "").replace("T", " ").slice(0, 16)}</span>
      </div>`;
    const play = (item) => {
      const details = (item.items || [])
        .map((part) => {
          const pts = Number(part.points || 0);
          return `<div class="ad-game-guess">
            <div>
              <strong>${esc(part.locality || part.name || "Byt")}</strong>
              <span>tip ${esc(fmtN(part.guess || 0))} Kč · realita ${esc(fmtN(part.actual || 0))} Kč</span>
            </div>
            <div class="ad-game-guess-amt">
              ${ccy()}
              <em class="${pts >= 400 ? "is-ok" : ""}">${esc(part.points || 0)} b</em>
            </div>
          </div>`;
        })
        .join("");
      return `<article class="ad-game-play">
        <div class="ad-game-play-head">
          <span class="ad-game-chip">${esc(item.player_name || "Anonym")}</span>
          <strong>${esc(fmtN(item.score || 0))}</strong>
          <span>${esc(String(item.accuracy ?? 0).replace(".", ","))} %</span>
          <span class="muted">${esc(item.created_at || "").replace("T", " ").slice(0, 16)}</span>
        </div>
        ${details || '<p class="muted">Bez detailu tipů</p>'}
      </article>`;
    };
    main.innerHTML = `
      <header class="ad-pagehead">
        <p class="ad-eyebrow">TIP NÁJMU · ŽEBŘÍČEK</p>
        <h1 class="ad-h">HRY</h1>
        <p class="ad-lead">Skóre z /hry/najem v Kč. Hráči žebříček nevidí — jen admin. Pět bytů, 1 000 bodů za přesný tip.</p>
      </header>
      <section class="ad-game-kpis">
        <article class="ad-game-kpi"><span>Odehraných kol</span><strong>${esc(fmtN(stats.n || 0))}</strong></article>
        <article class="ad-game-kpi"><span>Nejlepší skóre</span><strong>${esc(fmtN(stats.best || 0))}</strong></article>
        <article class="ad-game-kpi"><span>Průměr skóre</span><strong>${esc(fmtN(Math.round(stats.avg_score || 0)))}</strong></article>
        <article class="ad-game-kpi"><span>Průměrná přesnost</span><strong>${esc(String(stats.avg_accuracy ?? 0).replace(".", ","))} %</strong></article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">Top skóre</h2>
        <article class="ad-card ad-game-board">
          <div class="ad-utbl-wrap">
            <div class="ad-utbl ad-game-tbl">
              <div class="ad-utbl-head"><span>#</span><span>Hráč</span><span class="num">Skóre</span><span class="num">Přesnost</span><span class="num">Kdy</span></div>
              ${top.map((item, idx) => row(item, idx + 1)).join("")}
              <div class="ad-utbl-empty" ${top.length ? "hidden" : ""}>Zatím žádné kolo — zahrajte /hry/najem.</div>
            </div>
          </div>
        </article>
      </section>
      <section class="ad-nsec">
        <h2 class="ad-kicker">Poslední hry</h2>
        ${recent.map(play).join("") || '<p class="muted">Nikdo ještě nehrál tip nájmu.</p>'}
      </section>
    `;
  }

  const cmsPages =
    typeof window.AdCms === "function"
      ? window.AdCms({ api, esc, ico, $, main, fmtN, getMe: () => me })
      : {};

  const routes = {
    prehled: pageOverview,
    uzivatele: pageUsers,
    uzivatel: pageUser,
    monitory: pageMonitors,
    notifikace: pageNotify,
    provoz: pageOps,
    hry: pageGames,
    fakturace: pageBilling,
    promo: pagePromo,
    ...cmsPages,
  };

  function syncAdminPath() {
    if (location.hash.startsWith("#/")) {
      history.replaceState({}, "", `/admin/${location.hash.slice(2)}`);
    }
    const raw = location.pathname.replace(/\/+$/, "") || "/admin";
    if (raw === "/admin") {
      history.replaceState({}, "", "/admin/prehled");
    }
  }

  function currentRoute() {
    const rest = location.pathname.replace(/^\/admin\/?/, "").replace(/\/+$/, "") || "prehled";
    if (rest.startsWith("uzivatele/")) return "uzivatel";
    if (rest === "cms" || rest.startsWith("cms/")) {
      if (rest.endsWith("/nahled")) return "cms-preview";
      if (rest === "cms") return "cms";
      return "cms-edit";
    }
    return rest.split("/")[0] || "prehled";
  }

  async function render() {
    if (!me) return;
    stopLive();
    syncAdminPath();
    const route = currentRoute();
    navOn(route === "uzivatel" ? "uzivatele" : route);
    main.classList.toggle("ad-overview", route === "prehled");
    main.classList.toggle("ad-users", route === "uzivatele");
    main.classList.toggle("ad-user", route === "uzivatel");
    main.classList.toggle("ad-monitors", route === "monitory");
    main.classList.toggle("ad-notify", route === "notifikace");
    main.classList.toggle("ad-ops", route === "provoz");
    main.classList.toggle("ad-games", route === "hry");
    main.classList.toggle("ad-bill", route === "fakturace");
    main.classList.toggle("ad-promo", route === "promo");
    const cms = String(route).startsWith("cms");
    main.classList.toggle("ad-cms", cms);
    main.classList.toggle("ad-cms-edit", route === "cms-edit");
    document.body.classList.toggle("ad-cms-mode", cms);
    const crumb = $("ad-crumb");
    const div = $("ad-nav-div");
    const navName = $("ad-nav-name");
    if (crumb && div && navName) {
      crumb.hidden = !cms;
      div.hidden = !cms;
      navName.hidden = !cms;
      const rest = location.pathname.replace(/^\/admin\/?/, "");
      crumb.textContent =
        route === "cms-preview"
          ? "CMS / Náhled článku"
          : rest === "cms/novy"
            ? "CMS / Nový příspěvek"
            : route === "cms-edit"
              ? "CMS / Upravit příspěvek"
              : "CMS / Správa článků";
    }
    const logoutTop = $("ad-logout-top");
    if (logoutTop) logoutTop.hidden = cms;
    const menu = document.querySelector(".ad-menu");
    if (menu) menu.style.display = cms ? "none" : "";
    main.innerHTML = "<p class='ad-lead'>Načítám…</p>";
    try {
      await (routes[route] || pageOverview)();
    } catch (err) {
      if (err.status === 401) {
        me = null;
        showLogin();
        return;
      }
      main.innerHTML = `<p class="ad-err">${esc(err.message)}</p>`;
    }
  }

  async function boot() {
    try {
      me = await api("/api/admin/me");
      showApp();
      render();
    } catch {
      showLogin();
    }
  }

  $("ad-login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("ad-login-err").textContent = "";
    try {
      me = await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ email: $("ad-email").value, password: $("ad-password").value }),
      });
      showApp();
      history.replaceState({}, "", "/admin/prehled");
      render();
    } catch (err) {
      $("ad-login-err").textContent = err.message;
    }
  });

  const logout = async () => {
    if (window.AdUnsavedLeave && !(await window.AdUnsavedLeave())) return;
    await api("/api/admin/logout", { method: "POST" }).catch(() => {});
    me = null;
    showLogin();
  };
  $("ad-logout").addEventListener("click", logout);
  $("ad-logout-top").addEventListener("click", logout);
  let navLock = false;
  let lastAdminUrl = location.pathname + location.search;
  document.addEventListener("click", async (event) => {
    const link = event.target.closest("a[href^='/admin']");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (link.target && link.target !== "_self") return;
    event.preventDefault();
    if (navLock) return;
    const href = link.getAttribute("href");
    navLock = true;
    try {
      if (window.AdUnsavedLeave && !(await window.AdUnsavedLeave())) return;
      if (href && href !== location.pathname + location.search) history.pushState({}, "", href);
      lastAdminUrl = location.pathname + location.search;
      if (me) await render();
    } finally {
      navLock = false;
    }
  });
  window.addEventListener("popstate", async () => {
    if (navLock) return;
    navLock = true;
    try {
      if (window.AdUnsavedLeave && !(await window.AdUnsavedLeave())) {
        history.pushState({}, "", lastAdminUrl);
        return;
      }
      lastAdminUrl = location.pathname + location.search;
      if (me) await render();
    } finally {
      navLock = false;
    }
  });
  boot();
})();
