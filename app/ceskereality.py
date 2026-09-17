"""ČeskéReality list/detail scraper (HTML cards + image IDs)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price, with_page
from app.portal_urls import ceskereality_url
from app.sreality import Listing

SITE = ceskereality_url.site
CARD_RE = re.compile(r'(<article[^>]*class="[^"]*i-estate[^"]*"[^>]*>.*?</article>)', re.S | re.I)
CARD2_RE = re.compile(r'(class="[^"]*i-estate[^"]*".*?)(?:class="[^"]*i-estate[^"]*"|$)', re.S | re.I)
LISTING_HREF_RE = re.compile(
    r"""href=["']((?:https://www\.ceskereality\.cz)?/(?:pronajem|prodej)/[^"']+)["']""",
    re.I,
)
ID_ATTR_RE = re.compile(r'id-nemovitosti=["\'](\d+)["\']', re.I)
HTML_ID_RE = re.compile(r"-(\d{5,})\.html(?:$|[?#])", re.I)
ID_RE = re.compile(r"(?:-|/)(\d{5,})(?:\.html(?:$|[?#])|[-/]|$)")
IMG_ID_RE = re.compile(r"img-cache\.ceskereality\.cz/nemovitosti/(?![\w-]*x[\w-]*/)[^/]+/(\d+)/", re.I)
TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
ALT_RE = re.compile(r'alt="([^"]+)"')
PRICE_BOX_RE = re.compile(
    r"(?:i-estate__footer-price-value|i-estate__price|price)[^>]*>(.*?)</",
    re.S | re.I,
)
TOTAL_RE = re.compile(
    r"(?:vybírat ze|máme tady)\s+([\d\s\u00a0]+)\s+(?:byt|rodinn|nemovit|dom)",
    re.I,
)
# /pronajem/domy/ is the agency "Domy, spol. s r.o.", not the house category.
AGENCY_DOMY_RE = re.compile(r"^/(pronajem|prodej)/domy(/|$)", re.I)
NAV_SKIP = ("muj-profil", "redirect=", "/mapa/", "?sff=")
SORT_SKIP = ("/nejnovejsi/", "/nejlevnejsi/", "/nejdrazsi/")
PATH_SKIP = {
    "pronajem",
    "prodej",
    "byty",
    "rodinne-domy",
    "chaty-chalupy",
    "chaty",
    "cinzovni-domy",
    "pozemky",
    "komercni-prostory",
    "ostatni",
    "nejnovejsi",
    "nejlevnejsi",
    "nejdrazsi",
}


def canonical_list_url(url: str) -> str:
    """Rewrite agency /domy/ (and /dum/) list paths to live /rodinne-domy/."""
    split = urlsplit(url or "")
    path = split.path or "/"
    rewritten = AGENCY_DOMY_RE.sub(r"/\1/rodinne-domy\2", path, count=1)
    rewritten = re.sub(r"^/(pronajem|prodej)/dum(/|$)", r"/\1/rodinne-domy\2", rewritten, count=1, flags=re.I)
    if rewritten == path:
        return url
    return urlunsplit((split.scheme, split.netloc, rewritten, split.query, split.fragment))


def _city_from_url(url: str) -> str:
    parts = [part for part in urlsplit(url or "").path.split("/") if part]
    if not parts or not parts[-1].endswith(".html"):
        return ""
    slug = parts[-2].casefold() if len(parts) >= 2 else ""
    if not slug or slug in PATH_SKIP or slug.startswith("byty-"):
        return ""
    return slug.replace("-", " ").strip()


class CeskerealityClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _kind(self) -> str:
        path = (self.search_url or "").lower()
        if "rodinne-domy" in path or AGENCY_DOMY_RE.search(urlsplit(path).path or path) or "/dum/" in path:
            return "rodinne-domy"
        return "byty"

    def _nationwide_url(self, *, newest: bool) -> str:
        offer = self._context()
        kind = self._kind()
        suffix = "nejnovejsi/" if newest else ""
        return f"{SITE}/{offer}/{kind}/{suffix}"

    def _fallback_search_url(self) -> str:
        current = urlsplit(canonical_list_url(self.search_url or ""))
        if "/nejnovejsi/" in (current.path or ""):
            return self._nationwide_url(newest=False)
        return self._nationwide_url(newest=True)

    def _page_url(self, page: int = 1, newest: bool = True) -> str:
        return with_page(canonical_list_url(self.search_url or ""), page, self.PAGE_PARAM)

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        listings, total = await super().fetch_page(page, newest=newest)
        if listings or page > 1:
            return listings, total
        fallback = self._fallback_search_url()
        primary = canonical_list_url(self.search_url or "").split("?")[0].rstrip("/")
        if fallback.rstrip("/") == primary:
            return listings, total
        self.search_url = fallback
        return await super().fetch_page(page, newest=newest)

    def _parse_total(self, html: str) -> int:
        match = TOTAL_RE.search((html or "").replace("\xa0", " "))
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
        return super()._parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        blocks = CARD_RE.findall(html or "") or CARD2_RE.findall(html or "")
        if not blocks:
            blocks = [html or ""]
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for block in blocks:
            listing = self._parse_card(block, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _listing_href(self, html: str) -> str:
        for href in LISTING_HREF_RE.findall(html or ""):
            raw = href.split("#")[0]
            folded = raw.casefold()
            if any(skip in folded for skip in NAV_SKIP):
                continue
            if ".html" in folded:
                return raw
            if any(skip in folded for skip in SORT_SKIP):
                continue
            if ID_RE.search(raw):
                return raw
        return ""

    def _listing_id(self, html: str, url: str) -> str:
        attr = ID_ATTR_RE.search(html or "")
        if attr:
            return attr.group(1)
        html_id = HTML_ID_RE.search(url or "") or HTML_ID_RE.search(html or "")
        if html_id:
            return html_id.group(1)
        if url:
            id_m = ID_RE.search(url)
            if id_m:
                return id_m.group(1)
        img_id = IMG_ID_RE.search(html or "")
        return img_id.group(1) if img_id else ""

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        href = self._listing_href(html)
        url = abs_url(href, SITE) if href else ""
        listing_id = self._listing_id(html or "", url)
        if not listing_id:
            return None
        house = "rodinne-domy" in url or "/chaty/" in url
        if not url or "muj-profil" in url:
            house = house or self._kind() == "rodinne-domy"
            kind = "rodinne-domy" if house else "byty"
            url = f"{SITE}/{offer}/{kind}/{listing_id}/"
        title_m = TITLE_RE.search(html) or ALT_RE.search(html)
        title = clean(title_m.group(1) if title_m else "")
        price_m = PRICE_BOX_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1) if price_m else html)
        locality = ""
        if title:
            parts = re.split(r"\s+\d+\s*m", title, maxsplit=1)
            if len(parts) > 1:
                locality = clean(re.sub(r"^²\s*", "", parts[1]))
            elif "," in title:
                locality = title.split(",")[-1].strip()
        if not locality:
            locality = _city_from_url(url)
        folded = f"{title} {url}".casefold()
        estate = "dum" if house or "domu" in folded or "/chaty/" in folded else "byt"
        img = ""
        img_m = re.search(r'src="(https://img-cache\.ceskereality\.cz/[^"]+)"', html)
        if img_m:
            img = img_m.group(1).replace("/320x320_", "/640x640_").replace("/32x32_", "/640x640_")
        noun = "domu" if estate == "dum" else "bytu"
        return listing_from_card(
            listing_id=numeric_id(listing_id, url),
            name=title or f"{'Pronájem' if offer == 'pronajem' else 'Prodej'} {noun}",
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            image_url=img,
            photos=[img] if img else [],
            offer=offer,
            estate=estate,
        )
