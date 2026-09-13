document.querySelectorAll(".faq-section .faq-item").forEach((item, index) => {
  const trigger = item.querySelector(".faq-q");
  const answer = item.querySelector(".faq-a");
  if (!trigger || !answer) return;

  const panel = document.createElement("div");
  panel.className = "faq-panel";
  const inner = document.createElement("div");
  inner.className = "faq-panel-inner";
  answer.replaceWith(panel);
  panel.appendChild(inner);
  inner.appendChild(answer);

  const open = index === 0 || item.classList.contains("open");
  item.classList.toggle("open", open);
  trigger.setAttribute("aria-expanded", open ? "true" : "false");

  trigger.addEventListener("click", () => {
    const willOpen = !item.classList.contains("open");
    document.querySelectorAll(".faq-section .faq-item").forEach((other) => {
      other.classList.toggle("open", other === item && willOpen);
      other.querySelector(".faq-q")?.setAttribute("aria-expanded", other === item && willOpen ? "true" : "false");
    });
  });
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function renderGoneFast() {
  const host = document.getElementById("sold-cards");
  if (!host) return;
  try {
    const response = await fetch("/api/public/gone-fast");
    const data = await response.json().catch(() => ({}));
    const items = data.items || [];
    if (!items.length) return;
    host.innerHTML = items
      .map(
        (item) => `<article class="sold-card">
          <div class="sold-img">
            <img src="${escapeHtml(item.image || "/static/site/assets/sold-1.webp")}" width="300" height="220" alt="${escapeHtml(item.locality || "")}" loading="lazy" decoding="async" />
            <span class="sold-badge">${escapeHtml(item.badge || "PRONAJATO")}</span>
          </div>
          <div class="sold-meta">
            <div class="sold-top"><span>${escapeHtml(item.locality || "")}</span><span>${escapeHtml(item.price || "")}</span></div>
            <p class="spec">${escapeHtml(item.spec || "")}</p>
            <p class="when">${escapeHtml(item.when || "")}</p>
          </div>
        </article>`
      )
      .join("");
  } catch {
    /* keep placeholder cards */
  }
}

renderGoneFast();

async function renderNewToday() {
  const el = document.getElementById("urg-new-today");
  if (!el) return;
  try {
    const response = await fetch("/api/public/stats");
    const data = await response.json().catch(() => ({}));
    if (!Number.isFinite(data.new_today)) return;
    el.textContent = `${Number(data.new_today).toLocaleString("cs-CZ")} `;
  } catch {
    /* keep placeholder */
  }
}

renderNewToday();
