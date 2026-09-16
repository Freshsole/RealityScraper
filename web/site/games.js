(function () {
  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const fmt = (n) => {
    const num = Number(n || 0);
    return `${Math.round(num).toLocaleString("cs-CZ")} Kč`;
  };

  const specText = (item) =>
    [item.disposition, item.area_m2 ? `${item.area_m2} m²` : ""].filter(Boolean).join(" · ");

  const rowHtml = (item, { revealed = false, state = "" } = {}) => `
    <button type="button" class="flat-row${state ? ` ${state}` : ""}${revealed ? "" : " pickable"}" data-side="${escapeHtml(item.side || "")}" ${revealed ? "disabled" : 'tabindex="0"'}>
      <img class="flat-thumb" src="${escapeHtml(item.image_url || "/static/site/assets/sold-1.webp")}" width="96" height="96" alt="" decoding="async" />
      <div class="flat-meta">
        <div class="spec">${escapeHtml(specText(item))}</div>
        <div class="portal">${escapeHtml(item.portal_label || "")}</div>
      </div>
      <div class="flat-price${revealed ? "" : " is-hidden"}">${revealed ? `${fmt(item.price_czk)}` : "???"}</div>
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
      board.innerHTML = `<p class="lead">Načítám dvojici ze stejné lokality…</p>`;
      const res = await fetch("/api/public/games/higher-lower");
      const data = await res.json();
      const left = { ...data.left, side: "left" };
      const right = { ...data.right, side: "right" };
      const locality = data.locality_label || left.locality || right.locality || "Stejná lokalita";
      board.innerHTML = `
        <div class="loc-chip">${escapeHtml(locality)}</div>
        ${rowHtml(left)}
        <div class="vs-row">VS</div>
        ${rowHtml(right)}
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
            <div class="mint-banner">
              <strong>${correct ? "Správně." : "Špatně."}</strong>
              <span>${escapeHtml(message || "Dobré byty mizí rychle.")}</span>
            </div>
            <div class="converter-actions">
              <button class="pill pill-lg" type="button" id="hl-next">Další dvojice</button>
              <a class="pill pill-lg" href="/registrace" style="text-align:center">Hlídat podobné byty</a>
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
              <div class="flat-price">${escapeHtml(fmt(item.actual))}<span class="rent-pts${low ? " is-low" : ""}">${escapeHtml(item.points)} b</span></div>
            </div>
          `;
        })
        .join("");
      board.innerHTML = `
        <div class="loc-chip">Žebříček u admina</div>
        <div class="mint-banner">
          <strong>Skóre ${escapeHtml(scored.score)} / ${escapeHtml(scored.max_score)}</strong>
          <span>Přesnost ${String(scored.accuracy).replace(".", ",")} %. Skvělé byty mizí dřív, než stihnete srovnat pět inzerátů ručně.</span>
        </div>
        ${rows}
        <div class="converter-actions">
          <button class="pill pill-lg" type="button" id="rent-again">Další kolo</button>
          <a class="pill pill-lg" href="/registrace" style="text-align:center">Hlídat podobné byty</a>
        </div>
      `;
      document.getElementById("rent-again")?.addEventListener("click", () => {
        guesses.length = 0;
        index = 0;
        start().catch(() => fail("Hru se teď nepodařilo načíst. Zkuste to za chvíli."));
      });
    };

    const submitRound = async () => {
      board.innerHTML = `<p class="lead">Počítám skóre…</p>`;
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
        showResult(scored);
      } catch {
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
      board.innerHTML = `
        <div class="loc-chip">${escapeHtml(item.locality || item.name || "Byt")}</div>
        <img class="rent-hero-img" src="${escapeHtml(item.image_url || "/static/site/assets/sold-1.webp")}" width="504" height="180" alt="" decoding="async" />
        <div class="flat-meta" style="margin-bottom:16px">
          <div class="spec">${escapeHtml(specText(item))}</div>
          <div class="portal">${escapeHtml(item.portal_label || "")}</div>
        </div>
        <label class="rent-amount">
          <input id="rent-guess" type="number" min="1000" step="100" inputmode="numeric" placeholder="18000" required autofocus />
          <span class="unit">Kč / měsíc</span>
        </label>
        <div class="mint-banner">
          <strong>Tipněte měsíční nájem.</strong>
          <span>Enter nebo tlačítko — další byt za vteřinu.</span>
        </div>
        <div class="converter-actions">
          <button class="pill pill-lg" type="button" id="rent-next">${index + 1 >= items.length ? "Odeslat tipy" : "Další byt"}</button>
        </div>
      `;
      const input = document.getElementById("rent-guess");
      const next = document.getElementById("rent-next");
      const advance = () => {
        const value = Number(input?.value || 0);
        if (!value || value < 1000) {
          input?.focus();
          input?.reportValidity?.();
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
      board.innerHTML = `<p class="lead">Načítám pět nabídek…</p>`;
      const res = await fetch("/api/public/games/rent-round");
      const data = await res.json();
      items = data.items || [];
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
