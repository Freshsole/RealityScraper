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
        offer = _offer(filters)
        districts = [str(item) for item in (filters.get("districts") or []) if item]
        region = districts[0] if len(districts) == 1 else ""
        path = f"/{offer}/byty/"
        if region and re_slug(region):
            path = f"/{offer}/byty/{re_slug(region)}/"
        elif (filters.get("sort") or "nejnovejsi") == "nejnovejsi":
            path = f"/{offer}/byty/nejnovejsi/"
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
        category = str(filters.get("category") or "byty").casefold()
        if "dom" in category:
            path = "/domy-k-pronajmu.html" if offer == "pronajem" else "/domy-na-prodej.html"
        else:
            path = "/byty-k-pronajmu.html" if offer == "pronajem" else "/byty-na-prodej.html"
        # nabidkovy=1 drops poptávka cards so page-1 is 20 offers, not a mix of 10.
        return _join(self.site + path, {"nabidkovy": "1"})

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        if "prodej" in raw:
            filters["offers"] = ["prodej"]
        else:
            filters["offers"] = ["pronajem"]
        if "domy" in raw:
            filters["category"] = "domy"
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
        query = {
            "typ-nabidky": "pronajem" if offer == "pronajem" else "prodej",
            "typ-nemovitosti": "byt",
            "razeni": "nejnovejsi",
        }
        if filters.get("price_from"):
            query["cena-od"] = str(int(filters["price_from"]))
        if filters.get("price_to"):
            query["cena-do"] = str(int(filters["price_to"]))
        return _join(self.site + "/nemovitosti/", query)

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
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
        offer_path = "pronajem" if offer == "pronajem" else "prodej"
        category = str(filters.get("category") or "byty").casefold()
        kind = "domy-a-vily" if "dom" in category else "byty"
        query: dict[str, str] = {}
        if (filters.get("sort") or "nejnovejsi") == "nejnovejsi":
            query["order_by_published_date"] = "0"
        return _join(self.site + f"/reality/{kind}/{offer_path}/", query)

    def parse_url(self, url: str) -> dict:
        filters = default_filters(self.source)
        query = dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))
        raw = (url or "").lower()
        if query.get("sale") == "1" or "/prodej" in raw:
            filters["offers"] = ["prodej"]
        else:
            filters["offers"] = ["pronajem"]
        if "domy" in raw or "vily" in raw:
            filters["category"] = "domy"
        else:
            filters["category"] = "byty"
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
        category = str(filters.get("category") or "byty").casefold()
        kind = "domy" if "dom" in category else "byty"
        districts = [str(item) for item in (filters.get("districts") or []) if item]
        region = districts[0] if len(districts) == 1 else ""
        path = f"/{offer}/{kind}/{region}/" if region else f"/{offer}/{kind}/Ceska-republika/"
        query: dict[str, str] = {}
        if (filters.get("sort") or "nejnovejsi") == "nejnovejsi":
            query["s"] = "2"
        return _join(self.site + path, query)

    def parse_url(self, url: str) -> dict:
        filters = _parse_common(url, self.source)
        raw = (url or "").lower()
        if "/domy/" in raw or "/dum/" in raw:
            filters["category"] = "domy"
        else:
            filters["category"] = "byty"
        return filters


def re_slug(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"praha", "brno", "ostrava", "plzen", "olomouc"}:
        return raw
    if raw.startswith("praha-") and raw.split("-")[-1].isdigit():
        return raw
    return ""


MODULES = {
    "ceskereality": CeskerealityUrls(),
    "annonce": AnnonceUrls(),
    "mmreality": MmrealityUrls(),
    "ulovdomov": UlovdomovUrls(),
    "remax": RemaxUrls(),
    "realitycz": RealityczUrls(),
}

ceskereality_url = MODULES["ceskereality"]
annonce_url = MODULES["annonce"]
mmreality_url = MODULES["mmreality"]
ulovdomov_url = MODULES["ulovdomov"]
remax_url = MODULES["remax"]
realitycz_url = MODULES["realitycz"]
