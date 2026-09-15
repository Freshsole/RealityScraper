from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.catalog_sync import (
    fold,
    listing_matches_monitor,
    listing_offer,
    monitor_search_targets,
)
from app.identity import portal_from_url
from app import bazos_url, bezrealitky_url, idnes_url, url_builder
from app.sources import is_bazos, is_bezrealitky, is_idnes


def _normalize_offer_token(value: str) -> str:
    folded = fold(str(value or ""))
    if "pronaj" in folded:
        return "pronajem"
    if "draz" in folded:
        return "drazba"
    if "prodej" in folded:
        return "prodej"
    return folded or "*"


def _parse_filters(search_url: str) -> dict[str, Any]:
    url = (search_url or "").strip()
    if not url:
        return {}
    if is_idnes(url):
        return idnes_url.parse_url(url)
    if is_bazos(url):
        return bazos_url.parse_url(url)
    if is_bezrealitky(url):
        return bezrealitky_url.parse_url(url)
    return url_builder.parse_url(url)


def _size_tokens(filters: dict[str, Any]) -> list[str]:
    sizes = [str(item).casefold() for item in (filters.get("sizes") or []) if item]
    return sizes or ["*"]


def _offer_tokens(filters: dict[str, Any]) -> list[str]:
    offers = [_normalize_offer_token(str(item)) for item in (filters.get("offers") or []) if item]
    return offers or ["*"]


def _listing_size_token(listing: Any) -> str:
    disposition = fold(str(getattr(listing, "disposition", None) or "")).replace(" ", "")
    if not disposition:
        return "*"
    return disposition


class MonitorIndex:
    """Bucket monitors by (portal, offer[, size]) so matching is not O(listings × monitors)."""

    def __init__(self, monitors: list[dict[str, Any]] | None = None) -> None:
        self._by_bucket: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        self._by_id: dict[str, dict[str, Any]] = {}
        if monitors:
            self.rebuild(monitors)

    def rebuild(self, monitors: list[dict[str, Any]]) -> None:
        self._by_bucket.clear()
        self._by_id.clear()
        for monitor in monitors:
            if not monitor.get("enabled"):
                continue
            mid = str(monitor.get("id") or "")
            if not mid:
                continue
            self._by_id[mid] = monitor
            for target in monitor_search_targets(monitor):
                portal = str(target.get("portal") or "sreality")
                filters = _parse_filters(target.get("search_url") or "")
                for offer in _offer_tokens(filters):
                    for size in _size_tokens(filters):
                        self._by_bucket[(portal, offer, size)].append(monitor)
                        # Offer-level wildcard for size-specific monitors still reachable via "*"
                        if size != "*":
                            self._by_bucket[(portal, offer, "*")].append(monitor)

    def candidate_monitors(self, listing: Any) -> list[dict[str, Any]]:
        url = str(getattr(listing, "url", None) or "")
        portal = portal_from_url(url)
        offer = listing_offer(listing) or "*"
        size = _listing_size_token(listing)
        keys = [
            (portal, offer, size),
            (portal, offer, "*"),
            (portal, "*", size),
            (portal, "*", "*"),
        ]
        found: dict[str, dict[str, Any]] = {}
        for key in keys:
            for monitor in self._by_bucket.get(key, []):
                mid = str(monitor.get("id") or "")
                if mid and mid not in found:
                    found[mid] = monitor
        return list(found.values())

    def matching_monitors(self, listing: Any) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        for monitor in self.candidate_monitors(listing):
            try:
                if listing_matches_monitor(listing, monitor):
                    matched.append(monitor)
            except Exception:
                continue
        return matched

    @property
    def monitor_count(self) -> int:
        return len(self._by_id)

    @property
    def bucket_count(self) -> int:
        return len(self._by_bucket)
