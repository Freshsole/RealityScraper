(() => {
  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  function slugFromPath() {
    const parts = location.pathname.replace(/\/+$/, "").split("/").filter(Boolean);
    if (parts[0] === "uspechy" && parts[1]) return parts[1];
    return "";
  }

  function renderStats(stats) {
    const line =
      '<span class="article-stat-div" aria-hidden="true"><img src="/static/site/assets/stories/article-line.svg" width="48" height="1" alt="" /></span>';
    return (stats || [])
      .map(
        ([label, value], index) =>
          `${index ? line : ""}<div class="article-stat"><p>${escapeHtml(label)}</p><strong>${escapeHtml(value)}</strong></div>`,
      )
      .join("");
  }

  function renderBody(blocks) {
    return (blocks || [])
      .map((block) => {
        if (block.h) return `<h2>${escapeHtml(block.h)}</h2>`;
        if (block.p) return `<p>${escapeHtml(block.p)}</p>`;
        if (block.quote) return `<blockquote class="article-pull">${escapeHtml(block.quote)}</blockquote>`;
        if (block.apt) {
          const rows = block.apt.rows
            .map(([label, value]) => `<div><p>${escapeHtml(label)}</p><strong>${escapeHtml(value)}</strong></div>`)
            .join("");
          return `<div class="apt-box"><p class="apt-title">${escapeHtml(block.apt.title)}</p><div class="apt-grid">${rows}</div></div>`;
        }
        return "";
      })
      .join("");
  }

  function render(article) {
    document.title = article.documentTitle;
    document.getElementById("crumb-current").textContent = article.crumb;
    document.getElementById("article-badge").textContent = article.badge;
    document.getElementById("article-title").textContent = article.title;
    document.getElementById("article-lede").textContent = article.lede;
    document.getElementById("article-author").textContent = article.author;
    document.getElementById("article-role").textContent = article.role;
    const photo = document.getElementById("article-photo");
    photo.src = article.photo;
    photo.alt = article.crumb;
    photo.style.objectPosition = article.photoPos || "50% 50%";
    document.getElementById("article-stats").innerHTML = renderStats(article.stats);
    document.getElementById("article-body").innerHTML = renderBody(article.body);
  }

  const slug = slugFromPath();
  fetch(`/api/stories/${encodeURIComponent(slug)}?view=1`)
    .then((res) => {
      if (!res.ok) throw new Error("missing");
      return res.json();
    })
    .then((article) => {
      try {
        render(article);
      } finally {
        document.documentElement.classList.remove("article-pending");
      }
    })
    .catch(() => {
      location.replace("/uspechy");
    });

  const popupKey = "rf_article_popup";
  const popup = document.getElementById("article-popup");
  const popupForm = document.getElementById("article-popup-form");

  function closePopup() {
    if (!popup) return;
    popup.hidden = true;
    document.body.classList.remove("article-popup-open");
    sessionStorage.setItem(popupKey, "1");
  }

  function openPopup() {
    if (!popup || sessionStorage.getItem(popupKey)) return;
    popup.hidden = false;
    document.body.classList.add("article-popup-open");
    popup.querySelector("[data-popup-close]")?.focus();
  }

  if (popup && !sessionStorage.getItem(popupKey)) {
    let armed = false;
    let lastY = window.scrollY;
    const onScroll = () => {
      const y = window.scrollY;
      const down = y > lastY;
      lastY = y;
      if (!armed && down) {
        armed = true;
        window.removeEventListener("scroll", onScroll);
        window.setTimeout(openPopup, 7000);
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    popup.addEventListener("click", (event) => {
      if (event.target.closest("[data-popup-close]")) closePopup();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !popup.hidden) closePopup();
    });
  }

  popupForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.RfSendInquiryForm) {
      window.RfToast?.("Zprávu se nepodařilo odeslat", "error");
      return;
    }
    const ok = await window.RfSendInquiryForm(popupForm, { slug: slugFromPath() });
    if (ok) closePopup();
  });
})();
