"""Filter-aware search URLs for the extra portals (verified live with Playwright, 2026-11).

Every ``build_*`` returns ``None`` when the filters carry nothing the portal can express, so callers
keep the plain catalog URL (used by catalog sync).  ``parse_*`` is the inverse and returns a partial
canonical filter dict (offers/sizes/districts/price/area).

Not covered (blocked for automation, unverified): ceskereality (only price), annonce.
"""

from __future__ import annotations

import base64
import re
import zlib
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

PRAHA_NUMS = tuple(range(1, 11))
# RE/MAX + Realitymix share the same numeric ids (kraj and Praha 1-10 district).
KRAJ_IDS = {
    "praha": 19, "stredocesky-kraj": 27, "jihocesky-kraj": 35, "plzensky-kraj": 43,
    "karlovarsky-kraj": 51, "ustecky-kraj": 60, "liberecky-kraj": 78, "kralovehradecky-kraj": 86,
    "pardubicky-kraj": 94, "vysocina-kraj": 108, "jihomoravsky-kraj": 116, "olomoucky-kraj": 124,
    "moravskoslezsky-kraj": 132, "zlinsky-kraj": 141,
}
PRAHA_DISTRICT_IDS = {1: 19, 2: 27, 3: 35, 4: 43, 5: 51, 6: 60, 7: 78, 8: 86, 9: 94, 10: 108}
_ID_TO_PRAHA = {v: k for k, v in PRAHA_DISTRICT_IDS.items()}
_ID_TO_KRAJ = {v: k for k, v in KRAJ_IDS.items()}


def _num(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _list(filters: dict, key: str) -> list[str]:
    return [str(i) for i in (filters.get(key) or []) if i]


def _praha_numbers(districts: list[str]) -> list[int]:
    out = []
    for d in districts:
        m = re.fullmatch(r"praha-(\d+)", d)
        if m and int(m.group(1)) in PRAHA_NUMS:
            out.append(int(m.group(1)))
    return sorted(set(out))


def _is_sale(filters: dict) -> bool:
    for item in _list(filters, "offers"):
        item = item.casefold()
        if "pronaj" in item:
            return False
        if "prodej" in item:
            return True
    return False


def _query(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))


def _base(parsed: dict, url: str) -> dict:
    parsed.setdefault("offers", ["prodej" if "prodej" in (url or "").lower() else "pronajem"])
    return parsed


# ---------------------------------------------------------------- RE/MAX
REMAX_SIZES = {"1+kk": 2, "1+1": 9, "2+kk": 3, "2+1": 10, "3+kk": 4, "3+1": 11, "4+kk": 5, "4+1": 12,
               "5+kk": 6, "5+1": 13, "6-a-vice": 14, "atypicky": 16, "pokoj": 19}
_REMAX_REV = {v: k for k, v in REMAX_SIZES.items()}


def build_remax(site: str, filters: dict) -> str | None:
    q: list[tuple[str, str]] = []
    for size in _list(filters, "sizes"):
        if size in REMAX_SIZES:
            q.append((f"types[4][{REMAX_SIZES[size]}]", "on"))
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    if praha:
        q.extend((f"regions[19][{PRAHA_DISTRICT_IDS[n]}]", "on") for n in praha)
    else:
        for d in districts:
            if d in KRAJ_IDS:
                q.append((f"regions[{KRAJ_IDS[d]}]", "on"))
    for key, name in (("price_from", "price_from"), ("price_to", "price_to"),
                      ("area_from", "area_from"), ("area_to", "area_to")):
        if _num(filters.get(key)):
            q.append((name, str(int(filters[key]))))
    if not q:
        return None
    q += [("hledani", "1"), ("sale", "1" if _is_sale(filters) else "2")]
    return f"{site}/reality/vyhledavani/?" + urlencode(sorted(q))


def parse_remax(url: str) -> dict:
    q = _query(url)
    out: dict = {"offers": ["prodej"] if q.get("sale") == "1" or "/prodej" in url.lower() else ["pronajem"]}
    sizes, districts = [], []
    for key in q:
        m = re.fullmatch(r"types\[4\]\[(\d+)\]", key)
        if m and int(m.group(1)) in _REMAX_REV:
            sizes.append(_REMAX_REV[int(m.group(1))])
        m = re.fullmatch(r"regions\[19\]\[(\d+)\]", key)
        if m and int(m.group(1)) in _ID_TO_PRAHA:
            districts.append(f"praha-{_ID_TO_PRAHA[int(m.group(1))]}")
        m = re.fullmatch(r"regions\[(\d+)\]", key)
        if m and int(m.group(1)) in _ID_TO_KRAJ and not (int(m.group(1)) == 19 and districts):
            districts.append(_ID_TO_KRAJ[int(m.group(1))])
    out.update(sizes=sizes, districts=districts, price_from=_num(q.get("price_from")),
               price_to=_num(q.get("price_to")), area_from=_num(q.get("area_from")),
               area_to=_num(q.get("area_to")))
    return out


# ---------------------------------------------------------------- M&M Reality
MM_SIZES = {"1+kk": 51, "1+1": 52, "2+kk": 53, "2+1": 54, "3+kk": 200, "3+1": 55, "4+kk": 201, "4+1": 56,
            "5+kk": 202, "5+1": 57, "6-a-vice": 57, "atypicky": 58}
_MM_REV = {51: "1+kk", 52: "1+1", 53: "2+kk", 54: "2+1", 200: "3+kk", 55: "3+1", 201: "4+kk", 56: "4+1",
           202: "5+kk", 57: "5+1", 58: "atypicky"}


def _mm_encode(raw: str) -> str:
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = comp.compress(raw.encode()) + comp.flush()
    return quote(base64.b64encode(data).decode(), safe="")


def _mm_decode(blob: str) -> dict[str, str]:
    try:
        raw = unquote(blob)
        raw += "=" * (-len(raw) % 4)
        return dict(parse_qsl(zlib.decompress(base64.b64decode(raw), -15).decode(), keep_blank_values=True))
    except Exception:
        return {}


def build_mm(site: str, filters: dict) -> str:
    slug = "prodej" if _is_sale(filters) else "pronajem"
    praha = _praha_numbers(_list(filters, "districts"))
    districts = _list(filters, "districts")
    loc = ""
    if len(praha) == 1 and len(districts) == 1:
        loc = f"praha-{praha[0]}/"
    elif praha or "praha" in districts:
        loc = "hlavni-mesto-praha/"
    parts: list[str] = []
    if _num(filters.get("price_from")) or _num(filters.get("price_to")):
        parts += [f"common[price][from]={int(filters.get('price_from') or 0)}",
                  f"common[price][to]={int(filters.get('price_to') or 0)}"]
    ids = list(dict.fromkeys(MM_SIZES[s] for s in _list(filters, "sizes") if s in MM_SIZES))
    parts += [f"groups[0][types][{i}]={v}" for i, v in enumerate(ids)]
    if _num(filters.get("area_from")) or _num(filters.get("area_to")):
        parts += [f"groups[0][totalArea][from]={int(filters.get('area_from') or 0)}",
                  f"groups[0][totalArea][to]={int(filters.get('area_to') or 0)}"]
    url = f"{site}/nemovitosti/{slug}/byty/{loc}"
    return url + (f"?query={_mm_encode('&'.join(parts))}" if parts else "")


def parse_mm(url: str) -> dict:
    split = urlsplit(url or "")
    path = split.path.casefold()
    out: dict = {"offers": ["prodej"] if "/prodej" in path else ["pronajem"]}
    m = re.search(r"/(praha-\d+)/", path)
    if m:
        out["districts"] = [m.group(1)]
    elif "hlavni-mesto-praha" in path:
        out["districts"] = ["praha"]
    blob = _query(url).get("query")
    if blob:
        d = _mm_decode(blob)
        out["price_from"] = _num(d.get("common[price][from]")) or None
        out["price_to"] = _num(d.get("common[price][to]")) or None
        out["area_from"] = _num(d.get("groups[0][totalArea][from]")) or None
        out["area_to"] = _num(d.get("groups[0][totalArea][to]")) or None
        out["sizes"] = [_MM_REV[int(v)] for k, v in d.items()
                        if k.startswith("groups[0][types]") and _num(v) in _MM_REV]
    return out


# ---------------------------------------------------------------- UlovDomov
UD_SIZES = {"1+kk": "1-kk", "1+1": "1-1", "2+kk": "2-kk", "2+1": "2-1", "3+kk": "3-kk", "3+1": "3-1",
            "4+kk": "4-kk", "4+1": "4-1", "5+kk": "5-kk", "5+1": "5-1", "6-a-vice": "6-kk"}
_UD_REV = {v: k for k, v in UD_SIZES.items() if k != "6-a-vice"}
_UD_REV["6-kk"] = "6-a-vice"


def build_ulovdomov(site: str, filters: dict) -> str | None:
    offer = "prodej" if _is_sale(filters) else "pronajem"
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    q: list[tuple[str, str]] = []
    sizes = list(dict.fromkeys(UD_SIZES[s] for s in _list(filters, "sizes") if s in UD_SIZES))
    if sizes:
        q.append(("dispozice", ",".join(sizes)))
    if _num(filters.get("price_from")):
        q.append(("cena-od", f"{int(filters['price_from'])}kc"))
    if _num(filters.get("price_to")):
        q.append(("cena-do", f"{int(filters['price_to'])}kc"))
    if _num(filters.get("area_from")):
        q.append(("od", f"{int(filters['area_from'])}m2"))
    if _num(filters.get("area_to")):
        q.append(("do", f"{int(filters['area_to'])}m2"))
    path = f"/{offer}/byty"
    if len(praha) == 1 and len(districts) == 1:
        path, q = f"/{offer}/bytu/praha-{praha[0]}", q + [("lokace", f"Praha;{praha[0]}")]
    elif praha or "praha" in districts:
        path, q = f"/{offer}/bytu/praha", q + [("lokace", "Praha")]
    if not q:
        return None
    return f"{site}{path}?" + "&".join(f"{k}={v}" for k, v in q)


def parse_ulovdomov(url: str) -> dict:
    split = urlsplit(url or "")
    q = _query(url)
    out: dict = {"offers": ["prodej"] if "/prodej" in split.path else ["pronajem"]}
    parts = [p for p in split.path.split("/") if p]
    sizes = [_UD_REV[s] for s in (q.get("dispozice") or "").split(",") if s in _UD_REV]
    if len(parts) >= 4 and parts[3] in _UD_REV and _UD_REV[parts[3]] not in sizes:
        sizes.append(_UD_REV[parts[3]])
    if len(parts) >= 3 and re.fullmatch(r"praha(-\d+)?", parts[2]):
        out["districts"] = [parts[2]]
    strip = lambda v, suf: _num(re.sub(suf + r"$", "", v or "")) if v else None  # noqa: E731
    out.update(sizes=sizes, price_from=strip(q.get("cena-od"), "kc"), price_to=strip(q.get("cena-do"), "kc"),
               area_from=strip(q.get("od"), "m2"), area_to=strip(q.get("do"), "m2"))
    return out


# ---------------------------------------------------------------- Reality.cz
RCZ_SIZES = {"1+kk": [12], "1+1": [13], "2+kk": [14], "2+1": [15], "3+kk": [16], "3+1": [17], "4+kk": [18],
             "4+1": [19], "5+kk": [20], "5+1": [21], "6-a-vice": [20, 21]}
_RCZ_REV = {12: "1+kk", 13: "1+1", 14: "2+kk", 15: "2+1", 16: "3+kk", 17: "3+1", 18: "4+kk", 19: "4+1",
            20: "5+kk", 21: "5+1"}
RCZ_AREA_BANDS = [(11, 0, 40), (12, 40, 50), (13, 50, 60), (14, 60, 70), (15, 70, 80), (16, 80, 100),
                  (17, 100, 150), (18, 150, 10**6)]


def _rcz_codes(token: str) -> list[int]:
    return [int(token[i:i + 2]) for i in range(0, len(token) - len(token) % 2, 2)]


def build_realitycz(site: str, filters: dict, locality: str) -> str:
    sale = _is_sale(filters)
    offer = "prodej" if sale else "pronajem"
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    if len(praha) == 1 and len(districts) == 1:
        locality = f"obvod-Praha-{praha[0]}"
    elif praha or "praha" in districts:
        locality = "hlavni-mesto-Praha"
    path = f"/{offer}/byty/{locality}/"
    pf, pt = _num(filters.get("price_from")), _num(filters.get("price_to"))
    if pf or pt:
        path += f"cena-{pf or 0}-{pt or 'max'}-{2 if sale else 4}/"
    f = ""
    codes = sorted({c for s in _list(filters, "sizes") for c in RCZ_SIZES.get(s, [])})
    if codes:
        f += "D" + "".join(str(c) for c in codes)
    af, at = _num(filters.get("area_from")), _num(filters.get("area_to"))
    if af or at:
        bands = [b for b, lo, hi in RCZ_AREA_BANDS if (at is None or lo < at) and (af is None or hi > af)]
        if bands and len(bands) < len(RCZ_AREA_BANDS):
            f += "F" + "".join(str(b) for b in bands)
    return f"{site}{path}" + (f"?f={f}" if f else "")


def parse_realitycz(url: str) -> dict:
    split = urlsplit(url or "")
    out: dict = {"offers": ["prodej"] if "/prodej" in split.path else ["pronajem"]}
    m = re.search(r"/cena-(\d+)-(\d+|max)-\d/", split.path)
    if m:
        out["price_from"] = int(m.group(1)) or None
        out["price_to"] = None if m.group(2) == "max" else int(m.group(2))
    m = re.search(r"obvod-Praha-(\d+)", split.path, re.I)
    if m:
        out["districts"] = [f"praha-{m.group(1)}"]
    elif "hlavni-mesto-praha" in split.path.lower():
        out["districts"] = ["praha"]
    f = _query(url).get("f", "")
    dm = re.search(r"D(\d+)", f)
    if dm:
        out["sizes"] = [_RCZ_REV[c] for c in _rcz_codes(dm.group(1)) if c in _RCZ_REV]
    fm = re.search(r"F(\d+)", f)
    if fm:
        picked = [b for b in _rcz_codes(fm.group(1))]
        rows = [(lo, hi) for b, lo, hi in RCZ_AREA_BANDS if b in picked]
        if rows:
            out["area_from"] = min(lo for lo, _ in rows) or None
            out["area_to"] = max(hi for _, hi in rows) if max(hi for _, hi in rows) < 10**6 else None
    return out


# ---------------------------------------------------------------- Realitymix
RMIX_SIZES = {"1+1": [9], "1+kk": [2], "2+1": [10], "2+kk": [3], "3+1": [11], "3+kk": [4], "4+1": [12],
              "4+kk": [5], "5+1": [13], "5+kk": [6], "6-a-vice": [14, 7, 15, 8], "atypicky": [16], "pokoj": [18]}
_RMIX_REV = {9: "1+1", 2: "1+kk", 10: "2+1", 3: "2+kk", 11: "3+1", 4: "3+kk", 12: "4+1", 5: "4+kk",
             13: "5+1", 6: "5+kk", 14: "6-a-vice", 7: "6-a-vice", 15: "6-a-vice", 8: "6-a-vice",
             16: "atypicky", 18: "pokoj"}


def build_realitymix(site: str, filters: dict) -> str | None:
    q: list[tuple[str, str]] = []
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    if praha:
        q.append(("form[adresa_kraj_id][]", "19"))
        q += [("form[adresa_region_id][19][]", str(PRAHA_DISTRICT_IDS[n])) for n in praha]
    else:
        q += [("form[adresa_kraj_id][]", str(KRAJ_IDS[d])) for d in districts if d in KRAJ_IDS]
    for s in _list(filters, "sizes"):
        q += [("form[dispozice][]", str(c)) for c in RMIX_SIZES.get(s, [])]
    for key, name in (("price_from", "cena_normalizovana__from"), ("price_to", "cena_normalizovana__to"),
                      ("area_from", "plocha__from"), ("area_to", "plocha__to")):
        if _num(filters.get(key)):
            q.append((f"form[{name}]", str(int(filters[key]))))
    if not q:
        return None
    head = [("form[nemovitost_typ][]", "4"), ("form[inzerat_typ]", "1" if _is_sale(filters) else "2"),
            ("form[adresa_stat]", "CZ")]
    return f"{site}/vypis-nabidek/?" + urlencode(head + q, safe="[]")


def parse_realitymix(url: str) -> dict:
    pairs = parse_qsl(urlsplit(url or "").query, keep_blank_values=True)
    d: dict[str, list[str]] = {}
    for k, v in pairs:
        d.setdefault(k, []).append(v)
    out: dict = {"offers": ["prodej"] if d.get("form[inzerat_typ]", [""])[0] == "1" or "/prodej" in url.lower()
                 else ["pronajem"]}
    sizes = [_RMIX_REV[int(v)] for v in d.get("form[dispozice][]", []) if _num(v) in _RMIX_REV]
    out["sizes"] = list(dict.fromkeys(sizes))
    regs = [_ID_TO_PRAHA[int(v)] for v in d.get("form[adresa_region_id][19][]", []) if _num(v) in _ID_TO_PRAHA]
    if regs:
        out["districts"] = [f"praha-{n}" for n in regs]
    else:
        out["districts"] = [_ID_TO_KRAJ[int(v)] for v in d.get("form[adresa_kraj_id][]", []) if _num(v) in _ID_TO_KRAJ]
    for key, name in (("price_from", "cena_normalizovana__from"), ("price_to", "cena_normalizovana__to"),
                      ("area_from", "plocha__from"), ("area_to", "plocha__to")):
        out[key] = _num((d.get(f"form[{name}]") or [""])[0])
    return out


# ---------------------------------------------------------------- Realingo
RLG_SIZES = {"1+kk": "1+kk", "1+1": "1+1", "2+kk": "2+kk", "2+1": "2+1", "3+kk": "3+kk", "3+1": "3+1",
             "4+kk": "4+kk", "4+1": "4+1", "5+kk": "5+kk", "5+1": "5+1"}


def build_realingo(site: str, filters: dict) -> str:
    sale = _is_sale(filters)
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    loc = "cr"
    if len(praha) == 1 and len(districts) == 1:
        loc = f"praha-{praha[0]}"
    elif praha or "praha" in districts:
        loc = "praha"
    path = f"{site}/{'prodej' if sale else 'pronajem'}_byty/{loc}/"
    af, at = _num(filters.get("area_from")), _num(filters.get("area_to"))
    if af or at:
        path += f"{af or ''}-{at or ''}_plocha/"
    sizes = [RLG_SIZES[s] for s in _list(filters, "sizes") if s in RLG_SIZES]
    if sizes:
        path += f"{','.join(dict.fromkeys(sizes))}_kat/"
    pf, pt = _num(filters.get("price_from")), _num(filters.get("price_to"))
    if pf or pt:
        path += f"{pf or ''}-{pt or ''}_cena/"
    return path


def parse_realingo(url: str) -> dict:
    path = unquote(urlsplit(url or "").path)
    out: dict = {"offers": ["prodej"] if "/prodej_" in path else ["pronajem"]}
    m = re.search(r"_byty/([^/]+)/", path)
    if m:
        loc = m.group(1).casefold()
        mm = re.search(r"praha[,_ -]*(?:praha_)?(\d+)$", loc)
        if mm and loc.startswith("praha"):
            out["districts"] = [f"praha-{mm.group(1)}"]
        elif loc.startswith("praha"):
            out["districts"] = ["praha"]
    m = re.search(r"/([^/]*)_kat/", path)
    if m:
        out["sizes"] = [s for s in m.group(1).split(",") if s in RLG_SIZES]
    for seg, (kf, kt) in (("cena", ("price_from", "price_to")), ("plocha", ("area_from", "area_to"))):
        m = re.search(rf"/(\d*)-(\d*)_{seg}/", path)
        if m:
            out[kf], out[kt] = _num(m.group(1)), _num(m.group(2))
    return out


# ---------------------------------------------------------------- Eurobydleni
EUB_SIZES = {"1+kk": "1_kk", "1+1": "1_1", "2+kk": "2_kk", "2+1": "2_1", "3+kk": "3_kk", "3+1": "3_1",
             "4+kk": "4_kk", "4+1": "4_1", "5+kk": "5_kk", "5+1": "5_1", "pokoj": "pokoj"}
_EUB_REV = {v: k for k, v in EUB_SIZES.items()}


def build_eurobydleni(site: str, filters: dict) -> str | None:
    """Portal only filters price/area via POST, so the URL carries location (+ one disposition)."""
    districts = _list(filters, "districts")
    praha = _praha_numbers(districts)
    loc = ""
    if len(praha) == 1 and len(districts) == 1:
        loc = f"praha-{praha[0]}"
    elif praha or "praha" in districts:
        loc = "praha"
    if not loc:
        return None
    offer = "prodej" if _is_sale(filters) else "pronajem"
    sizes = [EUB_SIZES[s] for s in _list(filters, "sizes") if s in EUB_SIZES]
    if len(sizes) == 1:
        return f"{site}/byty/{sizes[0]}/{loc}/{offer}/"
    return f"{site}/byty/{loc}/{offer}/"


def parse_eurobydleni(url: str) -> dict:
    parts = [p for p in urlsplit(url or "").path.split("/") if p]
    out: dict = {"offers": ["prodej"] if "prodej" in parts else ["pronajem"]}
    for p in parts:
        if re.fullmatch(r"praha(-\d+)?", p):
            out["districts"] = [p]
        if p in _EUB_REV:
            out["sizes"] = [_EUB_REV[p]]
    return out


# ---------------------------------------------------------------- eSpolubydleni
def build_espolubydleni(site: str, filters: dict) -> str:
    districts = _list(filters, "districts")
    base = f"{site}/podnajem-spolubydlici/"
    if _praha_numbers(districts) or "praha" in districts:
        base += "praha"
    q: list[tuple[str, str]] = []
    if _num(filters.get("price_from")):
        q.append(("cena_od", str(int(filters["price_from"]))))
    if _num(filters.get("price_to")):
        q.append(("cena_do", str(int(filters["price_to"]))))
    nums = {s[0] for s in _list(filters, "sizes") if re.match(r"[1-5]\+", s)}
    if len(nums) == 1:
        q.append(("velikost", nums.pop()))
    return base + (f"?h=1&{urlencode(q)}" if q else "")


def parse_espolubydleni(url: str) -> dict:
    q = _query(url)
    out: dict = {"offers": ["pronajem"], "price_from": _num(q.get("cena_od")), "price_to": _num(q.get("cena_do"))}
    if urlsplit(url or "").path.rstrip("/").endswith("/praha"):
        out["districts"] = ["praha"]
    return out
