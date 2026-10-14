"""Realingo.cz list/detail scraper (JSON-LD based)."""

from __future__ import annotations

import json
import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card
from app.sreality import Listing

SITE = "https://www.realingo.cz"

JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S | re.I
)
URL_ID_RE = re.compile(r'/(\d{4,})/?$')
PRICE_RE = re.compile(r'([\d\xa0\s]+)\s*Kč', re.I)


def _parse_price(text: str) -> tuple[int | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    label = m.group(0).strip()
    num = m.group(1).replace("\xa0", "").replace(" ", "").strip()
    return (int(num) if num.isdigit() else None), label


GRAPHQL_URL = SITE + "/graphql"
# Server cap is 500 items per query (first>500 is silently clamped); `skip` pages deeper.
# 100 keeps the minute-poll cheap (~15 KB, ~70 ms); a full flat market is ~150 (rent) / ~300 (sale) requests.
GRAPHQL_PAGE_SIZE = 100
GRAPHQL_MAX_PAGE_SIZE = 500
_FIELDS = (
    "id url purpose property createdAt category isLocked "
    "price{type total canonical currency} area{main} photos{main} "
    "location{address latitude longitude}"
)
_QUERY = (
    "query($filter:OfferFilterInput!,$first:Int,$skip:Int,$sort:OfferSort){"
    "searchOffer(filter:$filter,first:$first,skip:$skip,sort:$sort){total lockedOffersCount items{" + _FIELDS + "}}}"
)
_PATH_RE = re.compile(r"/(pronajem|prodej)_([a-z]+)", re.I)
_PROPERTY = {"byty": "FLAT", "domy": "HOUSE", "pozemky": "LAND"}
# Realingo category enum -> disposition label ("OTHERS_FLAT" = nespecifikovaná dispozice).
_CATEGORY_DISP = {
    "FLAT1_KK": "1+kk", "FLAT11": "1+1", "FLAT2_KK": "2+kk", "FLAT21": "2+1",
    "FLAT3_KK": "3+kk", "FLAT31": "3+1", "FLAT4_KK": "4+kk", "FLAT41": "4+1",
    "FLAT5_KK": "5+kk", "FLAT51": "5+1", "FLAT6_AND_MORE": "6+",
}


def _num(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


class RealingoClient(HtmlPortalClient):
    """Realingo list via the site's own GraphQL endpoint (/graphql).

    The HTML list (`/pronajem_byty/cr/N_strana/`) only carries 40 rows (8 of them "locked"
    teasers without data); GraphQL returns up to 500 rows per query, sorted newest-first
    (`sort: NEWEST`) with `skip` offsets. Anonymous users do not see offers younger than
    ~48 h (premium early access) - `total` already excludes those (`lockedOffersCount`).
    """

    SITE = SITE
    PAGE_SIZE = GRAPHQL_PAGE_SIZE

    def _filter(self) -> dict:
        m = _PATH_RE.search(self.search_url or "")
        offer, kind = (m.group(1).lower(), m.group(2).lower()) if m else ("pronajem", "byty")
        flt = {"purpose": "SELL" if offer == "prodej" else "RENT"}
        prop = _PROPERTY.get(kind)
        if prop:
            flt["property"] = prop
        return flt

    def _estate(self) -> str:
        return {"HOUSE": "dum", "LAND": "pozemek"}.get(self._filter().get("property", ""), "byt")

    def _items_to_listings(self, items: list) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        ctx = self._context()
        estate = self._estate()
        for it in items or []:
            if not isinstance(it, dict) or it.get("isLocked"):
                continue
            lid = str(it.get("id") or "")
            path = str(it.get("url") or "")
            if not lid.isdigit() or not path or lid in seen:
                continue
            seen.add(lid)
            price = it.get("price") or {}
            czk = price.get("canonical") if price.get("canonical") is not None else (
                price.get("total") if price.get("currency") in (None, "CZK") else None
            )
            czk = int(czk) if isinstance(czk, (int, float)) and czk > 0 else None
            area = (it.get("area") or {}).get("main")
            area = int(area) if isinstance(area, (int, float)) and area > 0 else None
            loc = it.get("location") or {}
            address = clean(loc.get("address") or "")
            disp = _CATEGORY_DISP.get(str(it.get("category") or ""), "")
            label = {"byt": "Byt", "dum": "Dům", "pozemek": "Pozemek"}[estate]
            name = ", ".join(p for p in (disp or label, address, f"{area} m²" if area else "") if p)
            main = (it.get("photos") or {}).get("main")
            out.append(listing_from_card(
                listing_id=int(lid),
                name=name,
                url=abs_url(path, SITE),
                price_czk=czk,
                price_label="",
                locality=address,
                disposition=disp,
                area_m2=area,
                image_url=f"{SITE}/static/images/{main}" if main else None,
                offer=ctx,
                estate=estate,
                created_on=it.get("createdAt"),
                lat=_num(loc.get("latitude")),
                lon=_num(loc.get("longitude")),
            ))
        return out

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        from app.scrape_http import request_with_log
        from app.scrape_timing import note_httpx

        size = min(int(self.PAGE_SIZE), GRAPHQL_MAX_PAGE_SIZE)
        body = {
            "query": _QUERY,
            "variables": {
                "filter": self._filter(),
                "first": size,
                "skip": max(0, int(page) - 1) * size,
                "sort": "NEWEST",
            },
        }
        response = await request_with_log(
            self._client, "POST", GRAPHQL_URL, portal="realingo",
            json=body, headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        note_httpx(response)
        if response.status_code in {403, 429}:
            self._blocked_status = response.status_code
            return [], 0
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise RuntimeError("realingo graphql: " + str(payload.get("errors"))[:200])
        res = payload["data"].get("searchOffer") or {}
        items = res.get("items") or []
        listings = self._items_to_listings(items)
        return listings, int(res.get("total") or 0)

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        # HTML fallback only. The path MUST end with /cr/ - without it realingo serves a 1-row "Stránní" page.
        base = (self.search_url or "").split("?")[0].rstrip("/")
        if page <= 1:
            return base + "/"
        return f"{base}/{page}_strana/"

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        ctx = self._context()
        for jm in JSONLD_RE.finditer(html or ""):
            try:
                data = json.loads(jm.group(1))
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("@type") not in ("SingleFamilyResidence", "Apartment", "House"):
                continue
            url_path = data.get("url", "")
            id_m = URL_ID_RE.search(url_path or "")
            if not id_m:
                continue
            lid = id_m.group(1)
            if lid in seen:
                continue
            seen.add(lid)
            url = abs_url(url_path, SITE)
            title = clean(data.get("name", ""))
            img = data.get("image", "")
            if img and img.startswith("/"):
                img = SITE + img
            locality = clean(data.get("address", ""))
            area = None
            fs = data.get("floorSize", {})
            if isinstance(fs, dict):
                try:
                    area = int(float(str(fs.get("value", "")).replace(",", ".")))
                except ValueError:
                    pass
            lat, lon = None, None
            geo = data.get("geo", {})
            if isinstance(geo, dict):
                lat = geo.get("latitude")
                lon = geo.get("longitude")
            # Cena není v JSON-LD, zkusíme najít v okolí
            idx = (html or "").find(url_path)
            snippet = (html or "")[max(0, idx - 2000):idx + 2000]
            price, price_label = _parse_price(snippet)
            out.append(listing_from_card(
                listing_id=abs(hash(f"realingo_{lid}")) % (10 ** 12),
                name=title,
                url=url,
                price_czk=price,
                price_label=price_label,
                locality=locality,
                area_m2=area,
                image_url=img,
                offer=ctx,
                lat=lat,
                lon=lon,
            ))
        return out
