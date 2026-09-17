(function () {
  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const fmtNum = (n) => Math.round(Number(n || 0)).toLocaleString("cs-CZ");
  const fmt = (n) => `${fmtNum(n)} Kč`;
  const digitsOnly = (raw) => String(raw || "").replace(/[^\d]/g, "").slice(0, 8);
  const groupDigits = (digits) => String(digits || "").replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const prettyGuess = (raw) => {
    const digits = digitsOnly(raw);
    return digits ? groupDigits(digits) : "";
  };
  const caretAfterDigits = (formatted, count) => {
    if (count <= 0) return 0;
    let seen = 0;
    for (let i = 0; i < formatted.length; i += 1) {
      if (/\d/.test(formatted[i])) {
        seen += 1;
        if (seen === count) return i + 1;
      }
    }
    return formatted.length;
  };
  const formatGuessInput = (el) => {
    if (!el) return;
    const raw = String(el.value || "");
    const caret = el.selectionStart ?? raw.length;
    const digitsBefore = digitsOnly(raw.slice(0, caret)).length;
    const next = prettyGuess(raw);
    el.value = next;
    const pos = caretAfterDigits(next, digitsBefore);
    try {
      el.setSelectionRange(pos, pos);
    } catch {
      /* input not text-like */
    }
  };

  const specText = (item) =>
    [item.disposition, item.area_m2 ? `${item.area_m2} m²` : ""].filter(Boolean).join(" · ");

  const ccyPill = () =>
    `<span class="ccy-pill"><span class="ccy-flag">CZ</span> Kč</span>`;

  const TYPICAL_VANISH = "v řádu hodin";
  const vanishText = (hours, fallback) => {
    if (fallback) return fallback;
    const value = Number(hours || 0);
    if (!value) return TYPICAL_VANISH;
    if (value < 1) return `za ${Math.max(8, Math.round(value * 60))} min`;
    if (value < 24) return `za ${String(value.toFixed(1)).replace(".", ",")} h`;
    return `za ${Math.round(value)} h`;
  };

  const infoRows = (rows) =>
    `<div class="info-rows">${rows
      .filter((row) => row && row[1])
      .map(
        ([label, value]) =>
          `<div class="info-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
      )
      .join("")}</div>`;

  const rowHtml = (item, { revealed = false, state = "", priority = false } = {}) => `
    <button type="button" class="flat-row${state ? ` ${state}` : ""}${revealed ? "" : " pickable"}" data-side="${escapeHtml(item.side || "")}" ${revealed ? "disabled" : 'tabindex="0"'}>
      <img class="flat-thumb" src="${escapeHtml(item.image_url || "/static/site/assets/sold-1.webp")}" width="96" height="96" alt="" decoding="async"${priority ? ' fetchpriority="high"' : ""} />
      <div class="flat-meta">
        <div class="spec">${escapeHtml(specText(item))}</div>
        <div class="portal">${escapeHtml(item.portal_label || "")}</div>
      </div>
      <div class="amount-stack">
        <div class="flat-price${revealed ? "" : " is-hidden"}">${revealed ? `${fmtNum(item.price_czk)}` : "— — —"}</div>
        ${ccyPill()}
      </div>
    </button>
  `;

  async function loadHigherLower() {
    const board = document.getElementById("game-board");
    const scoreEl = document.getElementById("game-score");
    if (!board) return;
    let streak = 0;
    let played = 0;

    const renderScore = () => {
      if (!scoreEl) return;
      scoreEl.innerHTML = `
        <span class="score-chip">Série <strong>${streak}</strong></span>
        <span class="score-chip">Odehráno <strong>${played}</strong></span>
      `;
    };

    async function round() {
      board.setAttribute("aria-busy", "true");
      const res = await fetch("/api/public/games/higher-lower");
      const data = await res.json();
      const left = { ...data.left, side: "left" };
      const right = { ...data.right, side: "right" };
      const locality = data.locality_label || left.locality || right.locality || "Stejná lokalita";
      const vanish = vanishText(data.vanish_hours, data.vanish_label);
      board.removeAttribute("aria-busy");
      board.innerHTML = `
        <div class="loc-chip">${escapeHtml(locality)}</div>
        ${rowHtml(left, { priority: true })}
        <div class="vs-row">VS</div>
        ${rowHtml(right)}
        ${infoRows([
          ["Stejná lokalita", locality],
          ["Takové nabídky mizí", vanish],
        ])}
        <div class="mint-banner">
          <strong>${escapeHtml(data.copy || "Oba byty jsou ve stejné lokalitě.")}</strong>
        </div>
      `;
      renderScore();
      let locked = false;
      board.querySelectorAll(".pickable").forEach((el) => {
        const pick = () => {
          if (locked) return;
          locked = true;
          const side = el.dataset.side;
          const correct = side === data.cheaper;
          played += 1;
          streak = correct ? streak + 1 : 0;
          const message = correct ? data.copy_ok : data.copy_miss;
          board.innerHTML = `
            <div class="loc-chip">${escapeHtml(locality)}</div>
            ${rowHtml(left, { revealed: true, state: data.cheaper === "left" ? "win" : "lose" })}
            <div class="vs-row">VS</div>
            ${rowHtml(right, { revealed: true, state: data.cheaper === "right" ? "win" : "lose" })}
            ${infoRows([
              ["Stejná lokalita", locality],
              ["Takové nabídky mizí", vanish],
            ])}
            <div class="mint-banner">
              <strong>${correct ? "Správně." : "Špatně."}</strong>
              <span>${escapeHtml(message || "Dobré byty mizí rychle.")}</span>
            </div>
            <div class="converter-actions">
              <button class="pill pill-lg" type="button" id="hl-next">Další dvojice</button>
              <a class="pill-ghost" href="/registrace">Hlídat podobné byty</a>
            </div>
          `;
          renderScore();
          document.getElementById("hl-next")?.addEventListener("click", round);
        };
        el.addEventListener("click", pick);
        el.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            pick();
          }
        });
      });
    }
    round().catch(() => {
      board.innerHTML = "<div class='mint-banner'><strong>Hru se teď nepodařilo načíst. Zkuste to za chvíli.</strong></div>";
    });
  }

  async function loadRent() {
    const board = document.getElementById("rent-board");
    if (!board) return;
    const scoreEl = document.getElementById("rent-score");
    const stepsEl = document.getElementById("rent-steps");
    const nameInput = document.getElementById("player-name");
    let items = [];
    let index = 0;
    let roundMeta = {};
    const guesses = [];

    const renderScore = () => {
      const total = items.length || 5;
      const shown = Math.min(index + 1, total);
      if (scoreEl) {
        scoreEl.innerHTML = `
          <span class="score-chip">Byt <strong>${shown} / ${total}</strong></span>
          <span class="score-chip">Tipy <strong>${guesses.length}</strong></span>
        `;
      }
      if (stepsEl) {
        stepsEl.innerHTML = Array.from({ length: total }, (_, i) => {
          const state = i < index ? "is-done" : i === index ? "is-on" : "";
          return `<span class="step-dot ${state}"></span>`;
        }).join("");
      }
    };

    const fail = (msg) => {
      board.innerHTML = `<div class="mint-banner"><strong>${escapeHtml(msg)}</strong></div>`;
    };

    const showResult = (scored) => {
      index = items.length;
      renderScore();
      const rows = (scored.items || [])
        .map((item) => {
          const low = Number(item.points || 0) < 400;
          return `
            <div class="rent-result-row">
              <div>
                <div class="spec">${escapeHtml(item.locality || item.name || "")} · ${escapeHtml(specText(item))}</div>
                <div class="portal">Tip ${escapeHtml(fmt(item.guess))} · odchylka ${String(item.error_pct).replace(".", ",")} %</div>
              </div>
              <div class="amount-stack">
                <div class="flat-price">${escapeHtml(fmtNum(item.actual))}</div>
                ${ccyPill()}
                <span class="rent-pts${low ? " is-low" : ""}">${escapeHtml(item.points)} b</span>
              </div>
            </div>
          `;
        })
        .join("");
      const vanishRows = (scored.items || []).map((item) => [
        Number(item.vanish_hours) || 8,
        item.vanish_label || "",
      ]);
      const observed = vanishRows.filter(([, label]) => label && label !== TYPICAL_VANISH);
      const fastest = (observed.length ? observed : vanishRows).reduce(
        (best, row) => (!best || row[0] < best[0] ? row : best),
        null,
      );
      const locality = roundMeta.locality_label || "Stejná lokalita";
      const vanish = vanishText(fastest?.[0], fastest?.[1] || roundMeta.vanish_label);
      board.innerHTML = `
        <div class="loc-chip">${escapeHtml(locality)}</div>
        <div class="mint-banner">
          <strong>Skóre ${escapeHtml(scored.score)} / ${escapeHtml(scored.max_score)}</strong>
          <span>Přesnost ${String(scored.accuracy).replace(".", ",")} %. ${escapeHtml(roundMeta.copy_ok || `Nejrychlejší z těchto pěti mizí ${vanish}`)} — žebříček uvidí jen admin.</span>
        </div>
        ${rows}
        <div class="converter-actions">
          <button class="pill pill-lg" type="button" id="rent-again">Další kolo</button>
          <a class="pill-ghost" href="/registrace">Hlídat podobné byty</a>
        </div>
      `;
      document.getElementById("rent-again")?.addEventListener("click", () => {
        guesses.length = 0;
        index = 0;
        start().catch(() => fail("Hru se teď nepodařilo načíst. Zkuste to za chvíli."));
      });
    };

    const submitRound = async () => {
      board.setAttribute("aria-busy", "true");
      board.innerHTML = `<div class="mint-banner"><strong>Počítám skóre…</strong><span>Kč / měsíc — body za přesnost.</span></div>`;
      try {
        const scored = await fetch("/api/public/games/rent-score", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: nameInput?.value || "", guesses }),
        }).then((row) => row.json());
        if (!scored || scored.score == null) {
          fail("Skóre se nepodařilo uložit. Zkuste to znovu.");
          return;
        }
        board.removeAttribute("aria-busy");
        showResult(scored);
      } catch {
        board.removeAttribute("aria-busy");
        fail("Skóre se nepodařilo uložit. Zkuste to znovu.");
      }
    };

    const showItem = () => {
      const item = items[index];
      if (!item) {
        submitRound();
        return;
      }
      renderScore();
      board.removeAttribute("aria-busy");
      const locality = roundMeta.locality_label || item.locality || item.name || "Stejná lokalita";
      const vanish = vanishText(item.vanish_hours, item.vanish_label || roundMeta.vanish_label);
      const hint = roundMeta.copy_hint || "např. 18 000 · jen čísla, mezery doplníme";
      board.innerHTML = `
        <div class="loc-chip">${escapeHtml(locality)}</div>
        <img class="rent-hero-img" src="${escapeHtml(item.image_url || "/static/site/assets/sold-1.webp")}" width="390" height="180" alt="" decoding="async" fetchpriority="high" />
        <div class="flat-meta" style="margin-bottom:16px">
          <div class="spec">${escapeHtml(specText(item))}</div>
          <div class="portal">${escapeHtml(item.portal_label || "")}</div>
        </div>
        <label class="rent-amount">
          <input id="rent-guess" type="text" inputmode="numeric" autocomplete="off" placeholder="18 000" autofocus size="8" aria-describedby="rent-guess-hint" />
          ${ccyPill()}
        </label>
        <p class="rent-unit">Kč / měsíc</p>
        <p class="rent-hint" id="rent-guess-hint">${escapeHtml(hint)}</p>
        ${infoRows([
          ["Stejná lokalita", locality],
          ["Portál", item.portal_label || ""],
          ["Takové nabídky mizí", vanish],
        ])}
        <div class="mint-banner">
          <strong>Kolik stojí měsíc v Kč?</strong>
          <span>${escapeHtml(roundMeta.copy || `Pět bytů ze stejné čtvrti. Čím blíž, tím víc bodů. Dobré nabídky mizí ${vanish}.`)}</span>
        </div>
        <div class="converter-actions">
          <button class="pill pill-lg" type="button" id="rent-next">${index + 1 >= items.length ? "Odeslat tipy" : "Další byt"}</button>
        </div>
      `;
      const input = document.getElementById("rent-guess");
      const next = document.getElementById("rent-next");
      const parseGuess = (raw) => Number(digitsOnly(raw) || 0);
      input?.addEventListener("input", () => formatGuessInput(input));
      const advance = () => {
        const value = parseGuess(input?.value);
        if (!value || value < 1000) {
          input?.focus();
          input?.setCustomValidity("Zadejte nájem aspoň 1 000 Kč.");
          input?.reportValidity?.();
          input?.setCustomValidity("");
          return;
        }
        guesses.push({ id: item.id, guess: value });
        index += 1;
        if (index >= items.length) {
          submitRound();
          return;
        }
        showItem();
      };
      next?.addEventListener("click", advance);
      input?.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          advance();
        }
      });
      input?.focus();
    };

    async function start() {
      board.setAttribute("aria-busy", "true");
      const res = await fetch("/api/public/games/rent-round");
      const data = await res.json();
      items = data.items || [];
      roundMeta = data;
      if (items.length < 1) {
        fail("Hru se teď nepodařilo načíst. Zkuste to za chvíli.");
        return;
      }
      index = 0;
      showItem();
    }

    start().catch(() => fail("Hru se teď nepodařilo načíst. Zkuste to za chvíli."));
  }

  if (document.body.classList.contains("game-higher")) loadHigherLower();
  if (document.body.classList.contains("game-rent")) loadRent();
})();
