from __future__ import annotations

from typing import Any

from app import bazos_url, bezrealitky_url, idnes_url, localities, url_builder
from app.portal_urls import MODULES as EXTRA_URLS
from app.sources import is_bazos, is_bezrealitky, is_idnes, portal_of, source_name

SR_OFFERS = {"pronajem": "PRONAJEM", "prodej": "PRODEJ"}
SR_OFFER_LABELS = {"drazby": "Dražby", "podily": "Podíly"}
SR_SIZES = {
    "1+1": "DISP_1_1",
    "1+kk": "DISP_1_KK",
    "2+1": "DISP_2_1",
    "2+kk": "DISP_2_KK",
    "3+1": "DISP_3_1",
    "3+kk": "DISP_3_KK",
    "4+1": "DISP_4_1",
    "4+kk": "DISP_4_KK",
    "5+1": "DISP_5_1",
    "5+kk": "DISP_5_KK",
    "atypicky": "OSTATNI",
    "pokoj": "GARSONIERA",
}
SR_SIZE_EXPAND = {"6-a-vice": ["DISP_6_1", "DISP_6_KK", "DISP_7_1", "DISP_7_KK"]}
SR_SIZE_LABELS = {key: label for key, label in url_builder.SIZES}
SR_DISTRICTS = {
    "praha-1": "R15107966",
    "praha-2": "R19999122",
    "praha-3": "R19999121",
    "praha-4": "R19999068",
    "praha-5": "R19999086",
    "praha-6": "R19999115",
    "praha-7": "R19999114",
    "praha-8": "R19999109",
    "praha-9": "R19999082",
    "praha-10": "R19999075",
}
BR_DISTRICTS = {value: key for key, value in SR_DISTRICTS.items()}
BR_DISTRICT_LABELS = dict(bezrealitky_url.DISTRICTS)
SR_OWNERSHIP = {"druzstevni": "DRUZSTEVNI", "osobni": "OSOBNI", "statni-obecni": "OBECNI"}
SR_OWNERSHIP_LABELS = {key: label for key, label in url_builder.OWNERSHIP}
SR_CONDITIONS = {
    "developerske-projekty": "PROJECT",
    "dobry-stav": "GOOD",
    "k-demolici": "DEMOLITION",
    "novostavby": "NEW",
    "po-rekonstrukci": "AFTER_RECONSTRUCTION",
    "pred-rekonstrukci": "BEFORE_RECONSTRUCTION",
    "spatny-stav": "BAD",
    "v-rekonstrukci": "IN_RECONSTRUCTION",
    "ve-vystavbe": "CONSTRUCTION",
    "velmi-dobry-stav": "VERY_GOOD",
}
SR_CONDITION_LABELS = {key: label for key, label in url_builder.CONDITIONS}
SR_BUILDINGS = {"cihlova": ["BRICK"], "panelova": ["PANEL"], "ostatni": ["MIXED", "PREFAB", "SKELET", "STONE"]}
SR_EXTRAS = {
    "balkon": "balcony",
    "bezbarierovy": "barrierFree",
    "garaz": "garage",
    "lodzie": "loggia",
    "parkovani": "parking",
    "sklep": "cellar",
    "terasa": "terrace",
    "vytah": "lift",
}
SR_EXTRA_LABELS = {key: label for key, label in url_builder.EXTRAS}
BR_ESTATE_LABELS = {key: label for key, label in bezrealitky_url.ESTATES}
BR_SIZE_LABELS = {key: label for key, label in bezrealitky_url.SIZES}
BR_OWNERSHIP_LABELS = {key: label for key, label in bezrealitky_url.OWNERSHIP}
BR_CONDITION_LABELS = {key: label for key, label in bezrealitky_url.CONDITIONS}
BR_BUILDING_LABELS = {key: label for key, label in bezrealitky_url.BUILDINGS}
BR_EXTRA_LABELS = {key: label for key, label in bezrealitky_url.EXTRAS}


def convert_search_url(url: str) -> dict[str, Any]:
    extra = EXTRA_URLS.get(portal_of(url))
    if extra:
        parsed = extra.parse_url(url)
        filters = url_builder.default_filters()
        filters["offers"] = parsed.get("offers") or ["pronajem"]
        filters["districts"] = parsed.get("districts") or []
        filters["sizes"] = parsed.get("sizes") or []
        filters["price_from"] = parsed.get("price_from")
        filters["price_to"] = parsed.get("price_to")
        filters["area_from"] = parsed.get("area_from")
        filters["area_to"] = parsed.get("area_to")
        filters["source"] = "sreality"
        filters = localities.normalize_filters(filters)
        target_url = url_builder.build_url(filters)
        target = "Sreality"
        skipped: list[str] = []
        notes = ["Převod z dalšího portálu bere základní filtry (nabídka, lokalita, cena)."]
    elif is_idnes(url):
        filters, skipped, notes = idnes_to_sr(idnes_url.parse_url(url))
        filters["source"] = "sreality"
        filters = localities.normalize_filters(filters)
        target_url = url_builder.build_url(filters)
        target = "Sreality"
    elif is_bazos(url):
        filters, skipped, notes = bazos_to_sr(bazos_url.parse_url(url))
        filters["source"] = "sreality"
        filters = localities.normalize_filters(filters)
        target_url = url_builder.build_url(filters)
        target = "Sreality"
    elif is_bezrealitky(url):
        filters, skipped, notes = br_to_sr(bezrealitky_url.parse_url(url))
        filters["source"] = "sreality"
        filters = localities.normalize_filters(filters)
        target_url = url_builder.build_url(filters)
        target = "Sreality"
    else:
        filters, skipped, notes = sr_to_br(url_builder.parse_url(url))
        filters["source"] = "bezrealitky"
        filters = localities.normalize_filters(filters)
        target_url = bezrealitky_url.build_url(filters)
        target = "Bezrealitky"
    return {
        "source": source_name(url),
        "target": target,
        "url": target_url,
        "filters": filters,
        "skipped": skipped,
        "notes": notes,
    }


def sr_to_br(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = bezrealitky_url.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    dst["offers"] = _map_list(src.get("offers"), SR_OFFERS, SR_OFFER_LABELS, skipped) or ["PRONAJEM"]
    category = src.get("category") or "byty"
    if category != "byty":
        skipped.append(f"Typ nemovitosti „{category}“")
    dst["estates"] = ["BYT"]
    sizes: list[str] = []
    for size in src.get("sizes") or []:
        if size in SR_SIZE_EXPAND:
            sizes.extend(SR_SIZE_EXPAND[size])
            notes.append("6 pokojů a více → 6+kk / 6+1 / 7+kk / 7+1")
        elif size in SR_SIZES:
            sizes.append(SR_SIZES[size])
            if size == "pokoj":
                notes.append("Pokoj / spolubydlení → garsoniéra")
            if size == "atypicky":
                notes.append("Atypický → ostatní dispozice")
        elif size:
            skipped.append(f"Dispozice {SR_SIZE_LABELS.get(size, size)}")
    dst["sizes"] = list(dict.fromkeys(sizes))
    districts = []
    src_districts = [str(item) for item in (src.get("districts") or [])]
    if set(src_districts) >= set(localities.SREALITY_CZECH_REGIONS) or any(
        localities.is_czech_country(item, item) for item in src_districts
    ):
        districts = [localities.CZECH_OSM]
        dst["osm_value"] = "Česko"
        dst["boundary_points"] = localities.CZ_BOUNDARY_POINTS
    else:
        for item in src_districts:
            osm = localities.SREALITY_TO_OSM.get(item) or SR_DISTRICTS.get(item)
            if osm:
                districts.append(osm)
            elif item:
                skipped.append(f"Lokalita {item}")
        dst["osm_value"] = ", ".join(BR_DISTRICT_LABELS.get(item, item) for item in districts)
    dst["districts"] = list(dict.fromkeys(districts))
    dst["ownership"] = _map_list(src.get("ownership"), SR_OWNERSHIP, SR_OWNERSHIP_LABELS, skipped)
    dst["conditions"] = _map_list(src.get("conditions"), SR_CONDITIONS, SR_CONDITION_LABELS, skipped)
    buildings: list[str] = []
    for item in src.get("buildings") or []:
        if item in SR_BUILDINGS:
            buildings.extend(SR_BUILDINGS[item])
            if item == "ostatni":
                notes.append("Stavba ostatní → smíšená / montovaná / skeletová / kamenná")
        elif item:
            skipped.append(f"Konstrukce {item}")
    dst["buildings"] = list(dict.fromkeys(buildings))
    extras: list[str] = []
    for item in src.get("extras") or []:
        if item in SR_EXTRAS:
            extras.append(SR_EXTRAS[item])
        elif item:
            skipped.append(f"{SR_EXTRA_LABELS.get(item, item)} (na Bezrealitky nejde stejně)")
    dst["extras"] = extras
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    dst["sort"] = "TIMEORDER_DESC"
    if src.get("sort") == "nejlevnejsi":
        notes.append("Řazení nejlevnější → na Bezrealitky hlídáme nejnovější (detekce nových inzerátů)")
    if src.get("energy"):
        skipped.append("Energetická náročnost")
    if src.get("pois"):
        skipped.append("V okolí nemovitosti (zastávky, školy, obchody…)")
    if src.get("floor_from") is not None or src.get("floor_to") is not None:
        skipped.append("Patro od–do")
    dst["flags"] = ["includeImports", "includeShortTerm"]
    dst["roommate"] = [""]
    return dst, skipped, notes


def br_to_sr(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = url_builder.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    reverse_offers = {value: key for key, value in SR_OFFERS.items()}
    dst["offers"] = _map_list(src.get("offers"), reverse_offers, {"PRONAJEM": "Pronájem", "PRODEJ": "Prodej"}, skipped) or ["pronajem"]
    estates = src.get("estates") or ["BYT"]
    if any(item != "BYT" for item in estates):
        skipped.extend(f"Typ nemovitosti {BR_ESTATE_LABELS.get(item, item)}" for item in estates if item != "BYT")
    dst["category"] = "byty"
    reverse_sizes = {value: key for key, value in SR_SIZES.items()}
    sizes: list[str] = []
    for item in src.get("sizes") or []:
        if item in {"DISP_6_1", "DISP_6_KK", "DISP_7_1", "DISP_7_KK"}:
            sizes.append("6-a-vice")
            notes.append(f"{BR_SIZE_LABELS.get(item, item)} → 6 pokojů a více")
        elif item in reverse_sizes:
            sizes.append(reverse_sizes[item])
            if item == "GARSONIERA":
                notes.append("Garsoniéra → pokoj / spolubydlení")
            if item == "OSTATNI":
                notes.append("Ostatní dispozice → atypický")
        elif item:
            skipped.append(f"Dispozice {BR_SIZE_LABELS.get(item, item)}")
    dst["sizes"] = list(dict.fromkeys(sizes))
    districts: list[str] = []
    for item in src.get("districts") or []:
        if item == localities.CZECH_OSM or localities.is_czech_country(item, BR_DISTRICT_LABELS.get(item, item)):
            districts.extend(localities.SREALITY_CZECH_REGIONS)
        else:
            slug = localities.OSM_TO_SREALITY.get(item) or BR_DISTRICTS.get(item)
            if slug and "," not in slug:
                districts.append(slug)
            elif item:
                guess = localities.sreality_slug(item, BR_DISTRICT_LABELS.get(item, item))
                if guess and "," not in guess:
                    districts.append(guess)
                elif "," in str(guess):
                    districts.extend(part for part in guess.split(",") if part)
                else:
                    skipped.append(f"Lokalita {BR_DISTRICT_LABELS.get(item, item)}")
    dst["districts"] = list(dict.fromkeys(districts))
    reverse_own = {value: key for key, value in SR_OWNERSHIP.items()}
    dst["ownership"] = _map_list(src.get("ownership"), reverse_own, BR_OWNERSHIP_LABELS, skipped)
    if "OSTATNI" in (src.get("ownership") or []):
        skipped.append("Vlastnictví ostatní")
    reverse_cond = {value: key for key, value in SR_CONDITIONS.items()}
    conditions: list[str] = []
    for item in src.get("conditions") or []:
        if item == "AFTER_PARTIAL_RECONSTRUCTION":
            conditions.append("po-rekonstrukci")
            notes.append("Po částečné rekonstrukci → po rekonstrukci")
        elif item in reverse_cond:
            conditions.append(reverse_cond[item])
        elif item:
            skipped.append(f"Stav {BR_CONDITION_LABELS.get(item, item)}")
    dst["conditions"] = list(dict.fromkeys(conditions))
    buildings: list[str] = []
    for item in src.get("buildings") or []:
        if item == "BRICK":
            buildings.append("cihlova")
        elif item == "PANEL":
            buildings.append("panelova")
        elif item in {"MIXED", "PREFAB", "SKELET", "STONE"}:
            buildings.append("ostatni")
            notes.append(f"Konstrukce {BR_BUILDING_LABELS.get(item, item)} → ostatní")
        elif item:
            skipped.append(f"Konstrukce {BR_BUILDING_LABELS.get(item, item)}")
    dst["buildings"] = list(dict.fromkeys(buildings))
    reverse_extras = {value: key for key, value in SR_EXTRAS.items()}
    extras: list[str] = []
    for item in src.get("extras") or []:
        if item in reverse_extras:
            extras.append(reverse_extras[item])
        elif item in {"petFriendly", "searchPriceWithCharges"}:
            skipped.append(BR_EXTRA_LABELS.get(item, item))
        elif item:
            skipped.append(f"{BR_EXTRA_LABELS.get(item, item)}")
    if src.get("balcony_from") is not None or src.get("balcony_to") is not None:
        extras.append("balkon")
        notes.append("Plocha balkónu → jen „má balkón“")
    if src.get("loggia_from") is not None or src.get("loggia_to") is not None:
        extras.append("lodzie")
        notes.append("Plocha lodžie → jen „má lodžii“")
    if src.get("cellar_from") is not None or src.get("cellar_to") is not None:
        extras.append("sklep")
        notes.append("Plocha sklepa → jen „má sklep“")
    if src.get("terrace_from") is not None or src.get("terrace_to") is not None:
        extras.append("terasa")
        notes.append("Plocha terasy → jen „má terasu“")
    if src.get("garden_from") is not None or src.get("garden_to") is not None:
        extras.append("zahrada")
        notes.append("Předzahrádka → zahrada")
    dst["extras"] = list(dict.fromkeys(extras))
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    dst["sort"] = "nejnovejsi"
    _skip_if(skipped, src.get("transfers"), "Převod do osobního vlastnictví")
    _skip_if(skipped, src.get("equipped"), "Vybavenost")
    extra_flags = [item for item in (src.get("flags") or []) if item == "discountedOnly"]
    if extra_flags:
        skipped.append("Pouze zlevněné")
    roommate = (src.get("roommate") or [""])[0]
    if roommate in {"true", "false"}:
        skipped.append("Spolubydlení (jen / bez)")
    if (src.get("currency") or "CZK") != "CZK":
        skipped.append("Měna EUR")
    if src.get("neighborhood"):
        skipped.append("Okolí v km")
    if src.get("available_from"):
        skipped.append("Dostupné od")
    if src.get("advert_id"):
        skipped.append("Číslo inzerátu")
    if src.get("boundary_points"):
        skipped.append("Oblast z mapy")
    if src.get("annuity_from") is not None or src.get("annuity_to") is not None:
        skipped.append("Anuita")
    return dst, skipped, notes


def _map_list(values: list | None, mapping: dict[str, str], labels: dict[str, str], skipped: list[str]) -> list[str]:
    result: list[str] = []
    for item in values or []:
        if item in mapping:
            result.append(mapping[item])
        elif item:
            skipped.append(labels.get(item, item))
    return list(dict.fromkeys(result))


def _skip_if(skipped: list[str], values, label: str) -> None:
    if values:
        skipped.append(label)


def other_portal_name(url: str) -> str:
    if is_idnes(url) or is_bazos(url):
        return "Sreality"
    return "Sreality" if is_bezrealitky(url) else "Bezrealitky"


SR_TO_IDNES_SIZE = {
    "1+1": "1-1",
    "1+kk": "1-kk",
    "2+1": "2-1",
    "2+kk": "2-kk",
    "3+1": "3-1",
    "3+kk": "3-kk",
    "4+1": "4-1",
    "4+kk": "4-kk",
    "5+1": "5-1",
    "5+kk": "5-kk",
    "6-a-vice": "6-kk-a-vetsi",
    "atypicky": "atypicke",
    "pokoj": "pokoj",
}
IDNES_TO_SR_SIZE = {value: key for key, value in SR_TO_IDNES_SIZE.items()}
SR_TO_IDNES_OFFER = {"pronajem": "pronajem", "prodej": "prodej", "drazby": "drazba"}
IDNES_TO_SR_OFFER = {"pronajem": "pronajem", "prodej": "prodej", "drazba": "drazby"}
SR_TO_IDNES_COND = {
    "novostavby": "novostavba",
    "developerske-projekty": "projekt",
    "ve-vystavbe": "ve-vystavbe",
    "dobry-stav": "dobry-stav",
    "velmi-dobry-stav": "dobry-stav",
    "spatny-stav": "spatny-stav",
    "po-rekonstrukci": "po-rekonstrukci",
    "v-rekonstrukci": "v-rekonstrukci",
    "pred-rekonstrukci": "pred-rekonstrukci",
    "k-demolici": "k-demolici",
}
IDNES_TO_SR_COND = {
    "novostavba": "novostavby",
    "projekt": "developerske-projekty",
    "ve-vystavbe": "ve-vystavbe",
    "dobry-stav": "dobry-stav",
    "udrzovany": "dobry-stav",
    "spatny-stav": "spatny-stav",
    "po-rekonstrukci": "po-rekonstrukci",
    "v-rekonstrukci": "v-rekonstrukci",
    "pred-rekonstrukci": "pred-rekonstrukci",
    "k-demolici": "k-demolici",
}
SR_TO_IDNES_OWN = {"osobni": "osobni", "druzstevni": "druzstevni", "statni-obecni": "jine"}
IDNES_TO_SR_OWN = {"osobni": "osobni", "druzstevni": "druzstevni"}
SR_TO_IDNES_BUILD = {"cihlova": "cihlova", "panelova": "panelova"}
IDNES_TO_SR_BUILD = {
    "cihlova": "cihlova",
    "panelova": "panelova",
    "drevena": "ostatni",
    "kamenna": "ostatni",
    "skeletova": "ostatni",
    "montovana": "ostatni",
    "smisena": "ostatni",
}
IDNES_TO_BR_ESTATE = {
    "byty": ["BYT"],
    "domy": ["DUM"],
    "pozemky": ["POZEMEK"],
    "komercni": ["KANCELAR", "NEBYTOVY_PROSTOR"],
    "komercni-nemovitosti": ["KANCELAR", "NEBYTOVY_PROSTOR"],
    "male-objekty-garaze": ["GARAZ"],
}
IDNES_TO_BR_BUILD = {
    "cihlova": ["BRICK"],
    "panelova": ["PANEL"],
    "kamenna": ["STONE"],
    "skeletova": ["SKELET"],
    "montovana": ["PREFAB"],
    "smisena": ["MIXED"],
}
SHARED_EXTRAS = {
    "balkon",
    "lodzie",
    "terasa",
    "zahrada",
    "sklep",
    "garaz",
    "parkovani",
    "vytah",
    "bezbarierovy",
}


def to_sreality_filters(url: str) -> dict[str, Any]:
    extra = EXTRA_URLS.get(portal_of(url))
    if extra:
        parsed = extra.parse_url(url)
        filters = url_builder.default_filters()
        filters["offers"] = parsed.get("offers") or ["pronajem"]
        filters["districts"] = parsed.get("districts") or []
        filters["sizes"] = parsed.get("sizes") or []
        filters["price_from"] = parsed.get("price_from")
        filters["price_to"] = parsed.get("price_to")
        filters["area_from"] = parsed.get("area_from")
        filters["area_to"] = parsed.get("area_to")
    elif is_idnes(url):
        filters, _, _ = idnes_to_sr(idnes_url.parse_url(url))
    elif is_bazos(url):
        filters, _, _ = bazos_to_sr(bazos_url.parse_url(url))
    elif is_bezrealitky(url):
        filters, _, _ = br_to_sr(bezrealitky_url.parse_url(url))
    else:
        filters = url_builder.parse_url(url)
    filters["source"] = "sreality"
    return localities.normalize_filters(filters)


def search_urls_for_portals(url: str) -> dict[str, str]:
    from app.catalog_sync import normalize_search_url

    primary = portal_of(url)
    canonical = to_sreality_filters(url)
    built: dict[str, str] = {}
    try:
        built["sreality"] = normalize_search_url(url_builder.build_url(canonical))
    except Exception:
        built["sreality"] = ""
    try:
        br_filters, _, _ = sr_to_br(canonical)
        br_filters["source"] = "bezrealitky"
        built["bezrealitky"] = normalize_search_url(bezrealitky_url.build_url(localities.normalize_filters(br_filters)))
    except Exception:
        built["bezrealitky"] = ""
    try:
        id_filters, _, _ = sr_to_idnes(canonical)
        id_filters["source"] = "idnes"
        built["idnes"] = normalize_search_url(idnes_url.build_url(id_filters))
    except Exception:
        built["idnes"] = ""
    try:
        bz_filters, _, _ = sr_to_bazos(canonical)
        bz_filters["source"] = "bazos"
        built["bazos"] = normalize_search_url(bazos_url.build_url(bz_filters))
    except Exception:
        built["bazos"] = ""
    for portal_id, urls in EXTRA_URLS.items():
        try:
            extra_filters = {
                "offers": canonical.get("offers") or ["pronajem"],
                "category": canonical.get("category") or "byty",
                "districts": canonical.get("districts") or [],
                "sizes": canonical.get("sizes") or [],
                "price_from": canonical.get("price_from"),
                "price_to": canonical.get("price_to"),
                "area_from": canonical.get("area_from"),
                "area_to": canonical.get("area_to"),
            }
            built[portal_id] = normalize_search_url(urls.build_url(extra_filters))
        except Exception:
            built[portal_id] = ""
    original = normalize_search_url(url)
    if original:
        built[primary] = original
    return {key: value for key, value in built.items() if value}


def sr_to_idnes(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = idnes_url.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    offers = _map_list(src.get("offers"), SR_TO_IDNES_OFFER, SR_OFFER_LABELS | {"pronajem": "Pronájem", "prodej": "Prodej"}, skipped) or ["pronajem"]
    dst["offers"] = offers[:1]
    if len(offers) > 1:
        notes.append("iDNES bere jen jeden typ nabídky — použije se první")
    category = src.get("category") or "byty"
    dst["category"] = idnes_url._category(category)
    dst["sizes"] = _map_list(src.get("sizes"), SR_TO_IDNES_SIZE, SR_SIZE_LABELS, skipped)
    dst["districts"] = [str(item) for item in (src.get("districts") or []) if item]
    dst["ownership"] = _map_list(src.get("ownership"), SR_TO_IDNES_OWN, SR_OWNERSHIP_LABELS, skipped)
    dst["conditions"] = _map_list(src.get("conditions"), SR_TO_IDNES_COND, SR_CONDITION_LABELS, skipped)
    buildings: list[str] = []
    for item in src.get("buildings") or []:
        if item == "ostatni":
            buildings.extend(["drevena", "kamenna", "skeletova", "montovana", "smisena"])
            notes.append("Konstrukce ostatní → dřevěná / kamenná / skeletová / montovaná / smíšená")
        elif item in SR_TO_IDNES_BUILD:
            buildings.append(SR_TO_IDNES_BUILD[item])
        elif item in {key for key, _ in idnes_url.BUILDINGS}:
            buildings.append(item)
        elif item:
            skipped.append(f"Konstrukce {item}")
    dst["buildings"] = list(dict.fromkeys(buildings))
    extras: list[str] = []
    for item in src.get("extras") or []:
        if item in SHARED_EXTRAS:
            extras.append(item)
        elif item:
            skipped.append(SR_EXTRA_LABELS.get(item, item))
    dst["extras"] = extras
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    dst["sort"] = "nejnovejsi"
    if src.get("energy"):
        skipped.append("Energetická náročnost")
    if src.get("pois"):
        skipped.append("V okolí nemovitosti")
    if src.get("floor_from") is not None or src.get("floor_to") is not None:
        skipped.append("Patro od–do")
    return dst, skipped, notes


def idnes_to_sr(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = url_builder.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    dst["offers"] = _map_list(src.get("offers"), IDNES_TO_SR_OFFER, {"pronajem": "Pronájem", "prodej": "Prodej", "drazba": "Dražba"}, skipped) or ["pronajem"]
    category = src.get("category") or "byty"
    if category != "byty":
        skipped.append(f"Typ nemovitosti „{category}“ na Sreality v tomto hlídači zůstane u bytů")
    dst["category"] = "byty"
    dst["sizes"] = _map_list(src.get("sizes"), IDNES_TO_SR_SIZE, {key: label for key, label in idnes_url.SIZES}, skipped)
    dst["districts"] = [str(item) for item in (src.get("districts") or []) if item]
    own = []
    for item in src.get("ownership") or []:
        if item in IDNES_TO_SR_OWN:
            own.append(IDNES_TO_SR_OWN[item])
        elif item:
            skipped.append({"s-r-o": "S.r.o.", "podilove": "Podílové", "jine": "Jiné"}.get(item, item))
    dst["ownership"] = list(dict.fromkeys(own))
    cond = []
    for item in src.get("conditions") or []:
        if item in IDNES_TO_SR_COND:
            cond.append(IDNES_TO_SR_COND[item])
            if item == "udrzovany":
                notes.append("Udržovaný → dobrý stav")
        elif item:
            skipped.append(item)
    dst["conditions"] = list(dict.fromkeys(cond))
    dst["buildings"] = _map_list(src.get("buildings"), IDNES_TO_SR_BUILD, {key: label for key, label in idnes_url.BUILDINGS}, skipped)
    extras = [item for item in (src.get("extras") or []) if item in SHARED_EXTRAS]
    for item in src.get("extras") or []:
        if item not in SHARED_EXTRAS and item:
            skipped.append({"telefon": "Telefon", "kabelova-tv": "Kabelová televize", "internet": "Internet"}.get(item, item))
    dst["extras"] = extras
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    dst["sort"] = "nejnovejsi"
    if src.get("equipped"):
        skipped.append("Vybavenost (zařízený / částečně / nezařízený)")
    if src.get("flags"):
        notes.append("Video / zlevněno / den otevřených dveří zůstane jen na iDNES")
    if src.get("article_age"):
        skipped.append("Aktuálnost inzerátu")
    return dst, skipped, notes


SR_TO_BAZOS_CAT = {
    "byty": "byt",
    "domy": "dum",
    "pozemky": "pozemek",
    "komercni": "kancelar",
    "komercni-nemovitosti": "kancelar",
    "male-objekty-garaze": "garaz",
}
BAZOS_TO_SR_CAT = {
    "byt": "byty",
    "dum": "domy",
    "pozemek": "pozemky",
    "garaz": "male-objekty-garaze",
    "kancelar": "komercni",
    "prostory": "komercni",
    "projekty": "byty",
    "chata": "domy",
    "podnajem": "byty",
}


def sr_to_bazos(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = bazos_url.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    offers = [item for item in (src.get("offers") or []) if item in {"pronajem", "prodej"}]
    dst["offers"] = offers[:1] or ["pronajem"]
    if len(offers) > 1:
        notes.append("Bazoš bere jen jeden typ nabídky — použije se první")
    category = src.get("category") or "byty"
    dst["category"] = SR_TO_BAZOS_CAT.get(category) or bazos_url._category(category)
    sizes: list[str] = []
    for size in src.get("sizes") or []:
        if size in bazos_url.SIZE_KEYS:
            sizes.append(size)
            continue
        needle = bazos_url.SIZE_QUERY.get(size)
        match = next((key for key in bazos_url.SIZE_KEYS if needle and bazos_url.SIZE_QUERY.get(key) == needle), None)
        if match:
            sizes.append(match)
        elif size:
            skipped.append(SR_SIZE_LABELS.get(size, size))
    dst["sizes"] = list(dict.fromkeys(sizes))
    if len(dst["sizes"]) > 1:
        notes.append("Bazoš do URL vloží dispozici jen když je jedna — více velikostí dopočítáme z inzerátů")
    dst["districts"] = [str(item) for item in (src.get("districts") or []) if item]
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    try:
        dst["radius"] = int(src["radius"]) if src.get("radius") not in (None, "") else dst["radius"]
    except (TypeError, ValueError):
        pass
    _skip_if(skipped, src.get("ownership"), "Typ vlastnictví")
    _skip_if(skipped, src.get("conditions"), "Stav budovy")
    _skip_if(skipped, src.get("buildings"), "Konstrukce budovy")
    _skip_if(skipped, src.get("extras"), "Vybavení (balkón, výtah, …)")
    _skip_if(skipped, src.get("flags"), "Další filtry Sreality")
    if skipped:
        notes.append("Bazoš tyto filtry v URL nemá — u inzerátů je dopočítáme z textu, kde to jde")
    return dst, skipped, notes


def bazos_to_sr(src: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    dst = url_builder.default_filters()
    skipped: list[str] = []
    notes: list[str] = []
    dst["offers"] = [item for item in (src.get("offers") or ["pronajem"]) if item in {"pronajem", "prodej"}][:1] or ["pronajem"]
    category = src.get("category") or "byt"
    dst["category"] = BAZOS_TO_SR_CAT.get(category, "byty")
    if category not in {"byt", "dum"}:
        notes.append(f"Typ „{category}“ na Sreality mapujeme na {dst['category']}")
    dst["sizes"] = [item for item in (src.get("sizes") or []) if item in SR_SIZES or item in SR_SIZE_EXPAND]
    dst["districts"] = [str(item) for item in (src.get("districts") or []) if item]
    dst["price_from"] = src.get("price_from")
    dst["price_to"] = src.get("price_to")
    dst["area_from"] = src.get("area_from")
    dst["area_to"] = src.get("area_to")
    dst["sort"] = "nejnovejsi"
    if src.get("hledat") and not dst["sizes"]:
        notes.append("Volný text z Bazoše na Sreality nepřeneseme")
    return dst, skipped, notes
