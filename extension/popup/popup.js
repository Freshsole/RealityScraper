function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      resolve(response || { ok: false, error: chrome.runtime.lastError?.message });
    });
  });
}

const statusPill = document.getElementById("status-pill");
const body = document.getElementById("body");
const apiSelect = document.getElementById("api-base");

async function loadConfig() {
  const res = await send({ type: "RT_GET_CONFIG" });
  const current = res.apiBase || "https://realitify.cz";
  const defaults = res.defaults || [current];
  apiSelect.innerHTML = "";
  for (const base of defaults) {
    const opt = document.createElement("option");
    opt.value = base;
    opt.textContent = base.replace(/^https?:\/\//, "");
    if (base === current) opt.selected = true;
    apiSelect.appendChild(opt);
  }
  if (!defaults.includes(current)) {
    const opt = document.createElement("option");
    opt.value = current;
    opt.textContent = current;
    opt.selected = true;
    apiSelect.appendChild(opt);
  }
}

async function render() {
  await loadConfig();
  const res = await send({ type: "RT_GET_ME" });
  if (!res.ok) {
    statusPill.textContent = "Chyba";
    statusPill.className = "pill off";
    body.innerHTML = `<p class="muted">${res.error || "Nelze načíst stav."}</p>`;
    return;
  }
  const me = res.me || {};
  if (me.active) {
    statusPill.textContent = me.pro ? "PRO Active" : "Active";
    statusPill.className = "pill on";
    body.innerHTML = `
      <h2>Skóre na Sreality běží</h2>
      <div class="meta">
        <div><span>Účet</span><strong>${me.name || me.email || "—"}</strong></div>
        <div><span>Tarif</span><strong>${me.label || me.plan || "—"}</strong></div>
      </div>
      <p class="muted">Otevři výpis nebo detail na sreality.cz — Realitify widget se vloží automaticky.</p>
      <a class="btn" href="${me.app_url || "https://realitify.cz/app"}" target="_blank" rel="noopener">Otevřít Realitify</a>
    `;
    return;
  }
  if (me.authenticated) {
    statusPill.textContent = "Bez tarifu";
    statusPill.className = "pill off";
    body.innerHTML = `
      <h2>Chybí aktivní předplatné</h2>
      <p class="muted">Pro skóre na Sreality potřebuješ tarif Start nebo PRO.</p>
      <a class="btn" href="${me.pricing_url || "https://realitify.cz/#cenik"}" target="_blank" rel="noopener">Aktivovat tarif</a>
    `;
    return;
  }
  statusPill.textContent = "Odhlášen";
  statusPill.className = "pill off";
  body.innerHTML = `
    <h2>Přihlas se k Realitify</h2>
    <p class="muted">Po přihlášení na realitify.cz se session použije i v tomto rozšíření.</p>
    <a class="btn" href="${me.login_url || "https://realitify.cz/prihlaseni"}" target="_blank" rel="noopener">Přihlásit se</a>
  `;
}

apiSelect.addEventListener("change", async () => {
  await send({ type: "RT_SET_API_BASE", apiBase: apiSelect.value });
  await render();
});

document.getElementById("refresh").addEventListener("click", () => render());

render();
