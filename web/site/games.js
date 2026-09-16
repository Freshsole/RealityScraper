(function () {
  const fmt = (n) => {
    const num = Number(n || 0);
    return `${Math.round(num).toLocaleString("cs-CZ")} Kč`;
  };

  const vanishText = (hours) => {
    const h = Number(hours || 0);
    if (h < 1) return `za ${Math.max(8, Math.round(h * 60))} minut`;
    if (h < 24) return `za ${h.toFixed(1).replace(".", ",")} h`;
    return `za ${Math.round(h)} h`;
  };

  const cardHtml = (item, { pickable = false, showPrice = false } = {}) => `
    <article class="flat-card${pickable ? " pickable" : ""}" data-side="${item.side || ""}" ${pickable ? 'tabindex="0" role="button"' : ""}>
      <img src="${item.image_url || "/static/site/assets/hero-apart.png"}" alt="" />
      <div class="meta">
        <div class="loc">${item.locality || item.name || ""}</div>
        <div class="spec">${[item.disposition, item.area_m2 ? `${item.area_m2} m²` : ""].filter(Boolean).join(" • ")}</div>
        <div class="portal">${item.portal_label || ""}</div>
        ${showPrice ? `<div class="price">${fmt(item.price_czk)} / měsíc</div>` : ""}
      </div>
    </article>
  `;

  async function loadHigherLower() {
    const board = document.getElementById("game-board");
    if (!board) return;
    const status = document.getElementById("game-status");
    let streak = 0;
    let played = 0;

    async function round() {
      const res = await fetch("/api/public/games/higher-lower");
      const data = await res.json();
      const left = { ...data.left, side: "left" };
      const right = { ...data.right, side: "right" };
      board.innerHTML = `${cardHtml(left, { pickable: true }) }<div class="vs-mark">VS</div>${cardHtml(right, { pickable: true })}`;
      status.innerHTML = `<p class="fomo">${data.copy || "Dobré byty mizí rychle."}</p><div class="game-scoreline"><span>Série: ${streak}</span><span>Odehráno: ${played}</span></div>`;
      let locked = false;
      board.querySelectorAll(".pickable").forEach((el) => {
        const pick = () => {
          if (locked) return;
          locked = true;
          const side = el.dataset.side;
          const correct = side === data.cheaper;
          played += 1;
          streak = correct ? streak + 1 : 0;
          board.querySelectorAll(".flat-card").forEach((card) => {
            card.classList.remove("pickable");
            card.removeAttribute("tabindex");
            const win = card.dataset.side === data.cheaper;
            card.classList.add(win ? "win" : "lose");
            const price = card.dataset.side === "left" ? left.price_czk : right.price_czk;
            const meta = card.querySelector(".meta");
            if (meta && !meta.querySelector(".price")) {
              const p = document.createElement("div");
              p.className = "price";
              p.textContent = `${fmt(price)} / měsíc`;
              meta.appendChild(p);
            }
          });
          status.innerHTML = `
            <p class="fomo">${correct ? "Správně — ale na trhu byste měli jen vteřiny." : "Špatně. Levnější byt už je často pryč."} Podobné nabídky mizí ${vanishText(data.vanish_hours)}.</p>
            <div class="game-scoreline"><span>Série: ${streak}</span><span>Odehráno: ${played}</span></div>
            <button class="pill pill-lg" type="button" id="hl-next">Další dvojice</button>
          `;
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
      status.innerHTML = "<p class='fomo'>Hru se teď nepodařilo načíst. Zkuste to za chvíli.</p>";
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
          <img src="${item.image_url || "/static/site/assets/hero-apart.png"}" alt="" />
          <div class="meta">
            <div class="loc">${item.locality || item.name || ""}</div>
            <div class="spec">${[item.disposition, item.area_m2 ? `${item.area_m2} m²` : ""].filter(Boolean).join(" • ")}</div>
            <div class="portal">${item.portal_label || ""}</div>
            <label>Tip nájmu / měsíc
              <input type="number" min="1000" step="100" name="guess" data-id="${item.id}" required />
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
          <h2 class="display" style="font-size:40px;line-height:34px">SKÓRE ${scored.score} / ${scored.max_score}</h2>
          <p>Přesnost ${String(scored.accuracy).replace(".", ",")} %. Skvělé byty mizí dřív, než stihnete srovnat 5 inzerátů ručně.</p>
          <div class="rent-grid" style="margin-top:16px">
            ${(scored.items || [])
              .map(
                (item) => `
                <article class="rent-card">
                  <div class="meta">
                    <div class="loc">${item.locality || item.name || ""}</div>
                    <div class="price">${fmt(item.actual)}</div>
                    <div class="spec">Tip ${fmt(item.guess)} · ${item.points} b · odchylka ${String(item.error_pct).replace(".", ",")} %</div>
                  </div>
                </article>
              `,
              )
              .join("")}
          </div>
        `;
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }

  if (document.body.classList.contains("game-higher")) loadHigherLower();
  if (document.body.classList.contains("game-rent")) loadRent();
})();
