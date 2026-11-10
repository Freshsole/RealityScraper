// Chytré hledání v nápovědě: vybírá nejpodobnější odpověď z častých dotazů (bez serveru, bez AI modelu).
(() => {
  const form = document.getElementById("help-form");
  const input = document.getElementById("help-q");
  const out = document.getElementById("help-ans");
  if (!form || !input || !out) return;

  const E = [
    { q: "Co je Realitify?", k: "realitify co je služba agregátor vyhledávač nemovitostí o nás", a: "Realitify je realitní agregátor. Sbírá inzeráty z českých portálů do jednoho vyhledávače, sjednocuje duplicity a hlídá nové nabídky.", u: "/faq#q1" },
    { q: "Musím se registrovat, abych viděl nabídky?", k: "registrace účet přihlášení zdarma bez registrace vidět nabídky", a: "Ne. Katalog nabídek s mapou a filtry otevřete i bez účtu. Účet potřebujete až pro hlídání nových bytů a notifikace.", u: "/faq#q12" },
    { q: "Které portály Realitify sleduje?", k: "portály zdroje sreality bezrealitky idnes bazoš remax annonce reality odkud", a: "Agregujeme Sreality, Reality.iDNES, Bazoš, ČeskéReality, Bezrealitky, Annonce, M&M Reality, RE/MAX a Reality.cz. Duplicity mezi portály sjednocujeme.", u: "/faq#q10" },
    { q: "Jak rychle dostanu notifikaci o novém bytu?", k: "notifikace upozornění rychlost sekund minut zpoždění email sms nový byt", a: "Nejnovější nabídky stahujeme zhruba každých 60 sekund. Tarif Zdarma hlídá do 5 minut, placené tarify posílají upozornění v řádu desítek sekund. Přesné podmínky najdete u tarifů.", u: "/faq#q3" },
    { q: "Jak být první u nové nabídky?", k: "hlídání hlídací pes filtr první nový inzerát nastavit upozornění", a: "Nastavte filtr (lokalita, dispozice, maximální cena) a zapněte upozornění. Realitify vás pak informuje, jakmile se objeví odpovídající inzerát.", u: "/faq#q3" },
    { q: "Jak často se nabídky aktualizují?", k: "aktualizace frekvence aktuální stahování synchronizace neaktivní zrušené", a: "Nejnovější nabídky stahujeme zhruba každých 60 sekund. Zrušené inzeráty průběžně označujeme jako neaktivní.", u: "/faq#q11" },
    { q: "Jaké jsou tarify a kolik stojí?", k: "tarif tarify cena ceník kolik stojí platba předplatné pro start zdarma plán", a: "K dispozici je tarif Zdarma a placené tarify Start a Pro s rychlejším hlídáním a více hlídacími profily. Aktuální ceny a rozdíly najdete v ceníku.", u: "/faq#q5" },
    { q: "Můžu předplatné kdykoliv zrušit?", k: "zrušit zrušení předplatné odhlásit závazek storno vypovědět", a: "Ano, předplatné je bez závazků. Zrušíte ho kdykoliv v nastavení profilu jedním kliknutím, bez skrytých poplatků.", u: "/faq#q5" },
    { q: "Je prohlížení nabídek opravdu zdarma?", k: "zdarma free zadarmo prohlížení vyhledávání platit poplatek", a: "Ano. Prohlížení a ruční vyhledávání aktivních inzerátů je zdarma. Platíte jen za automatické hlídání a rychlé notifikace.", u: "/faq#q12" },
    { q: "V čem se Realitify liší od hlídacího psa na Sreality?", k: "rozdíl sreality hlídací pes konkurence porovnání oproti proč", a: "Hlídací pes na Sreality sleduje jen Sreality. Realitify hlídá víc portálů najednou, filtruje duplicity a posílá upozornění rychleji.", u: "/faq#q1" },
    { q: "Kolik stojí pronájem 2+kk v Praze?", k: "cena nájem pronájem 2+kk praha kolik stojí průměr medián", a: "", u: "/faq#q4" },
    { q: "Jak poznat předraženou nabídku?", k: "předražená drahá cena porovnat medián kč m2 nadhodnocená", a: "Porovnejte cenu s mediánem Kč/m² ve stejné lokalitě a dispozici. Odchylka nad zhruba 10–15 % si zaslouží vysvětlení.", u: "/faq#q6" },
    { q: "Jak Realitify počítá cenu za m²?", k: "metodika výpočet cena m2 metr čtvereční medián statistika index nájmů", a: "", u: "/faq#q7" },
    { q: "Přehled cen a trhu", k: "trh ceny statistiky index nájmů města lokality vývoj trend graf", a: "", u: "/trh" },
    { q: "Pronájem bytu v Praze", k: "praha pražské byty pronájem nájem hledat", a: "", u: "/pronajem-praha" },
    { q: "Pronájem bytu v Brně", k: "brno brněnské byty pronájem nájem hledat", a: "", u: "/pronajem-brno" },
    { q: "Jaké cookies používáte?", k: "cookies cookie sušenky souhlas analytika marketing nastavení soukromí", a: "Nezbytné cookies držíte zapnuté vždy, analytické, marketingové a funkční si můžete kdykoliv nastavit.", u: "/nastaveni-cookies" },
    { q: "Jak nakládáte s osobními údaji?", k: "osobní údaje gdpr soukromí ochrana data smazat účet email", a: "", u: "/ochrana-soukromi" },
    { q: "Souvisí Realitify se společností Realtify nebo PriceHubble?", k: "realtify pricehubble spojitost firma společnost", a: "Realitify (realitify.cz) nesouvisí se společností Realtify ani PriceHubble.", u: "/faq#q9" },
    { q: "MCP konektor pro AI asistenty", k: "mcp konektor ai claude chatgpt asistent api integrace", a: "", u: "/mcp-docs-cs" },
    { q: "Hry z reálných cen", k: "hra hry zábava tip nájmu vyšší nižší žebříček", a: "", u: "/hry" },
    { q: "Kontakt a podpora", k: "kontakt napsat podpora pomoc problém chyba telefon email", a: "", u: "/kontakt" },
  ];

  const STOP = new Set("a i o u k s v z je to se na do ze si jak co kdy kde kdo mi me my vy ty by byl jsem jste jsou bude muzu muze mam mate chci chtel bych pro po za ale nebo ani ano ne lze lze tak pri".split(" "));
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9+ ]+/g, " ");
  const toks = (s) => norm(s).split(/\s+/).filter((t) => t.length > 1 && !STOP.has(t)).map((t) => t.slice(0, 5));
  E.forEach((e) => { e._q = new Set(toks(e.q)); e._k = new Set(toks(e.k)); e._a = new Set(toks(e.a)); });

  function rank(text) {
    const t = [...new Set(toks(text))];
    if (!t.length) return [];
    return E.map((e) => {
      let sc = 0;
      t.forEach((w) => { sc += (e._q.has(w) ? 3 : 0) + (e._k.has(w) ? 2.5 : 0) + (e._a.has(w) ? 1 : 0); });
      return { e, s: sc / t.length };
    }).filter((r) => r.s > 0).sort((a, b) => b.s - a.s);
  }

  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const NOTE = '<p class="help-note">Odpovědi vybíráme z našich častých dotazů. Nemusí vždy sedět přesně.</p>';
  let timer = 0;

  function show(text) {
    clearTimeout(timer);
    out.hidden = false;
    out.innerHTML = '<div class="help-card"><div class="help-think"><i></i><i></i><i></i><span>Hledám nejlepší odpověď…</span></div></div>';
    timer = setTimeout(() => render(text), 650);
  }

  function render(text) {
    const r = rank(text);
    const best = r[0];
    if (!best || best.s < 1.2) {
      out.innerHTML = '<div class="help-card"><h3>Na tohle zatím neumím odpovědět</h3><p>V našich odpovědích jsem nenašel nic, co by na vaši otázku sedělo. Zkuste ji přeformulovat, nebo se zeptejte přímo nás.</p>' +
        '<a class="help-more" href="/kontakt">Napsat nám →</a></div>' + NOTE;
      return;
    }
    const rel = r.slice(1, 3).filter((x) => x.s >= 0.8);
    const e = best.e;
    out.innerHTML = '<div class="help-card"><h3>' + esc(e.q) + "</h3>" +
      (e.a ? "<p>" + esc(e.a) + "</p>" : "<p>Tohle téma máme podrobně popsané na samostatné stránce.</p>") +
      '<a class="help-more" href="' + e.u + '">' + (e.a ? "Číst víc" : "Otevřít stránku") + " →</a>" +
      (rel.length ? '<div class="help-rel"><span>Mohlo by vás zajímat</span>' + rel.map((x) => '<a href="' + x.e.u + '">' + esc(x.e.q) + "</a>").join("") + "</div>" : "") +
      "</div>" + NOTE;
  }

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const v = input.value.trim();
    if (v.length < 2) { out.hidden = true; return; }
    show(v);
  });
  input.addEventListener("input", () => { if (!input.value.trim()) { out.hidden = true; clearTimeout(timer); } });
  document.querySelectorAll(".help-chips button").forEach((b) =>
    b.addEventListener("click", () => { input.value = b.dataset.q; show(b.dataset.q); }));
})();
