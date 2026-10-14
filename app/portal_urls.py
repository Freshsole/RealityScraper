"""URL builders for the extra Czech portals (ČeskéReality, Annonce, M&M, UlovDomov, RE/MAX, Reality.cz)."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.url_builder import SIZES as SR_SIZES

OFFERS = [
    ("pronajem", "Pronájem"),
    ("prodej", "Prodej"),
]
CATEGORIES = [("byty", "Byty")]
SIZES = list(SR_SIZES)


def _int_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def default_filters(source: str) -> dict:
    return {
        "source": source,
        "offers": ["pronajem"],
        "category": "byty",
        "sizes": [],
        "districts": [],
        "price_from": None,
        "price_to": None,
        "area_from": None,
        "area_to": None,
        "sort": "nejnovejsi",
    }


def sample_filters(source: str) -> dict:
    data = default_filters(source)
    data["offers"] = ["pronajem", "prodej"]
    data["sizes"] = [key for key, _ in SIZES[:8]]
    data["districts"] = ["praha"]
    return data


def catalog() -> dict:
    return {"offers": OFFERS, "categories": CATEGORIES, "sizes": SIZES, "sorts": [("nejnovejsi", "Nejnovější")]}


def _offer(filters: dict, default: str = "pronajem") -> str:
    offers = [str(item).casefold() for item in (filters.get("offers") or []) if item]
    for item in offers:
        if "pronaj" in item:
            return "pronajem"
        if "prodej" in item or item == "prodam":
            return "prodej"
    return default


def _join(url: str, query: dict[str, str]) -> str:
    split = urlsplit(url)
    existing = dict(parse_qsl(split.query, keep_blank_values=True))
    existing.update({key: value for key, value in query.items() if value not in (None, "")})
    return urlunsplit((split.scheme or "https", split.netloc, split.path, urlencode(existing, doseq=True), ""))


def _parse_common(url: str, source: str) -> dict:
    filters = default_filters(source)
    raw = (url or "").lower()
    if "prodej" in raw or "prodam" in raw or "na-prodej" in raw or "sale=1" in raw:
        filters["offers"] = ["prodej"]
    else:
        filters["offers"] = ["pronajem"]
    split = urlsplit(url or "")
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    filters["price_from"] = _int_or_none(query.get("cena-od") or query.get("price_from") or query.get("priceFrom"))
    filters["price_to"] = _int_or_none(query.get("cena-do") or query.get("price_to") or query.get("priceTo"))
    parts = [part for part in split.path.split("/") if part]
    for part in parts:
        if part.startswith("praha"):
            filters["districts"] = [part]
            break
    return filters


# Site slugs for region filters (sreality-style key -> ceskereality path segment). Verified live 2026-10-14.
CESKEREALITY_REGIONS = {
    "praha": "hlavni-mesto-praha",
    "stredocesky-kraj": "stredocesky-kraj",
    "jihocesky-kraj": "jihocesky-kraj",
    "plzensky-kraj": "plzensky-kraj",
    "karlovarsky-kraj": "karlovarsky-kraj",
    "ustecky-kraj": "ustecky-kraj",
    "liberecky-kraj": "liberecky-kraj",
    "kralovehradecky-kraj": "kralovehradecky-kraj",
    "pardubicky-kraj": "pardubicky-kraj",
    "vysocina-kraj": "kraj-vysocina",
    "jihomoravsky-kraj": "jihomoravsky-kraj",
    "olomoucky-kraj": "olomoucky-kraj",
    "zlinsky-kraj": "zlinsky-kraj",
    "moravskoslezsky-kraj": "moravskoslezsky-kraj",
}
# Disposition path segments (/pronajem/byty/<segment>/). "6-a-vice"/"atypicky" have no category page.
CESKEREALITY_SIZES = {
    "1+1": "byty-1-1",
    "1+kk": "byty-1-kk",
    "2+1": "byty-2-1",
    "2+kk": "byty-2-kk",
    "3+1": "byty-3-1",
    "3+kk": "byty-3-kk",
    "4+1": "byty-4-1",
    "4+kk": "byty-4-kk",
    "5+kk": "byty-5-kk",
    "5+1": "byty-5-1-vetsi",
    "pokoj": "pokoje",
}


class CeskerealityUrls:
    source = "ceskereality"
    site = "https://www.ceskereality.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        """``/<offer>/byty/[<disposition>/][<region>/]`` - nationwide lists stop at ~12 pages (240 ads),
        region (and region x disposition) lists page through everything the header count promises."""
        offer = _offer(filters)
        districts = [str(item) for item in (filters.get("districts") or []) if item]
        region = districts[0] if len(districts) == 1 else ""
        sizes = [str(item) for item in (filters.get("sizes") or []) if item]
        parts = ["", offer, "byty"]
        size_slug = CESKEREALITY_SIZES.get(sizes[0]) if len(sizes) == 1 else None
        if size_slug:
            parts.append(size_slug)
        slug = CESKEREALITY_REGIONS.get(region) or (re_slug(region) if region else "")
        if slug:
            # Site canonical slug for Prague (avoids redirect).
            parts.append("hlavni-mesto-praha" if slug == "praha" else slug)
        path = "/".join(parts) + "/"
        query: dict[str, str] = {}
        if filters.get("price_from"):
            query["cena-od"] = str(int(filters["price_from"]))
        if filters.get("price_to"):
            query["cena-do"] = str(int(filters["price_to"]))
        return _join(self.site + path, query)

    def parse_url(self, url: str) -> dict:
        return _parse_common(url, self.source)


class AnnonceUrls:
    source = "annonce"
    site = "https://www.annonce.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        path = "/byty-k-pronajmu.html" if offer == "pronajem" else "/byty-na-prodej.html"
        return self.site + path

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        if "prodej" in raw:
            filters["offers"] = ["prodej"]
        else:
            filters["offers"] = ["pronajem"]
        return filters


class MmrealityUrls:
    source = "mmreality"
    site = "https://www.mmreality.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        slug = "pronajem" if offer == "pronajem" else "prodej"
        # Path-based since the 2026 redesign; query params are ignored by the site.
        # "/byty/" matters: without it the list also holds houses, garages, land, offices.
        return f"{self.site}/nemovitosti/{slug}/byty/"

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        path = (urlsplit(url or "").path or "").casefold()
        if "/pronajem" in path:
            filters["offers"] = ["pronajem"]
        elif "/prodej" in path:
            filters["offers"] = ["prodej"]
        else:
            # Legacy query params (pre-2026 site).
            query = dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))
            kind = (query.get("typ-nabidky") or "").casefold()
            if "prodej" in kind:
                filters["offers"] = ["prodej"]
            elif "pronaj" in kind:
                filters["offers"] = ["pronajem"]
        return filters


class UlovdomovUrls:
    source = "ulovdomov"
    site = "https://www.ulovdomov.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        return f"{self.site}/{offer}/byty"

    def parse_url(self, url: str) -> dict:
        return _parse_common(url, self.source)


class RemaxUrls:
    source = "remax"
    site = "https://www.remax-czech.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        # Canonical path (the old ``?sale=&order_by_price=0`` form redirects and
        # order_by_price=0 means "most expensive first", not newest). Default
        # order is newest; ``?stranka=N`` pages, 21 cards/page.
        return f"{self.site}/reality/byty/{'pronajem' if offer == 'pronajem' else 'prodej'}/"

    def parse_url(self, url: str) -> dict:
        filters = default_filters(self.source)
        query = dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))
        if query.get("sale") == "1" or "/prodej" in (url or "").lower():
            filters["offers"] = ["prodej"]
        else:
            filters["offers"] = ["pronajem"]
        return filters


class RealityczUrls:
    source = "realitycz"
    site = "https://www.reality.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        # Locality listing pages are server-rendered with offers in HTML.
        # The bare /{offer}/byty/ is only a catalogue homepage (carousel),
        # not a real result list.
        # Without a single explicit locality use the nationwide list
        # (/Ceska-republika/, paged with ?g=N). For sale the nationwide list is
        # capped (~1 500 of ~1 780), so catalog sync should use REGION_SLUGS.
        districts = [str(item) for item in (filters.get("districts") or []) if item]
        region = districts[0] if len(districts) == 1 else ""
        locality = "Ceska-republika"
        if region:
            slug = re_slug(region)
            if slug:
                # reality.cz uses obvod-Praha-N / okres-... slugs; map plain
                # "praha" to the whole-city listing, keep explicit slugs.
                locality = slug if slug != "praha" else "hlavni-mesto-Praha"
        return f"{self.site}/{offer}/byty/{locality}/"

    def parse_url(self, url: str) -> dict:
        return _parse_common(url, self.source)


#: reality.cz kraj slugs (14 regions); sum of region counts = complete market.
REALITYCZ_REGION_SLUGS = (
    "hlavni-mesto-Praha", "Stredocesky-kraj", "Jihocesky-kraj", "Plzensky-kraj",
    "Karlovarsky-kraj", "Ustecky-kraj", "Liberecky-kraj", "Kralovehradecky-kraj",
    "Pardubicky-kraj", "kraj-Vysocina", "Jihomoravsky-kraj", "Olomoucky-kraj",
    "Zlinsky-kraj", "Moravskoslezsky-kraj",
)


def re_slug(value: str) -> str:
    raw = (value or "").strip().lower()
    for region in REALITYCZ_REGION_SLUGS:
        if raw == region.lower():
            return region
    if raw in {"praha", "brno", "ostrava", "plzen", "olomouc"}:
        return raw
    if raw.startswith("praha-") and raw.split("-")[-1].isdigit():
        return raw
    return ""


class EurobydleniUrls:
    source = "eurobydleni"
    site = "https://www.eurobydleni.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        path = "pronajem" if offer == "pronajem" else "prodej"
        # Nationwide list (all of CZ, newest first by sort_date, 12/page, /page-N/).
        return f"{self.site}/byty/{path}/"

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        filters["offers"] = ["prodej"] if "prodej" in raw else ["pronajem"]
        return filters


class RealitymixUrls:
    source = "realitymix"
    site = "https://realitymix.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        path = "pronajem" if offer == "pronajem" else "prodej"
        # Nationwide list, default order "Nejnovější", 20/page, ?stranka=N.
        return f"{self.site}/reality/byty/{path}"

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        filters["offers"] = ["prodej"] if "prodej" in raw else ["pronajem"]
        return filters


class RealingoUrls:
    source = "realingo"
    site = "https://www.realingo.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        offer = _offer(filters)
        # "_byty" (not "_reality" = every property type, 39k rent / 100k sale incl. houses, land, commercial).
        path = "pronajem_byty" if offer == "pronajem" else "prodej_byty"
        return f"{self.site}/{path}/cr/"

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        filters["offers"] = ["prodej"] if "prodej" in raw else ["pronajem"]
        return filters


class EspolubydleniUrls:
    source = "espolubydleni"
    site = "https://www.espolubydleni.cz"

    def catalog(self) -> dict:
        return catalog()

    def default_filters(self) -> dict:
        return default_filters(self.source)

    def sample_filters(self) -> dict:
        return sample_filters(self.source)

    def build_url(self, filters: dict) -> str:
        return f"{self.site}/podnajem-spolubydlici/"

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        filters["offers"] = ["pronajem"]
        return filters


MODULES = {
    "ceskereality": CeskerealityUrls(),
    "annonce": AnnonceUrls(),
    "mmreality": MmrealityUrls(),
    "ulovdomov": UlovdomovUrls(),
    "remax": RemaxUrls(),
    "realitycz": RealityczUrls(),
    "eurobydleni": EurobydleniUrls(),
    "realitymix": RealitymixUrls(),
    "realingo": RealingoUrls(),
    "espolubydleni": EspolubydleniUrls(),
}

ceskereality_url = MODULES["ceskereality"]
annonce_url = MODULES["annonce"]
mmreality_url = MODULES["mmreality"]
ulovdomov_url = MODULES["ulovdomov"]
remax_url = MODULES["remax"]
realitycz_url = MODULES["realitycz"]
eurobydleni_url = MODULES["eurobydleni"]
realitymix_url = MODULES["realitymix"]
realingo_url = MODULES["realingo"]
espolubydleni_url = MODULES["espolubydleni"]
