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
    const grid = document.getElementById("rent-grid");
    if (!grid) return;
    const form = document.getElementById("rent-form");
    const result = document.getElementById("rent-result");
    const res = await fetch("/api/public/games/rent-round");
    const data = await res.json();
    grid.innerHTML = (data.items || [])
      .map(
        (item) => `
        <article class="rent-card">
          <img src="${escapeHtml(item.image_url || "/static/site/assets/sold-1.webp")}" alt="" width="300" height="140" decoding="async" />
          <div class="meta">
            <div class="loc">${escapeHtml(item.locality || item.name || "")}</div>
            <div class="spec">${escapeHtml(specText(item))}</div>
            <div class="portal">${escapeHtml(item.portal_label || "")}</div>
            <label>Tip nájmu / měsíc
              <input type="number" min="1000" step="100" name="guess" data-id="${escapeHtml(item.id)}" required />
            </label>
          </div>
        </article>
      `,
      )
      .join("");
    form?.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const guesses = [...grid.querySelectorAll("input[name='guess']")].map((input) => ({
        id: input.dataset.id,
        guess: Number(input.value || 0),
      }));
      const name = document.getElementById("player-name")?.value || "";
      const submit = form.querySelector("button[type='submit']");
      if (submit) submit.disabled = true;
      try {
        const scored = await fetch("/api/public/games/rent-score", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, guesses }),
        }).then((row) => row.json());
        result.hidden = false;
        result.innerHTML = `
          <h2 class="display">SKÓRE ${escapeHtml(scored.score)} / ${escapeHtml(scored.max_score)}</h2>
          <p>Přesnost ${String(scored.accuracy).replace(".", ",")} %. Skvělé byty mizí dřív, než stihnete srovnat 5 inzerátů ručně.</p>
          <div class="rent-grid" style="margin-top:16px">
            ${(scored.items || [])
              .map(
                (item) => `
                <article class="rent-card">
                  <div class="meta">
                    <div class="loc">${escapeHtml(item.locality || item.name || "")}</div>
                    <div class="flat-price">${escapeHtml(fmt(item.actual))}</div>
                    <div class="spec">Tip ${escapeHtml(fmt(item.guess))} · ${escapeHtml(item.points)} b · odchylka ${String(item.error_pct).replace(".", ",")} %</div>
                  </div>
                </article>
              `,
              )
              .join("")}
          </div>
          <a class="pill" href="/registrace">Hlídat podobné byty</a>
        `;
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }

  if (document.body.classList.contains("game-higher")) loadHigherLower();
  if (document.body.classList.contains("game-rent")) loadRent();
})();
