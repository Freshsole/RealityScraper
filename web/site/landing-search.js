(() => {
  const locality = document.getElementById("db-locality");
  const suggest = document.getElementById("db-locality-suggest");
  const dispBtn = document.getElementById("db-disp-btn");
  const dispMenu = document.getElementById("db-disp-menu");
  const price = document.getElementById("db-price");
  const searchBtn = document.getElementById("db-search");
  const results = document.getElementById("db-results");
  const stage = document.querySelector(".db-stage");
  const gate = document.getElementById("db-gate");
  const modal = document.getElementById("landing-modal");
  const modalBody = document.getElementById("landing-modal-body");
  if (!searchBtn || !results) return;

  const DISPS = ["1+kk", "1+1", "2+kk", "2+1", "3+kk", "3+1", "4+kk"];
  let selectedDisp = new Set(["2+kk", "3+kk"]);
  let suggestItems = [];
  let suggestTimer = 0;
  let locked = false;

  const lockSearch = () => {
    locked = true;
    stage?.classList.add("is-locked");
    if (gate) gate.hidden = false;
    locality && (locality.disabled = true);
    price && (price.disabled = true);
    dispBtn && (dispBtn.disabled = true);
    searchBtn.disabled = true;
    hideSuggest();
    if (dispMenu) dispMenu.hidden = true;
  };

  const guestUsed = (data) => Boolean(data?.need_register || data?.used || (data && data.ok === false && !data.logged_in));

  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const dispLabel = () => {
    const items = DISPS.filter((item) => selectedDisp.has(item));
    return items.length ? items.join(", ") : "Dispozice";
  };

  const renderDisp = () => {
    if (dispBtn) dispBtn.querySelector("span").textContent = dispLabel();
    if (!dispMenu) return;
    dispMenu.innerHTML = DISPS.map(
      (item) =>
        `<button type="button" class="${selectedDisp.has(item) ? "on" : ""}" data-disp="${escapeHtml(item)}">${escapeHtml(item)}</button>`,
    ).join("");
  };

  const hideSuggest = () => {
    if (!suggest) return;
    suggest.hidden = true;
    suggest.innerHTML = "";
  };

  const searchUrl = () => {
    const params = new URLSearchParams();
    const city = (locality?.value || "Brno").trim();
    const cityName = city.split("-")[0].trim() || "Brno";
    params.set("district", cityName);
    params.set("q", cityName);
    const disps = DISPS.filter((item) => selectedDisp.has(item));
    if (disps.length) params.set("disposition", disps.join(","));
    const max = String(price?.value || "").replace(/\s/g, "");
    if (max) params.set("price_to", max);
    params.set("offer", "pronajem");
    params.set("estate", "byt");
    return `/nabidka?${params.toString()}`;
  };

  const cardHtml = (item) => `
    <article class="mock-card" data-monitor="${escapeHtml(item.monitor_id || "")}" data-id="${escapeHtml(item.id ?? "")}" data-key="${escapeHtml(item.listing_key || "")}" data-url="${escapeHtml(item.url || "")}">
      <div class="thumb"><img src="${escapeHtml(item.image || "/static/site/assets/db-1.webp")}" width="380" height="150" alt="" loading="lazy" decoding="async" /></div>
      <div class="mock-body">
        <div class="mock-top"><span class="source">${escapeHtml(item.portal || "")}</span><strong>${escapeHtml(item.price || "")}</strong></div>
        <div>
          <h3>${escapeHtml(item.name || "Byt")}</h3>
          <p>${escapeHtml(item.locality || "")}</p>
        </div>
      </div>
    </article>`;

  async function loadListings() {
    try {
      const response = await fetch("/api/public/landing-listings");
      const data = await response.json().catch(() => ({}));
      const items = data.items || [];
      if (!items.length) return;
      results.innerHTML = items.map(cardHtml).join("");
    } catch {
      /* keep placeholders */
    }
  }

  async function openModal(card) {
    if (!modal || !modalBody) return;
    modal.hidden = false;
    document.body.classList.add("landing-modal-open");
    modalBody.innerHTML = `<p class="meta">Načítám nabídku…</p>`;
    const params = new URLSearchParams();
    if (card.dataset.monitor) params.set("monitor_id", card.dataset.monitor);
    if (card.dataset.id) params.set("id", card.dataset.id);
    if (card.dataset.key) params.set("listing_key", card.dataset.key);
    if (card.dataset.url) params.set("url", card.dataset.url);
    try {
      const response = await fetch(`/api/catalog/item?${params}`);
      const item = await response.json().catch(() => ({}));
      if (!response.ok) {
        modalBody.innerHTML = `<p class="meta">${escapeHtml(item.detail || "Nabídku se nepodařilo načíst.")}</p>`;
        return;
      }
      const photo = (item.photos && item.photos[0]) || item.image_url || "";
      const price = item.price_label || (item.price_czk != null ? `${Number(item.price_czk).toLocaleString("cs-CZ")} Kč` : "");
      const loc = item.extras?.address || item.locality || "";
      const desc = item.description ? `<p class="meta">${escapeHtml(String(item.description).slice(0, 420))}</p>` : "";
      modalBody.innerHTML = `
        ${photo ? `<img src="${escapeHtml(photo)}" alt="" />` : ""}
        <p class="meta">${escapeHtml(item.portal || "")} · ${escapeHtml(price)}</p>
        <h3>${escapeHtml(item.name || loc || "Nabídka")}</h3>
        <p class="meta">${escapeHtml([item.disposition, item.area_m2 ? `${item.area_m2} m²` : "", loc].filter(Boolean).join(" · "))}</p>
        ${desc}
        ${item.url ? `<a class="pill pill-lg" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">Otevřít na portálu</a>` : ""}
      `;
    } catch {
      modalBody.innerHTML = `<p class="meta">Nabídku se nepodařilo načíst.</p>`;
    }
  }

  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("landing-modal-open");
  }

  dispMenu?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-disp]");
    if (!button) return;
    const value = button.dataset.disp;
    if (selectedDisp.has(value)) selectedDisp.delete(value);
    else selectedDisp.add(value);
    renderDisp();
  });

  dispBtn?.addEventListener("click", () => {
    if (!dispMenu) return;
    dispMenu.hidden = !dispMenu.hidden;
    hideSuggest();
  });

  locality?.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    const q = locality.value.trim();
    if (q.length < 2) {
      hideSuggest();
      return;
    }
    suggestTimer = window.setTimeout(async () => {
      const response = await fetch(`/api/filters/locality?q=${encodeURIComponent(q)}`);
      const data = await response.json().catch(() => ({ items: [] }));
      suggestItems = data.items || [];
      if (!suggestItems.length) {
        hideSuggest();
        return;
      }
      suggest.hidden = false;
      suggest.innerHTML = suggestItems
        .map((item, index) => `<button type="button" data-suggest="${index}">${escapeHtml(item.label)}</button>`)
        .join("");
    }, 180);
  });

  suggest?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-suggest]");
    if (!button) return;
    const item = suggestItems[Number(button.dataset.suggest)];
    if (item?.label) locality.value = item.label;
    hideSuggest();
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".filter-field")) {
      hideSuggest();
      if (dispMenu) dispMenu.hidden = true;
    }
  });

  results.addEventListener("click", (event) => {
    if (locked) {
      if (gate) gate.hidden = false;
      return;
    }
    const card = event.target.closest(".mock-card");
    if (card) openModal(card);
  });

  document.getElementById("landing-modal-close")?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  document.getElementById("db-gate-pricing")?.addEventListener("click", (event) => {
    event.stopPropagation();
    if (gate) gate.hidden = true;
  });

  stage?.addEventListener("click", (event) => {
    if (!locked || event.target.closest(".db-gate-card")) return;
    if (gate?.hidden) gate.hidden = false;
  });

  searchBtn.addEventListener("click", async (event) => {
    event.preventDefault();
    if (locked) {
      if (gate) gate.hidden = false;
      return;
    }
    const target = searchUrl();
    try {
      const response = await fetch("/api/public/guest-search", { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (guestUsed(data)) {
        lockSearch();
        return;
      }
      location.href = target;
    } catch {
      location.href = target;
    }
  });

  renderDisp();
  loadListings();
  fetch("/api/public/guest-search")
    .then((res) => res.json())
    .then((data) => {
      if (!data.logged_in && guestUsed(data)) lockSearch();
    })
    .catch(() => {});
})();
