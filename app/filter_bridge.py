from __future__ import annotations

from typing import Any

from app import bezrealitky_url, url_builder
from app.sources import is_bezrealitky, source_name

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
    if is_bezrealitky(url):
        filters, skipped, notes = br_to_sr(bezrealitky_url.parse_url(url))
        target_url = url_builder.build_url(filters)
        target = "Sreality"
    else:
        filters, skipped, notes = sr_to_br(url_builder.parse_url(url))
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
    districts = [SR_DISTRICTS[item] for item in src.get("districts") or [] if item in SR_DISTRICTS]
    for item in src.get("districts") or []:
        if item not in SR_DISTRICTS:
            skipped.append(f"Lokalita {item}")
    dst["districts"] = districts
    dst["osm_value"] = ", ".join(BR_DISTRICT_LABELS.get(item, item) for item in districts)
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
        if item == "R435514":
            districts.extend(f"praha-{i}" for i in range(1, 11))
            notes.append("Celá Praha → Praha 1–10")
        elif item in BR_DISTRICTS:
            districts.append(BR_DISTRICTS[item])
        elif item:
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
    return "Sreality" if is_bezrealitky(url) else "Bezrealitky"
