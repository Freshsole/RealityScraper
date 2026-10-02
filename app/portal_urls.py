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
            slug = re_slug(region)
            # Site canonical slug for Prague (avoids redirect).
            if slug == "praha":
                slug = "praha-hlavni-mesto"
            path = f"/{offer}/byty/{slug}/"
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
        return f"{self.site}/nemovitosti/{slug}/"

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
        sale = "2" if offer == "pronajem" else "1"
        return _join(self.site + "/reality/byty/", {"sale": sale, "order_by_price": "0"})

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
        districts = [str(item) for item in (filters.get("districts") or []) if item]
        region = districts[0] if len(districts) == 1 else ""
        locality = "hlavni-mesto-Praha"
        if region:
            slug = re_slug(region)
            if slug:
                # reality.cz uses obvod-Praha-N / okres-... slugs; map plain
                # "praha" to the whole-city listing, keep explicit slugs.
                locality = slug if slug != "praha" else "hlavni-mesto-Praha"
        return f"{self.site}/{offer}/byty/{locality}/"

    def parse_url(self, url: str) -> dict:
        return _parse_common(url, self.source)


def re_slug(value: str) -> str:
    raw = (value or "").strip().lower()
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
        return f"{self.site}/byty/praha/{path}/"

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
        return f"{self.site}/reality/byty/{path}/praha"

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
        path = "pronajem_reality" if offer == "pronajem" else "prodej_reality"
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
