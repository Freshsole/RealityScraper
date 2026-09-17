(() => {
  const PORTALS = [
    { id: "sreality", label: "Sreality", icon: "/static/icons/sreality.svg" },
    { id: "idnes", label: "Reality.iDNES", icon: "/static/icons/idnes.svg", match: ["idnes"] },
    { id: "bazos", label: "Bazoš", icon: "/static/icons/bazos.svg", match: ["bazos"] },
    { id: "ceskereality", label: "ČeskéReality", icon: "/static/icons/ceskereality.svg", match: ["ceskereality"] },
    { id: "bezrealitky", label: "Bezrealitky", icon: "/static/icons/bezrealitky.svg", match: ["bezrealitky"] },
    { id: "annonce", label: "Annonce", icon: "/static/icons/annonce.svg", match: ["annonce"] },
    { id: "mmreality", label: "M&M Reality", icon: "/static/icons/mmreality.svg", match: ["mmreality"] },
    { id: "ulovdomov", label: "UlovDomov", icon: "/static/icons/ulovdomov.svg", match: ["ulovdomov"] },
    { id: "remax", label: "RE/MAX", icon: "/static/icons/remax.svg", match: ["remax"] },
    { id: "realitycz", label: "Reality.cz", icon: "/static/icons/realitycz.svg", match: ["reality.cz", "realitycz"] },
  ];
  const byId = Object.fromEntries(PORTALS.map((item) => [item.id, item]));

  function fold(value) {
    return String(value || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function portalIdFrom(value) {
    const raw = fold(value);
    if (!raw) return "sreality";
    if (raw.includes("bezrealitky")) return "bezrealitky";
    if (raw.includes("idnes")) return "idnes";
    if (raw.includes("bazos")) return "bazos";
    if (raw.includes("ceskereality")) return "ceskereality";
    if (raw.includes("annonce")) return "annonce";
    if (raw.includes("mmreality")) return "mmreality";
    if (raw.includes("ulovdomov")) return "ulovdomov";
    if (raw.includes("remax")) return "remax";
    if (raw === "realitycz" || raw.includes("://www.reality.cz") || raw.includes("://reality.cz") || raw.includes("m.reality.cz")) {
      return "realitycz";
    }
    if (raw.includes("sreality")) return "sreality";
    if (byId[raw]) return raw;
    return "sreality";
  }

  function portalMeta(value) {
    const id = portalIdFrom(value);
    return byId[id] || byId.sreality;
  }

  window.PORTALS = PORTALS;
  window.portalIdFrom = portalIdFrom;
  window.portalMeta = portalMeta;
  window.portalLabelOf = (value) => portalMeta(value).label;
  window.portalIconOf = (value) => portalMeta(value).icon;
})();
