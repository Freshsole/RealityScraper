(() => {
  window.AdCms = (ctx) => {
    const { api, esc, ico, main, fmtN, getMe } = ctx;
    const PAGE = 8;
    const STATS = [
      ["hledali", "Hledali"],
      ["typ", "Typ bytu"],
      ["lokalita", "Lokalita"],
      ["budget", "Budget"],
      ["monitoru", "Monitorů"],
      ["notifikace", "Notifikace"],
    ];
    const APT = [
      ["apt_dispozice", "Dispozice"],
      ["apt_lokalita", "Lokalita"],
      ["apt_velikost", "Velikost"],
      ["apt_najem", "Měsíční nájem"],
      ["apt_vztah", "Typ vztahu"],
    ];

    const statusLabel = (value) =>
      value === "published" ? "Publikováno" : value === "scheduled" ? "Naplánováno" : "Koncept";
    const statusClass = (value) =>
      value === "published" ? "is-pub" : value === "scheduled" ? "is-plan" : "is-draft";
    const fmtDate = (value) => {
      if (!value) return "—";
      const stamp = new Date(value);
      if (Number.isNaN(stamp.getTime())) return "—";
      return stamp.toLocaleDateString("cs-CZ");
    };
    const fmtTime = (value) => {
      const stamp = value ? new Date(value) : new Date();
      if (Number.isNaN(stamp.getTime())) return "";
      return stamp.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
    };
    const cmsId = () => {
      const rest = location.pathname.replace(/^\/admin\/cms\/?/, "").replace(/\/+$/, "");
      if (!rest || rest === "novy") return "novy";
      return rest.replace(/\/nahled$/, "");
    };
    const val = (el, name) => (el.querySelector(`[name="${name}"]`) || {}).value || "";
    const inp = (label, name, value, attrs = "") =>
      `<label class="cms-field"><span>${label}</span><input name="${name}" value="${esc(value || "")}" ${attrs} /></label>`;
    const area = (label, name, value, rows = 4) =>
      `<label class="cms-field"><span>${label}</span><textarea name="${name}" rows="${rows}">${esc(value || "")}</textarea></label>`;
    const locked = (label, name, value) =>
      `<label class="cms-lock"><span class="cms-lock-k">${esc(label)}</span><input name="${name}" value="${esc(value || "")}" /></label>`;
    const sel = (label, name, value, options) =>
      `<label class="cms-field"><span>${label}</span><span class="cms-select"><select name="${name}">${options
        .map(([id, text]) => `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(text)}</option>`)
        .join("")}</select>${ico("chevron", 8, 5)}</span></label>`;

    const collect = (root) => {
      const data = {};
      root.querySelectorAll("[name]").forEach((el) => {
        if (el.type === "checkbox") data[el.name] = el.checked;
        else data[el.name] = el.value;
      });
      data.stats = Object.fromEntries(STATS.map(([key]) => [key, data[key] || ""]));
      STATS.forEach(([key]) => delete data[key]);
      data.featured = Boolean(root.querySelector('[name="featured"]')?.checked);
      data.blocks = collectBlocks(root);
      return data;
    };

    const BLOCK_LABEL = { h: "Nadpis", p: "Odstavec", quote: "Citace" };

    function articleBlocks(a) {
      if (Array.isArray(a.blocks) && a.blocks.length) {
        return a.blocks.map((item) => ({ type: item.type === "h" || item.type === "quote" ? item.type : "p", text: item.text || "" }));
      }
      const flow = [
        ["h", a.start_h],
        ["p", a.start_p1],
        ["p", a.start_p2],
        ["h", a.discover_h],
        ["p", a.discover_p1],
        ["p", a.discover_p2],
        ["quote", a.quote],
        ["h", a.found_h],
        ["p", a.found_p1],
        ["p", a.found_p2],
        ["h", a.advice_h],
        ["p", a.advice_p],
      ];
      const legacy = flow.filter(([, text]) => text).map(([type, text]) => ({ type, text }));
      return legacy.length ? legacy : [{ type: "h", text: "" }, { type: "p", text: "" }];
    }

    function collectBlocks(root) {
      return [...(root.querySelectorAll("#cms-blocks [data-block]") || [])].map((el) => ({
        type: el.dataset.block,
        text: el.querySelector("[data-block-text]")?.value || "",
      }));
    }

    function blockField(item) {
      const type = item.type === "h" || item.type === "quote" ? item.type : "p";
      const field =
        type === "h"
          ? `<input data-block-text value="${esc(item.text || "")}" />`
          : `<textarea data-block-text rows="${type === "quote" ? 3 : 4}">${esc(item.text || "")}</textarea>`;
      return `<div class="cms-block" data-block="${type}">
        <div class="cms-block-bar">
          <strong>${BLOCK_LABEL[type]}</strong>
          <div class="cms-block-moves">
            <button class="cms-block-ico" type="button" data-block-up title="Posunout nahoru">↑</button>
            <button class="cms-block-ico" type="button" data-block-down title="Posunout dolů">↓</button>
            <button class="cms-block-ico is-del" type="button" data-block-del title="Smazat">${ico("trash", 14)}</button>
          </div>
        </div>
        ${field}
        <div class="cms-block-insert">
          <span>Vložit pod:</span>
          <button type="button" data-insert-block="h">Nadpis</button>
          <button type="button" data-insert-block="p">Odstavec</button>
          <button type="button" data-insert-block="quote">Citace</button>
        </div>
      </div>`;
    }

    function blockTools(where) {
      return `<div class="cms-block-tools" data-block-tools="${where}">
        <button class="cms-btn" type="button" data-add-block="h">${ico("plus", 14)} Nadpis</button>
        <button class="cms-btn" type="button" data-add-block="p">${ico("plus", 14)} Odstavec</button>
        <button class="cms-btn" type="button" data-add-block="quote">${ico("plus", 14)} Citace</button>
      </div>`;
    }

    function bindBlocks(root) {
      const list = root.querySelector("#cms-blocks");
      if (!list) return;
      const add = (type, after) => {
        const wrap = document.createElement("div");
        wrap.innerHTML = blockField({ type, text: "" });
        const node = wrap.firstElementChild;
        if (after === "start") list.insertBefore(node, list.firstChild);
        else if (after && after.parentNode === list) after.after(node);
        else list.appendChild(node);
        node.querySelector("[data-block-text]")?.focus();
        refreshDirty();
      };
      root.addEventListener("click", (event) => {
        const addBtn = event.target.closest("[data-add-block]");
        if (addBtn && root.contains(addBtn)) {
          event.preventDefault();
          const tools = addBtn.closest("[data-block-tools]");
          add(addBtn.dataset.addBlock, tools?.dataset.blockTools === "top" ? "start" : "end");
          return;
        }
        const insertBtn = event.target.closest("[data-insert-block]");
        if (insertBtn && root.contains(insertBtn)) {
          event.preventDefault();
          add(insertBtn.dataset.insertBlock, insertBtn.closest("[data-block]"));
          return;
        }
        const up = event.target.closest("[data-block-up]");
        if (up && root.contains(up)) {
          event.preventDefault();
          const row = up.closest("[data-block]");
          if (row?.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
          refreshDirty();
          return;
        }
        const down = event.target.closest("[data-block-down]");
        if (down && root.contains(down)) {
          event.preventDefault();
          const row = down.closest("[data-block]");
          if (row?.nextElementSibling) row.nextElementSibling.after(row);
          refreshDirty();
          return;
        }
        const del = event.target.closest("[data-block-del]");
        if (del && root.contains(del)) {
          event.preventDefault();
          del.closest("[data-block]")?.remove();
          refreshDirty();
        }
      });
    }

    const fmtViews = (n) => {
      const num = Number(n) || 0;
      if (num >= 10000) return `${(num / 1000).toFixed(1).replace(".", ",")}K`.replace(",0K", "K");
      return fmtN(num);
    };

    const fmtInqDate = (value) => {
      const stamp = value ? new Date(value) : null;
      if (!stamp || Number.isNaN(stamp.getTime())) return "—";
      const d = String(stamp.getDate()).padStart(2, "0");
      const m = String(stamp.getMonth() + 1).padStart(2, "0");
      return `${d}.${m}.${stamp.getFullYear()}`;
    };
    const inqStatus = (value) => {
      if (value === "contacted") return { cls: "is-inq-contacted", label: "Oslovený" };
      if (value === "done") return { cls: "is-inq-done", label: "Vyřešený" };
      return { cls: "is-inq-new", label: "Nový" };
    };
    const inqMoves = (value) => {
      if (value === "new") return [{ status: "contacted", label: "Označit jako oslovené" }];
      if (value === "contacted") {
        return [
          { status: "new", label: "Vrátit na nové" },
          { status: "done", label: "Označit jako vyřešené" },
        ];
      }
      return [{ status: "contacted", label: "Vrátit na oslovené" }];
    };
    const INQ_STATES = [
      ["new", "Nový"],
      ["contacted", "Oslovený"],
      ["done", "Vyřešený"],
    ];
    let inquiryItems = [];
    const inqById = (id) => inquiryItems.find((row) => row.id === id);
    const inqNewLabel = (n) => {
      const count = Number(n) || 0;
      const word = count === 1 ? "nový" : count >= 2 && count <= 4 ? "nové" : "nových";
      return `${count} ${word}`;
    };

    function inquiriesHtml(pack) {
      inquiryItems = pack.items || [];
      const items = inquiryItems;
      const rows =
        items
          .map((row) => {
            const st = inqStatus(row.status);
            const moves = inqMoves(row.status)
              .map(
                (move) =>
                  `<button class="cms-inq-next" type="button" data-inq-status="${esc(row.id)}" data-status="${move.status}">${esc(move.label)}</button>`,
              )
              .join("");
            return `<div class="cms-inq-row" data-inq-open="${esc(row.id)}">
              <span class="cms-inq-c-date">${esc(fmtInqDate(row.created_at))}</span>
              <span class="cms-inq-c-name" title="${esc(row.name)}">${esc(row.name)}</span>
              <span class="cms-inq-c-mail" title="${esc(row.email)}">${esc(row.email)}</span>
              <span class="cms-inq-c-phone">${esc(row.phone || "—")}</span>
              <span class="cms-inq-c-msg" title="${esc(row.message)}">${esc(row.message)}</span>
              <span class="cms-inq-c-src" title="${esc(row.source)}">${esc(row.source)}</span>
              <span class="cms-inq-c-st"><span class="cms-badge ${st.cls}">${esc(st.label)}</span></span>
              <span class="cms-inq-c-act">
                <button type="button" data-inq-open="${esc(row.id)}" title="Otevřít">${ico("edit", 16)}</button>
                <button type="button" data-inq-del="${esc(row.id)}" title="Smazat">${ico("trash", 16)}</button>
                ${moves}
              </span>
            </div>`;
          })
          .join("") || `<p class="cms-empty cms-inq-empty">Zatím žádné vyplnění formuláře.</p>`;
      return `
        <section class="cms-inq">
          <div class="cms-inq-head">
            <p>Došlé dotazy z formuláře</p>
            <span class="cms-inq-count">${esc(inqNewLabel(pack.new_count))}</span>
          </div>
          <div class="cms-inq-scroll">
            <div class="cms-inq-table">
              <div class="cms-inq-thead">
                <span class="cms-inq-c-date">DATUM</span>
                <span class="cms-inq-c-name">JMÉNO</span>
                <span class="cms-inq-c-mail">E-MAIL</span>
                <span class="cms-inq-c-phone">TELEFON</span>
                <span class="cms-inq-c-msg">ZPRÁVA</span>
                <span class="cms-inq-c-src">ZDROJ</span>
                <span class="cms-inq-c-st">STAV</span>
                <span class="cms-inq-c-act">AKCE</span>
              </div>
              ${rows}
            </div>
          </div>
        </section>
        <div class="cms-leave" id="cms-inq-modal" hidden>
          <div class="cms-leave-card cms-inq-card" role="dialog" aria-modal="true" aria-labelledby="cms-inq-title">
            <div class="cms-inq-pop-head">
              <h3 id="cms-inq-title">Vyplnění formuláře</h3>
              <button class="cms-inq-pop-x" type="button" data-inq-cancel aria-label="Zavřít">×</button>
            </div>
            <div id="cms-inq-pop-body"></div>
          </div>
        </div>`;
    }

    function inquiryPopupHtml(row) {
      const st = inqStatus(row.status);
      const chips = INQ_STATES.map(
        ([id, label]) =>
          `<button class="cms-inq-chip ${row.status === id ? "is-on" : ""}" type="button" data-inq-status="${esc(row.id)}" data-status="${id}">${esc(label)}</button>`,
      ).join("");
      const moves = inqMoves(row.status)
        .map(
          (move) =>
            `<button class="cms-inq-next" type="button" data-inq-status="${esc(row.id)}" data-status="${move.status}">${esc(move.label)}</button>`,
        )
        .join("");
      const line = (label, value, extra = "") =>
        `<div class="cms-inq-kv"><span>${esc(label)}</span><p ${extra}>${value}</p></div>`;
      return `
        <div class="cms-inq-pop-status">
          <span class="cms-badge ${st.cls}">${esc(st.label)}</span>
          <div class="cms-inq-chips">${chips}</div>
        </div>
        ${line("Datum", esc(fmtInqDate(row.created_at)))}
        ${line("Jméno", esc(row.name || "—"))}
        ${line("E-mail", row.email ? `<a href="mailto:${esc(row.email)}">${esc(row.email)}</a>` : "—")}
        ${line("Telefon", row.phone ? `<a href="tel:${esc(row.phone)}">${esc(row.phone)}</a>` : "—")}
        ${line("Předmět", esc(row.subject || "—"))}
        ${line("Zdroj", esc(row.source || "—"))}
        ${line("Zpráva", esc(row.message || "—"), 'class="cms-inq-msg"')}
        <div class="cms-leave-acts cms-inq-pop-acts">
          ${moves}
          <button class="cms-btn" type="button" data-inq-cancel>Zavřít</button>
        </div>`;
    }

    function bindInquiries(root) {
      const modal = root.querySelector("#cms-inq-modal");
      const body = root.querySelector("#cms-inq-pop-body");
      const closeModal = () => {
        if (modal) modal.hidden = true;
      };
      const openInquiry = (id) => {
        const row = inqById(id);
        if (!row || !modal || !body) return;
        body.innerHTML = inquiryPopupHtml(row);
        modal.hidden = false;
      };
      const setStatus = async (id, status, reopen) => {
        await api(`/api/admin/cms-inquiry/${id}`, { method: "POST", body: JSON.stringify({ status }) });
        await pageList();
        if (reopen) {
          const nextModal = main.querySelector("#cms-inq-modal");
          const nextBody = main.querySelector("#cms-inq-pop-body");
          const row = inqById(id);
          if (nextModal && nextBody && row) {
            nextBody.innerHTML = inquiryPopupHtml(row);
            nextModal.hidden = false;
          }
        }
      };
      root.addEventListener("click", (event) => {
        const del = event.target.closest("[data-inq-del]");
        if (del && root.contains(del)) {
          event.preventDefault();
          event.stopPropagation();
          if (!confirm("Opravdu smazat tento dotaz?")) return;
          api(`/api/admin/cms-inquiry/${del.dataset.inqDel}/delete`, { method: "POST" }).then(() => pageList());
          return;
        }
        const statusBtn = event.target.closest("[data-inq-status]");
        if (statusBtn && root.contains(statusBtn)) {
          event.preventDefault();
          event.stopPropagation();
          const reopen = Boolean(statusBtn.closest("#cms-inq-modal"));
          setStatus(statusBtn.dataset.inqStatus, statusBtn.dataset.status, reopen);
          return;
        }
        const open = event.target.closest("[data-inq-open]");
        if (open && root.contains(open)) {
          event.preventDefault();
          event.stopPropagation();
          openInquiry(open.dataset.inqOpen);
        }
      });
      modal?.addEventListener("click", (event) => {
        if (event.target === modal || event.target.closest("[data-inq-cancel]")) closeModal();
      });
    }

    async function pageList() {
      isDirty = false;
      window.AdUnsavedLeave = async () => true;
      const data = await api("/api/admin/cms");
      let page = Number(new URL(location.href).searchParams.get("p") || 1);
      const q = (new URL(location.href).searchParams.get("q") || "").trim().toLowerCase();
      const status = new URL(location.href).searchParams.get("status") || "";
      const category = new URL(location.href).searchParams.get("cat") || "";
      let rows = data.articles || [];
      if (q) {
        rows = rows.filter((row) => `${row.title} ${row.slug} ${row.author}`.toLowerCase().includes(q));
      }
      if (status) rows = rows.filter((row) => row.status === status);
      if (category) rows = rows.filter((row) => row.category === category);
      const pages = Math.max(1, Math.ceil(rows.length / PAGE));
      page = Math.min(page, pages);
      const slice = rows.slice((page - 1) * PAGE, page * PAGE);
      const cats = ["", ...(data.categories || [])];
      const pager = Array.from({ length: pages }, (_, i) => i + 1)
        .map((n) => `<a class="cms-page ${n === page ? "is-on" : ""}" href="/admin/cms?p=${n}">${n}</a>`)
        .join("");
      main.innerHTML = `
        <div class="cms-headrow">
          <div>
            <h1 class="ad-h">SPRÁVA ČLÁNKŮ</h1>
            <p class="ad-lead">Vytvářejte a spravujte úspěšné příběhy, rady a trendy ze světa bydlení.</p>
          </div>
          <a class="cms-pill cms-pill-green" href="/admin/cms/novy">${ico("plus", 14)} Nový příspěvek</a>
        </div>
        <div class="cms-kpis">
          <article class="cms-kpi">
            <p>Publikované články</p>
            <div class="cms-kpi-row"><strong>${esc(data.stats.published)}</strong></div>
            <span>Aktivně na webu</span>
          </article>
          <article class="cms-kpi">
            <p>Rozepsané koncepty</p>
            <div class="cms-kpi-row"><strong>${esc(data.stats.drafts)}</strong></div>
            <span>Vyžadují dopracování</span>
          </article>
          <article class="cms-kpi">
            <p>Naplánované publikace</p>
            <div class="cms-kpi-row"><strong>${esc(data.stats.scheduled)}</strong></div>
            <span>Automatické vydání</span>
          </article>
          <article class="cms-kpi">
            <p>Celkem zobrazení</p>
            <div class="cms-kpi-row"><strong>${esc(fmtViews(data.stats.views))}</strong></div>
            <span>Součet zobrazení článků</span>
          </article>
        </div>
        <form class="cms-filters" id="cms-filters">
          <label class="cms-search">
            ${ico("search", 16)}
            <input name="q" value="${esc(q)}" placeholder="Hledat v článcích..." />
          </label>
          <label class="cms-filter">
            <span>Stav:</span>
            <select name="status">
              <option value="">Všechny</option>
              <option value="published" ${status === "published" ? "selected" : ""}>Publikováno</option>
              <option value="scheduled" ${status === "scheduled" ? "selected" : ""}>Naplánováno</option>
              <option value="draft" ${status === "draft" ? "selected" : ""}>Koncept</option>
            </select>
            ${ico("chevron", 8, 5)}
          </label>
          <label class="cms-filter">
            <span>Kategorie:</span>
            <select name="cat">
              ${cats
                .map(
                  (c) =>
                    `<option value="${esc(c)}" ${c === category ? "selected" : ""}>${esc(c || "Všechny")}</option>`,
                )
                .join("")}
            </select>
            ${ico("chevron", 8, 5)}
          </label>
        </form>
        <section class="cms-table-wrap">
          <div class="cms-thead">
            <span class="cms-col-title">NÁZEV ČLÁNKU</span>
            <span class="cms-col-status">STAV</span>
            <span class="cms-col-cat">KATEGORIE</span>
            <span class="cms-col-author">AUTOR</span>
            <span class="cms-col-date">PUBLIKACE</span>
            <span class="cms-col-views">ZOBRAZENÍ</span>
            <span class="cms-col-act">AKCE</span>
          </div>
          ${
            slice
              .map(
                (row) => `
            <div class="cms-trow">
              <a class="cms-col-title" href="/admin/cms/${esc(row.id)}">${esc(row.title || "Bez názvu")}</a>
              <span class="cms-col-status"><span class="cms-badge ${statusClass(row.status)}">${esc(statusLabel(row.status))}</span></span>
              <span class="cms-col-cat">${esc(row.category || "Úspěšné příběhy")}</span>
              <span class="cms-col-author">${esc(row.cms_author || "Admin")}</span>
              <span class="cms-col-date">${esc(fmtDate(row.published_at))}</span>
              <span class="cms-col-views">${esc(fmtN(row.views || 0))}</span>
              <span class="cms-col-act">
                <a href="/admin/cms/${esc(row.id)}" title="Upravit">${ico("edit", 16)}</a>
                <button type="button" data-del="${esc(row.id)}" title="Smazat">${ico("trash", 16)}</button>
              </span>
            </div>`,
              )
              .join("") || `<p class="cms-empty">Žádné články.</p>`
          }
          <div class="cms-tfoot">
            <span>Zobrazeno ${(page - 1) * PAGE + (slice.length ? 1 : 0)}–${(page - 1) * PAGE + slice.length} z celkem ${rows.length} článků</span>
            <div class="cms-pager">${page > 1 ? `<a href="/admin/cms?p=${page - 1}">Předchozí</a>` : `<span>Předchozí</span>`}${pager}${page < pages ? `<a href="/admin/cms?p=${page + 1}">Další</a>` : `<span>Další</span>`}</div>
          </div>
        </section>
        ${inquiriesHtml(data.inquiries || { items: [], new_count: 0 })}`;
      main.querySelector("#cms-filters")?.addEventListener("change", (event) => {
        const form = event.currentTarget;
        const params = new URLSearchParams(new FormData(form));
        history.pushState({}, "", `/admin/cms?${params.toString()}`);
        pageList();
      });
      main.querySelector('#cms-filters [name="q"]')?.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        event.currentTarget.form.dispatchEvent(new Event("change"));
      });
      main.querySelectorAll("[data-del]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("Opravdu smazat tento článek?")) return;
          await api(`/api/admin/cms/${btn.dataset.del}/delete`, { method: "POST" });
          pageList();
        });
      });
      bindInquiries(main);
    }

    function editorHtml(a, isNew) {
      const stats = a.stats || {};
      const saved = a.updated_at ? `Uloženo v ${fmtTime(a.updated_at)}` : "Všechny změny jsou uložené";
      return `
        <div class="cms-edhead">
          <a class="cms-back" href="/admin/cms">${ico("arrow-left", 16)} Zpět na přehled</a>
          ${dirtyBar(saved)}
        </div>
        <h1 class="ad-h">${isNew ? "NOVÝ PŘÍSPĚVEK" : "UPRAVIT PŘÍSPĚVEK"}</h1>
        <form id="cms-form" class="cms-editor">
          <input type="hidden" name="id" value="${esc(a.id || "")}" />
          <div class="cms-left">
            ${inp("Název článku", "title", a.title)}
            <div class="cms-group">
              <h3>Hrdina příběhu</h3>
              ${inp("Jméno v drobečkové navigaci", "crumb", a.crumb)}
              ${inp("Jména v článku", "author", a.author, 'placeholder="Martina a Tomáš, Praha"')}
              ${inp("Podtitul autora", "role", a.role, 'placeholder="Úspěšní nájemníci v Dejvicích"')}
              ${inp("Štítek nad titulkem", "badge", a.badge)}
              ${area("Citát pod titulkem", "lede", a.lede, 3)}
            </div>
            <div class="cms-group">
              <h3>Karta na /uspechy</h3>
              ${inp("Štítek karty", "card_badge", a.card_badge)}
              ${area("Perex karty", "card_excerpt", a.card_excerpt, 3)}
              ${area("Citace ve featured bloku", "featured_quote", a.featured_quote, 2)}
              <div class="cms-triple">
                ${inp("Lokalita (tag)", "tag_place", a.tag_place)}
                ${inp("Dispozice (tag)", "tag_spec", a.tag_spec)}
                ${inp("Cena (tag)", "tag_price", a.tag_price)}
              </div>
              <label class="cms-check"><input type="checkbox" name="featured" ${a.featured ? "checked" : ""} /> Příběh měsíce (featured na výpisu)</label>
            </div>
            <div class="cms-group">
              <h3>Statistiky článku</h3>
              <p class="cms-hint">Popisky jsou pevné. Doplňujete jen hodnoty.</p>
              <div class="cms-locks">${STATS.map(([key, label]) => locked(label, key, stats[key])).join("")}</div>
            </div>
            <div class="cms-group">
              <h3>Tělo článku</h3>
              <p class="cms-hint">Nadpisy, odstavce a citace můžete přidat kamkoli a měnit jejich pořadí.</p>
              ${blockTools("top")}
              <div id="cms-blocks">${articleBlocks(a).map((item) => blockField(item)).join("")}</div>
              ${blockTools("bottom")}
            </div>
            <div class="cms-group">
              <h3>Nalezený byt v detailech</h3>
              <p class="cms-hint">Popisky Dispozice / Lokalita / Velikost / Nájem / Typ vztahu jsou dané.</p>
              ${inp("Nadpis boxu", "apt_title", a.apt_title)}
              <div class="cms-locks">${APT.map(([key, label]) => locked(label, key, a[key])).join("")}</div>
            </div>
          </div>
          <aside class="cms-right">
            <h2>NASTAVENÍ ČLÁNKU</h2>
            ${sel("Stav publikace", "status", a.status || "draft", [
              ["draft", "Koncept"],
              ["scheduled", "Naplánováno"],
              ["published", "Publikováno"],
            ])}
            <label class="cms-field">
              <span>Vlastní URL slug</span>
              <input name="slug" value="${esc(a.slug || "")}" placeholder="martina-a-tomas" autocomplete="off" ${a.slug ? "data-custom=\"1\"" : ""} />
              <p class="cms-url-preview" id="cms-public-url"></p>
            </label>
            ${inp("Datum publikace", "published_at", (a.published_at || "").slice(0, 16), 'type="datetime-local"')}
            ${inp("Kategorie", "category", a.category || "Úspěšné příběhy")}
            ${inp("Autor", "cms_author", a.cms_author || getMe()?.name || "Admin")}
            <div class="cms-field">
              <span>Náhledový obrázek</span>
              <label class="cms-upload" id="cms-upload">
                <input type="file" accept="image/jpeg,image/png,image/webp" hidden />
                <img id="cms-photo-preview" src="${esc(a.photo || "")}" alt="" ${a.photo ? "" : "hidden"} />
                <span id="cms-upload-empty" ${a.photo ? "hidden" : ""}>
                  ${ico("upload-cloud", 24)}
                  <b>Nahrát obrázek (JPG, PNG)</b>
                  <small>Doporučený poměr 16:9 (max 5MB)</small>
                </span>
              </label>
              <input type="hidden" name="photo" value="${esc(a.photo || "")}" />
              ${inp("Obrázek na výpisu", "list_photo", a.list_photo)}
            </div>
            ${inp("Klíčová slova / Tagy", "seo_tags", a.seo_tags)}
            ${area("SEO Meta popisek", "seo_desc", a.seo_desc, 3)}
          </aside>
        </form>
        <div class="cms-actions">
          <button class="cms-btn cms-btn-danger" type="button" id="cms-delete" ${isNew ? "hidden" : ""}>Smazat</button>
          <div class="cms-actions-right">
            <button class="cms-btn" type="button" id="cms-draft" ${a.status === "published" || isNew || a.status === "scheduled" ? "" : "hidden"}>${a.status === "published" ? "Převest na koncept" : "Uložit koncept"}</button>
            <a class="cms-btn cms-btn-line" id="cms-preview-btn" href="/admin/cms/${esc(a.id || "novy")}/nahled">Náhled</a>
            ${liveButton(a.slug, a.status)}
            <button class="cms-btn cms-btn-dark" type="button" id="cms-publish" ${a.status === "published" ? "hidden" : ""}>Publikovat článek</button>
          </div>
        </div>`;
    }

    function dirtyBar(saved) {
      return `<div class="cms-savebar">
        <p class="cms-autosave" id="cms-autosave"><span></span> <em id="cms-autosave-text">${esc(saved)}</em></p>
        <div class="cms-dirty-acts" id="cms-dirty-acts" hidden>
          <button class="cms-btn" type="button" id="cms-discard">Zahodit</button>
          <button class="cms-btn cms-btn-dark" type="button" id="cms-save">Uložit změny</button>
        </div>
      </div>`;
    }

    function currentStatus() {
      return main.querySelector('[name="status"]')?.value || "draft";
    }

    function slugify(value) {
      return String(value || "")
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80);
    }

    function publicPath(slug) {
      return `/uspechy/${slugify(slug) || "…"}`;
    }

    function liveButton(slug, status) {
      const path = publicPath(slug);
      const live = status === "published" && Boolean(slugify(slug));
      return `<a class="cms-btn cms-btn-line" id="cms-live-link" href="${esc(path)}" target="_blank" rel="noopener" ${live ? "" : "hidden"}>Otevřít na webu</a>`;
    }

    function syncLiveLink() {
      const link = main.querySelector("#cms-live-link");
      if (!link) return;
      const slug = main.querySelector('[name="slug"]')?.value || "";
      const live = currentStatus() === "published" && Boolean(slugify(slug));
      if (slugify(slug)) link.setAttribute("href", publicPath(slug));
      setHidden(link, !live);
    }

    function syncPublicUrl() {
      const slugField = main.querySelector('[name="slug"]');
      const preview = main.querySelector("#cms-public-url");
      if (!slugField || !preview) return;
      const path = publicPath(slugField.value || main.querySelector('[name="title"]')?.value);
      const hasSlug = Boolean(slugify(slugField.value));
      const live = currentStatus() === "published" && hasSlug;
      if (live) {
        preview.innerHTML = `Živě na webu: <a href="${esc(path)}" target="_blank" rel="noopener">${esc(path)}</a>`;
      } else if (hasSlug) {
        preview.innerHTML = `Článek bude na <span>${esc(path)}</span>`;
      } else {
        preview.innerHTML = `Článek bude na <span>/uspechy/…</span>`;
      }
      syncLiveLink();
    }

    function setHidden(el, hide) {
      if (!el) return;
      el.hidden = hide;
      el.toggleAttribute("hidden", hide);
    }

    function syncSaveActions() {
      const status = currentStatus();
      const draftBtn = main.querySelector("#cms-draft");
      const publishBtn = main.querySelector("#cms-publish");
      if (draftBtn && publishBtn) {
        if (status === "published") {
          setHidden(draftBtn, false);
          draftBtn.textContent = "Převest na koncept";
          setHidden(publishBtn, true);
        } else {
          const inProgress = cmsId() === "novy" || status === "scheduled";
          draftBtn.textContent = "Uložit koncept";
          setHidden(draftBtn, !inProgress);
          setHidden(publishBtn, false);
        }
      }
      syncPublicUrl();
    }

    function showToast(message, isError) {
      let el = document.getElementById("ad-toast");
      if (!el) {
        el = document.createElement("div");
        el.id = "ad-toast";
        document.body.appendChild(el);
      }
      el.className = `ad-toast${isError ? " is-err" : ""}`;
      el.textContent = message;
      el.hidden = false;
      el.classList.remove("is-on");
      void el.offsetWidth;
      el.classList.add("is-on");
      clearTimeout(el._hide);
      el._hide = setTimeout(() => el.classList.remove("is-on"), 4500);
    }

    let isDirty = false;
    let lastSnap = "";
    let snapshotOf = () => "";
    let savePending = async () => null;
    let discardPending = async () => {};
    let leaveBusy = false;

    function leaveModal() {
      let el = document.getElementById("cms-leave");
      if (el) return el;
      el = document.createElement("div");
      el.id = "cms-leave";
      el.className = "cms-leave";
      el.hidden = true;
      el.innerHTML = `
        <div class="cms-leave-card" role="dialog" aria-modal="true" aria-labelledby="cms-leave-title">
          <h3 id="cms-leave-title">Neuložené změny</h3>
          <p>Máte neuložené změny. Chcete je uložit, nebo zahodit?</p>
          <div class="cms-leave-acts">
            <button type="button" class="cms-btn" data-act="discard">Zahodit</button>
            <button type="button" class="cms-btn cms-btn-line" data-act="stay">Zůstat</button>
            <button type="button" class="cms-btn cms-btn-dark" data-act="save">Uložit změny</button>
          </div>
        </div>`;
      document.body.appendChild(el);
      return el;
    }

    function askUnsaved() {
      const el = leaveModal();
      el.hidden = false;
      return new Promise((resolve) => {
        const done = (value) => {
          el.hidden = true;
          el.onclick = null;
          el.querySelectorAll("[data-act]").forEach((btn) => {
            btn.onclick = null;
          });
          resolve(value);
        };
        el.onclick = (event) => {
          if (event.target === el) done("stay");
        };
        el.querySelector('[data-act="stay"]').onclick = () => done("stay");
        el.querySelector('[data-act="discard"]').onclick = () => done("discard");
        el.querySelector('[data-act="save"]').onclick = () => done("save");
      });
    }

    function setDirtyState(on) {
      isDirty = Boolean(on);
      const acts = main.querySelector("#cms-dirty-acts");
      const mark = main.querySelector("#cms-autosave");
      const text = main.querySelector("#cms-autosave-text");
      if (acts) acts.hidden = !isDirty;
      if (mark) mark.classList.toggle("is-warn", isDirty);
      if (text) text.textContent = isDirty ? "Máte neuložené změny" : "Všechny změny jsou uložené";
    }

    function refreshDirty() {
      setDirtyState(snapshotOf() !== lastSnap);
    }

    function markSaved() {
      lastSnap = snapshotOf();
      setDirtyState(false);
    }

    function bindUnsaved({ snapshot, save, discard }) {
      snapshotOf = snapshot;
      savePending = save;
      discardPending = discard;
      lastSnap = snapshotOf();
      setDirtyState(false);
      window.AdUnsavedLeave = confirmLeave;
      main.querySelector("#cms-save")?.addEventListener("click", async () => {
        try {
          await savePending();
          markSaved();
          showToast("Změny uloženy.");
        } catch (err) {
          showToast(err.message || "Uložení se nepodařilo.", true);
        }
      });
      main.querySelector("#cms-discard")?.addEventListener("click", async () => {
        await discardPending();
      });
    }

    async function confirmLeave() {
      if (!isDirty) return true;
      if (leaveBusy) return false;
      leaveBusy = true;
      try {
        const choice = await askUnsaved();
        if (choice === "stay") return false;
        if (choice === "save") {
          await savePending();
          markSaved();
        }
        isDirty = false;
        return true;
      } catch (err) {
        showToast(err.message || "Uložení se nepodařilo.", true);
        return false;
      } finally {
        leaveBusy = false;
      }
    }

    window.AdUnsavedLeave = async () => true;
    window.addEventListener("beforeunload", (event) => {
      if (!isDirty) return;
      event.preventDefault();
      event.returnValue = "";
    });

    let heldStatus = null;

    async function saveStatus(status) {
      const id = cmsId();
      heldStatus = status;
      if (id === "novy") return saveForm(status);
      const saved = await api(`/api/admin/cms/${id}`, {
        method: "POST",
        body: JSON.stringify({ status }),
      });
      heldStatus = null;
      const field = main.querySelector('[name="status"]');
      if (field) field.value = status;
      syncSaveActions();
      return saved;
    }

    async function saveForm(status) {
      const form = main.querySelector("#cms-form");
      if (!form) return null;
      if (heldStatus && !status) status = heldStatus;
      const payload = collect(form);
      if (payload.published_at && payload.published_at.length === 16) payload.published_at = `${payload.published_at}:00`;
      if (status) payload.status = status;
      else if (heldStatus) payload.status = heldStatus;
      const id = cmsId();
      const saved =
        id === "novy"
          ? await api("/api/admin/cms", { method: "POST", body: JSON.stringify(payload) })
          : await api(`/api/admin/cms/${id}`, { method: "POST", body: JSON.stringify(payload) });
      if (id === "novy" && saved.id) history.replaceState({}, "", `/admin/cms/${saved.id}`);
      const preview = main.querySelector("#cms-preview-btn");
      if (preview && saved.id) preview.setAttribute("href", `/admin/cms/${saved.id}/nahled`);
      const statusField = main.querySelector('[name="status"]');
      if (statusField) statusField.value = heldStatus || saved.status || statusField.value;
      const slugField = main.querySelector('[name="slug"]');
      if (slugField && saved.slug) {
        slugField.value = saved.slug;
        slugField.dataset.custom = "1";
      }
      syncPublicUrl();
      syncSaveActions();
      markSaved();
      return saved;
    }

    async function pageEdit() {
      const id = cmsId();
      const article = id === "novy" ? { cms_author: getMe()?.name || "Admin", status: "draft" } : await api(`/api/admin/cms/${id}`);
      if (article.published_at && article.published_at.length > 16) {
        article.published_at = article.published_at.slice(0, 16);
      }
      main.innerHTML = editorHtml(article, id === "novy");
      const form = main.querySelector("#cms-form");
      const slugField = form.querySelector('[name="slug"]');
      const titleField = form.querySelector('[name="title"]');
      const fillSlugFromTitle = () => {
        if (!slugField || slugField.dataset.custom === "1") return;
        slugField.value = slugify(titleField?.value || "");
        syncPublicUrl();
      };
      slugField?.addEventListener("input", () => {
        slugField.dataset.custom = slugify(slugField.value) ? "1" : "";
        syncPublicUrl();
      });
      titleField?.addEventListener("input", fillSlugFromTitle);
      fillSlugFromTitle();
      syncPublicUrl();
      bindBlocks(form);
      bindUnsaved({
        snapshot: () => JSON.stringify(collect(form)),
        save: () => saveForm(),
        discard: async () => {
          isDirty = false;
          await pageEdit();
        },
      });
      form.addEventListener("input", refreshDirty);
      form.addEventListener("change", (event) => {
        if (event.target?.name === "status") syncSaveActions();
        refreshDirty();
      });
      syncSaveActions();
      const upload = main.querySelector("#cms-upload input[type=file]");
      upload?.addEventListener("change", async () => {
        const file = upload.files?.[0];
        if (!file) return;
        const body = new FormData();
        body.append("file", file);
        const res = await fetch("/api/admin/cms-upload", { method: "POST", body, credentials: "same-origin" });
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || "Nahrání selhalo");
          return;
        }
        form.querySelector('[name="photo"]').value = data.url;
        if (!form.querySelector('[name="list_photo"]').value) form.querySelector('[name="list_photo"]').value = data.url;
        const img = main.querySelector("#cms-photo-preview");
        img.src = data.url;
        img.hidden = false;
        main.querySelector("#cms-upload-empty").hidden = true;
        refreshDirty();
      });
      main.querySelector("#cms-draft")?.addEventListener("click", () => {
        const status = currentStatus();
        const field = main.querySelector('[name="status"]');
        if (status === "published") {
          if (field) field.value = "draft";
          syncSaveActions();
          showToast("Stav změněn: Publikováno → Koncept. Článek už není veřejný.");
          saveForm("draft").catch((err) => {
            if (field) field.value = "published";
            heldStatus = null;
            syncSaveActions();
            refreshDirty();
            showToast(err.message || "Převod na koncept se nepodařil.", true);
          });
          return;
        }
        saveForm(status === "scheduled" ? "scheduled" : "draft")
          .then(() => showToast("Stav: koncept uložen."))
          .catch((err) => showToast(err.message || "Uložení se nepodařilo.", true));
      });
      main.querySelector("#cms-publish")?.addEventListener("click", () => {
        const field = main.querySelector('[name="status"]');
        const previous = currentStatus();
        if (field) field.value = "published";
        syncSaveActions();
        showToast("Stav změněn: Koncept → Publikováno. Článek je na webu.");
        saveForm("published")
          .then((saved) => {
            if (saved?.id) {
              isDirty = false;
              history.pushState({}, "", `/admin/cms/${saved.id}/nahled`);
              return pagePreview();
            }
          })
          .catch((err) => {
            if (field) field.value = previous;
            heldStatus = null;
            syncSaveActions();
            refreshDirty();
            showToast(err.message || "Publikování se nepodařilo.", true);
          });
      });
      main.querySelector("#cms-delete")?.addEventListener("click", async () => {
        if (!confirm("Opravdu smazat tento článek?")) return;
        isDirty = false;
        await api(`/api/admin/cms/${cmsId()}/delete`, { method: "POST" });
        history.pushState({}, "", "/admin/cms");
        await pageList();
      });
    }

    function previewHtml(a) {
      const stats = a.stats || {};
      const photo = a.photo || a.list_photo || "";
      const unpublished = a.status !== "published";
      const bodyBlocks = articleBlocks(a)
        .filter((item) => item.text)
        .map((item, index) => {
          if (item.type === "h") return `<h2 data-block-i="${index}" data-block-type="h" contenteditable="true">${esc(item.text)}</h2>`;
          if (item.type === "quote") return `<blockquote data-block-i="${index}" data-block-type="quote" contenteditable="true">${esc(item.text)}</blockquote>`;
          return `<p data-block-i="${index}" data-block-type="p" contenteditable="true">${esc(item.text)}</p>`;
        })
        .join("");
      const apt = APT.filter(([key]) => a[key])
        .map(
          ([key, label]) =>
            `<div><p>${esc(label)}</p><strong data-k="${key}" contenteditable="true">${esc(a[key])}</strong></div>`,
        )
        .join("");
      return `
        <div class="cms-edhead">
          <a class="cms-back" href="/admin/cms/${esc(a.id)}">${ico("arrow-left", 16)} Zpět do editoru</a>
          <div class="cms-prev-head">
            ${dirtyBar("Všechny změny jsou uložené")}
            <div class="cms-prev-actions">
              <a class="cms-btn" href="/admin/cms/${esc(a.id)}">Upravit článek</a>
              ${liveButton(a.slug, a.status)}
              ${
                unpublished
                  ? `<button class="cms-btn cms-btn-dark" type="button" id="cms-go-live">Publikovat ihned</button>`
                  : `<button class="cms-btn" type="button" id="cms-to-draft">Převest na koncept</button>`
              }
            </div>
          </div>
        </div>
        ${
          unpublished
            ? `<div class="cms-banner">${ico("alert-triangle", 20)} Toto je náhled článku — článek je uložen jako ${esc(statusLabel(a.status).toLowerCase())} a zatím není veřejně dostupný na webu REALITIFY.</div>`
            : `<div class="cms-banner is-live">Článek je veřejný na <a href="/uspechy/${esc(a.slug)}" target="_blank">/uspechy/${esc(a.slug)}</a>. Kliknutím do textu ho tu můžete upravit.</div>`
        }
        <article class="cms-preview" id="cms-preview" data-id="${esc(a.id)}">
          <div class="cms-phero">
            <div class="cms-pphoto">${photo ? `<img src="${esc(photo)}" alt="" />` : ""}</div>
            <div class="cms-pcopy">
              <div class="cms-pbadge">${esc(a.badge || "Příběh")}</div>
              <h1 data-k="title" contenteditable="true">${esc(a.title || "")}</h1>
              <p class="cms-plede" data-k="lede" contenteditable="true">${esc(a.lede || "")}</p>
              <strong data-k="author" contenteditable="true">${esc(a.author || "")}</strong>
              <span data-k="role" contenteditable="true">${esc(a.role || "")}</span>
            </div>
          </div>
          <div class="cms-pstats">
            ${STATS.map(
              ([key, label], i) =>
                `${i ? `<span class="cms-pdiv"></span>` : ""}<div><p>${esc(label)}</p><strong data-k="${key}" data-stat="1" contenteditable="true">${esc(stats[key] || "")}</strong></div>`,
            ).join("")}
          </div>
          <div class="cms-pbody">${bodyBlocks}
            ${apt ? `<div class="cms-papt"><p class="cms-papt-title">${esc(a.apt_title || "Nalezený byt v detailech")}</p><div class="cms-papt-grid">${apt}</div></div>` : ""}
          </div>
        </article>`;
    }

    async function pagePreview() {
      const id = cmsId();
      const article = await api(`/api/admin/cms/${id}`);
      main.innerHTML = previewHtml(article);
      const root = main.querySelector("#cms-preview");
      const collectPreview = () => {
        const payload = {};
        const stats = { ...(article.stats || {}) };
        root.querySelectorAll("[data-k]").forEach((el) => {
          const value = el.textContent.trim();
          if (el.dataset.stat === "1") stats[el.dataset.k] = value;
          else payload[el.dataset.k] = value;
        });
        payload.stats = stats;
        payload.blocks = [...root.querySelectorAll("[data-block-i]")].map((el) => ({
          type: el.dataset.blockType,
          text: el.textContent.trim(),
        }));
        return payload;
      };
      const savePreview = async () => {
        const payload = collectPreview();
        const saved = await api(`/api/admin/cms/${article.id}`, { method: "POST", body: JSON.stringify(payload) });
        Object.assign(article, saved, { stats: saved.stats || payload.stats });
        return saved;
      };
      bindUnsaved({
        snapshot: () => JSON.stringify(collectPreview()),
        save: savePreview,
        discard: async () => {
          isDirty = false;
          await pagePreview();
        },
      });
      root.addEventListener("input", refreshDirty);
      main.querySelector("#cms-go-live")?.addEventListener("click", async () => {
        try {
          if (isDirty) await savePreview();
          showToast("Stav změněn: Koncept → Publikováno. Článek je na webu.");
          await api(`/api/admin/cms/${article.id}`, { method: "POST", body: JSON.stringify({ status: "published" }) });
          isDirty = false;
          history.pushState({}, "", `/admin/cms/${article.id}/nahled`);
          await pagePreview();
        } catch (err) {
          showToast(err.message || "Publikování se nepodařilo.", true);
        }
      });
      main.querySelector("#cms-to-draft")?.addEventListener("click", async () => {
        try {
          if (isDirty) await savePreview();
          showToast("Stav změněn: Publikováno → Koncept. Článek už není veřejný.");
          await api(`/api/admin/cms/${article.id}`, { method: "POST", body: JSON.stringify({ status: "draft" }) });
          isDirty = false;
          await pagePreview();
        } catch (err) {
          showToast(err.message || "Převod na koncept se nepodařil.", true);
        }
      });
    }

    return { cms: pageList, "cms-edit": pageEdit, "cms-preview": pagePreview };
  };
})();
