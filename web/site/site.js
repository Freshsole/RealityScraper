document.querySelectorAll(".faq-item").forEach((item, index) => {
  if (index === 0) item.classList.add("open");
  const trigger = item.querySelector(".faq-q");
  trigger.addEventListener("click", () => {
    const open = item.classList.contains("open");
    document.querySelectorAll(".faq-item").forEach((other) => other.classList.remove("open"));
    if (!open) item.classList.add("open");
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
            <img src="${escapeHtml(item.image || "/static/site/assets/sold-1.png")}" width="300" height="220" alt="${escapeHtml(item.locality || "")}" />
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
