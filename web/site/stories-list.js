(() => {
  const esc = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const featuredHtml = (a) => `
      <div class="section-badge">${esc(a.badge || "Příběh měsíce")}</div>
      <a class="featured-card" href="/uspechy/${esc(a.slug)}">
        <div class="featured-photo">
          <img src="${esc(a.listPhoto || a.photo)}" width="640" height="500" alt="${esc(a.crumb)}" />
        </div>
        <div class="featured-content">
          <div class="featured-header">
            <h2>${esc(a.title)}</h2>
            <p>${esc(a.cardExcerpt || a.lede)}</p>
          </div>
          <div class="meta-row">
            ${a.tagPlace ? `<span class="tag tag-dark">${esc(a.tagPlace)}</span>` : ""}
            ${a.tagSpec ? `<span class="tag">${esc(a.tagSpec)}</span>` : ""}
            ${a.tagPrice ? `<span class="tag">${esc(a.tagPrice)}</span>` : ""}
          </div>
          ${a.featuredQuote ? `<blockquote class="quote-box">${esc(a.featuredQuote)}</blockquote>` : ""}
          <span class="pill pill-lg">Přečíst celý příběh</span>
        </div>
      </a>`;

  const cardHtml = (a) => `
        <a class="story-card" href="/uspechy/${esc(a.slug)}">
          <div class="story-img"><img src="${esc(a.listPhoto || a.photo)}" width="400" height="220" alt="" /></div>
          <div class="story-body">
            <div class="story-top">
              <div class="section-badge">${esc(a.cardBadge || a.badge || "Příběh")}</div>
              <h3>${esc(a.title)}</h3>
              <p>${esc(a.cardExcerpt || a.lede)}</p>
            </div>
            <div class="story-foot">
              <div class="meta-row wrap">
                ${a.tagPlace ? `<span class="tag tag-dark">${esc(a.tagPlace)}</span>` : ""}
                ${a.tagSpec ? `<span class="tag">${esc(a.tagSpec)}</span>` : ""}
                ${a.tagPrice ? `<span class="tag">${esc(a.tagPrice)}</span>` : ""}
              </div>
              <span class="read-more">
                Číst více
                <span class="icon" style="width:14px;height:14px"><img src="/static/site/assets/stories/arrow-right.svg" width="14" height="14" alt="" /></span>
              </span>
            </div>
          </div>
        </a>`;

  fetch("/api/stories")
    .then((res) => res.json())
    .then((data) => {
      const featured = document.getElementById("featured");
      const grid = document.getElementById("stories-grid");
      if (featured && data.featured) featured.innerHTML = featuredHtml(data.featured);
      if (grid) grid.innerHTML = (data.articles || []).map(cardHtml).join("");
    })
    .catch(() => {});
})();
