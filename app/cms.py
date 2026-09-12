from __future__ import annotations

import json
import re
import secrets
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import analytics as site_stats
from app import config
from app.store import Store

META_KEY = "cms_articles"
INQ_KEY = "cms_inquiries"
INQ_STATUSES = ("new", "contacted", "done")
SEED_PATH = Path(__file__).with_name("cms_seed.json")
MEDIA_DIR = config.DATA_DIR / "cms-media"

STAT_LABELS = [
    ("hledali", "Hledali"),
    ("typ", "Typ bytu"),
    ("lokalita", "Lokalita"),
    ("budget", "Budget"),
    ("monitoru", "Monitorů"),
    ("notifikace", "Notifikace"),
]
APT_LABELS = [
    ("apt_dispozice", "Dispozice"),
    ("apt_lokalita", "Lokalita"),
    ("apt_velikost", "Velikost"),
    ("apt_najem", "Měsíční nájem"),
    ("apt_vztah", "Typ vztahu"),
]
BODY_DEFAULTS = {
    "start_h": "Jak to celé začalo",
    "discover_h": "Objev Realitify",
    "found_h": "Nalezení bytu",
    "advice_h": "Co by vzkázali ostatním",
    "apt_title": "Nalezený byt v detailech",
    "badge": "Příběh",
    "category": "Úspěšné příběhy",
    "card_badge": "Příběh",
}

TEXT_FIELDS = [
    "title",
    "slug",
    "crumb",
    "badge",
    "lede",
    "author",
    "role",
    "photo",
    "photo_pos",
    "list_photo",
    "card_badge",
    "card_excerpt",
    "featured_quote",
    "tag_place",
    "tag_spec",
    "tag_price",
    "cms_author",
    "category",
    "seo_tags",
    "seo_desc",
    "status",
    "published_at",
    "start_h",
    "start_p1",
    "start_p2",
    "discover_h",
    "discover_p1",
    "discover_p2",
    "quote",
    "found_h",
    "found_p1",
    "found_p2",
    "advice_h",
    "advice_p",
    "apt_title",
    "apt_dispozice",
    "apt_lokalita",
    "apt_velikost",
    "apt_najem",
    "apt_vztah",
]
STAT_KEYS = [key for key, _ in STAT_LABELS]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80] or "clanek"


def blank_article(author: str = "Admin") -> dict[str, Any]:
    stamp = _now()
    return {
        "id": secrets.token_hex(8),
        "slug": "",
        "status": "draft",
        "title": "",
        "crumb": "",
        "badge": BODY_DEFAULTS["badge"],
        "lede": "",
        "author": "",
        "role": "",
        "photo": "",
        "photo_pos": "50% 50%",
        "list_photo": "",
        "featured": False,
        "card_badge": BODY_DEFAULTS["card_badge"],
        "card_excerpt": "",
        "featured_quote": "",
        "tag_place": "",
        "tag_spec": "",
        "tag_price": "",
        "cms_author": author,
        "category": BODY_DEFAULTS["category"],
        "seo_tags": "",
        "seo_desc": "",
        "views": 0,
        "published_at": "",
        "created_at": stamp,
        "updated_at": stamp,
        "stats": {key: "" for key in STAT_KEYS},
        **BODY_DEFAULTS,
        "start_p1": "",
        "start_p2": "",
        "discover_p1": "",
        "discover_p2": "",
        "quote": "",
        "found_p1": "",
        "found_p2": "",
        "advice_p": "",
        "apt_dispozice": "",
        "apt_lokalita": "",
        "apt_velikost": "",
        "apt_najem": "",
        "apt_vztah": "",
        "blocks": [],
    }


_BRAND_SWAPS = (
    ("BytAlertem", "Realitify"),
    ("BytAlertu", "Realitify"),
    ("BYTALERT", "REALITIFY"),
    ("BytAlert", "Realitify"),
    ("bytalert.cz", "realitify.cz"),
)


def _rebrand_text(value: str) -> str:
    text = value
    for old, new in _BRAND_SWAPS:
        text = text.replace(old, new)
    return text


def _rebrand_value(value: Any) -> Any:
    if isinstance(value, str):
        return _rebrand_text(value)
    if isinstance(value, dict):
        return {key: _rebrand_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebrand_value(item) for item in value]
    return value


def _sanitize_blocks(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        text = str(item.get("text") or "")
        if kind not in {"h", "p", "quote"}:
            if item.get("h"):
                kind, text = "h", str(item.get("h") or "")
            elif item.get("p"):
                kind, text = "p", str(item.get("p") or "")
            elif item.get("quote"):
                kind, text = "quote", str(item.get("quote") or "")
            else:
                continue
        out.append({"type": kind, "text": text})
    return out


def _legacy_blocks(item: dict[str, Any]) -> list[dict[str, str]]:
    flow = [
        ("h", "start_h"),
        ("p", "start_p1"),
        ("p", "start_p2"),
        ("h", "discover_h"),
        ("p", "discover_p1"),
        ("p", "discover_p2"),
        ("quote", "quote"),
        ("h", "found_h"),
        ("p", "found_p1"),
        ("p", "found_p2"),
        ("h", "advice_h"),
        ("p", "advice_p"),
    ]
    blocks: list[dict[str, str]] = []
    for kind, key in flow:
        text = str(item.get(key) or "")
        if text:
            blocks.append({"type": kind, "text": text})
    return blocks


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    item = _rebrand_value(item)
    base = blank_article(str(item.get("cms_author") or "Admin"))
    base.update({k: item.get(k, base[k]) for k in base if k not in {"stats", "blocks"}})
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    base["stats"] = {key: str(stats.get(key) or item.get(key) or "") for key in STAT_KEYS}
    base["featured"] = bool(item.get("featured"))
    base["views"] = int(item.get("views") or 0)
    if "blocks" in item and isinstance(item.get("blocks"), list):
        base["blocks"] = _sanitize_blocks(item["blocks"])
    else:
        base["blocks"] = _legacy_blocks(item)
    return base


def _load_seed() -> list[dict[str, Any]]:
    if not SEED_PATH.exists():
        return []
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return [_normalize(item) for item in data]


def _read(store: Store) -> list[dict[str, Any]]:
    raw = store.get_meta(META_KEY) or ""
    if not raw:
        seeded = _load_seed()
        _write(store, seeded)
        return seeded
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _load_seed()
    articles = [_normalize(item) for item in data if isinstance(item, dict)]
    if "BytAlert" in raw or "BYTALERT" in raw or "bytalert" in raw:
        _write(store, articles)
    return articles


def _write(store: Store, articles: list[dict[str, Any]]) -> None:
    store.set_meta(META_KEY, json.dumps(articles, ensure_ascii=False))


def _parse_dt(value: str) -> datetime | None:
    text = (value or "").replace("Z", "+00:00")
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _publish_due(articles: list[dict[str, Any]]) -> bool:
    now = datetime.now(timezone.utc)
    changed = False
    for item in articles:
        if item.get("status") != "scheduled":
            continue
        when = _parse_dt(str(item.get("published_at") or ""))
        if when and when <= now:
            item["status"] = "published"
            changed = True
    return changed


def _unique_slug(articles: list[dict[str, Any]], slug: str, current_id: str) -> str:
    base = _slugify(slug)
    taken = {item.get("slug") for item in articles if item.get("id") != current_id}
    if base not in taken:
        return base
    index = 2
    while f"{base}-{index}" in taken:
        index += 1
    return f"{base}-{index}"


def apply_payload(article: dict[str, Any], payload: dict[str, Any], articles: list[dict[str, Any]]) -> dict[str, Any]:
    out = _normalize(article)
    for field in TEXT_FIELDS:
        if field in payload and payload[field] is not None:
            out[field] = str(payload[field])
    if "featured" in payload:
        out["featured"] = bool(payload["featured"])
    if "blocks" in payload:
        out["blocks"] = _sanitize_blocks(payload["blocks"])
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    merged = dict(out["stats"])
    for key in STAT_KEYS:
        if key in payload and payload[key] is not None:
            merged[key] = str(payload[key])
        if key in stats:
            merged[key] = str(stats[key] or "")
    out["stats"] = merged
    slug_src = out["slug"] or out["title"] or out["crumb"] or out["id"]
    out["slug"] = _unique_slug(articles, slug_src, out["id"])
    if not out["crumb"]:
        out["crumb"] = out["author"] or out["title"][:40]
    status = out["status"] if out["status"] in {"draft", "scheduled", "published"} else "draft"
    out["status"] = status
    if status == "published" and not out.get("published_at"):
        out["published_at"] = _now()
    out["updated_at"] = _now()
    return out


def list_articles(store: Store) -> dict[str, Any]:
    articles = _read(store)
    if _publish_due(articles):
        _write(store, articles)
    published = [a for a in articles if a["status"] == "published"]
    drafts = [a for a in articles if a["status"] == "draft"]
    scheduled = [a for a in articles if a["status"] == "scheduled"]
    real_views = site_stats.story_view_counts(store)
    ordered = []
    for item in sorted(articles, key=lambda a: a.get("updated_at") or "", reverse=True):
        row = dict(item)
        row["views"] = int(real_views.get(row.get("slug") or "", 0))
        ordered.append(row)
    views = sum(int(row.get("views") or 0) for row in ordered)
    return {
        "articles": ordered,
        "stats": {
            "published": len(published),
            "drafts": len(drafts),
            "scheduled": len(scheduled),
            "views": views,
        },
        "categories": sorted({a.get("category") or "Úspěšné příběhy" for a in articles}),
        "stat_labels": STAT_LABELS,
        "apt_labels": APT_LABELS,
        "inquiries": list_inquiries(store),
    }


def get_article(store: Store, article_id: str) -> dict[str, Any]:
    articles = _read(store)
    for item in articles:
        if item["id"] == article_id or item["slug"] == article_id:
            return item
    raise KeyError("Článek neexistuje")


def save_article(store: Store, article_id: str | None, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    articles = _read(store)
    if article_id in (None, "", "novy"):
        current = blank_article(actor)
        articles.append(current)
    else:
        current = next((item for item in articles if item["id"] == article_id or item["slug"] == article_id), None)
        if current is None:
            raise KeyError("Článek neexistuje")
    saved = apply_payload(current, payload, articles)
    next_list: list[dict[str, Any]] = []
    for item in articles:
        if item["id"] == saved["id"]:
            next_list.append(saved)
            continue
        if saved.get("featured") and item.get("featured"):
            item = dict(item)
            item["featured"] = False
        next_list.append(item)
    _write(store, next_list)
    return saved


def delete_article(store: Store, article_id: str) -> dict[str, Any]:
    articles = _read(store)
    kept = [item for item in articles if item["id"] != article_id and item["slug"] != article_id]
    if len(kept) == len(articles):
        raise KeyError("Článek neexistuje")
    _write(store, kept)
    return {"ok": True}


def bump_views(store: Store, slug: str) -> None:
    articles = _read(store)
    changed = False
    for item in articles:
        if item["slug"] == slug and item["status"] == "published":
            item["views"] = int(item.get("views") or 0) + 1
            changed = True
            break
    if changed:
        _write(store, articles)


def _public_body(article: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    source = article.get("blocks") if isinstance(article.get("blocks"), list) else _legacy_blocks(article)
    for item in source:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        kind = item.get("type")
        if kind == "h":
            blocks.append({"h": text})
        elif kind == "quote":
            blocks.append({"quote": text})
        else:
            blocks.append({"p": text})
    apt_rows = [[label, article.get(key) or ""] for key, label in APT_LABELS if article.get(key)]
    if apt_rows:
        blocks.append({"apt": {"title": article.get("apt_title") or "Nalezený byt v detailech", "rows": apt_rows}})
    return blocks or [{"p": article.get("lede") or ""}]


def public_article(article: dict[str, Any]) -> dict[str, Any]:
    stats = article.get("stats") or {}
    return {
        "id": article["id"],
        "slug": article["slug"],
        "status": article["status"],
        "featured": bool(article.get("featured")),
        "crumb": article.get("crumb") or article.get("author") or "",
        "title": article.get("title") or "",
        "documentTitle": f"{article.get('crumb') or article.get('title') or 'Příběh'} — REALITIFY",
        "badge": article.get("badge") or "Příběh",
        "lede": article.get("lede") or "",
        "author": article.get("author") or "",
        "role": article.get("role") or "",
        "photo": article.get("photo") or article.get("list_photo") or "",
        "photoPos": article.get("photo_pos") or "50% 50%",
        "listPhoto": article.get("list_photo") or article.get("photo") or "",
        "cardBadge": article.get("card_badge") or article.get("badge") or "Příběh",
        "cardExcerpt": article.get("card_excerpt") or article.get("lede") or "",
        "featuredQuote": article.get("featured_quote") or article.get("quote") or "",
        "tagPlace": article.get("tag_place") or stats.get("lokalita") or "",
        "tagSpec": article.get("tag_spec") or "",
        "tagPrice": article.get("tag_price") or stats.get("budget") or "",
        "stats": [[label, stats.get(key) or ""] for key, label in STAT_LABELS],
        "body": _public_body(article),
        "seoDesc": article.get("seo_desc") or article.get("lede") or "",
    }


def public_listing(store: Store) -> dict[str, Any]:
    articles = _read(store)
    if _publish_due(articles):
        _write(store, articles)
    published = [public_article(item) for item in articles if item["status"] == "published"]
    featured = next((item for item in published if item.get("featured")), published[0] if published else None)
    cards = [item for item in published if not featured or item["slug"] != featured["slug"]]
    return {"featured": featured, "articles": cards}


def public_by_slug(store: Store, slug: str, count_view: bool = False) -> dict[str, Any]:
    articles = _read(store)
    if _publish_due(articles):
        _write(store, articles)
    item = next((row for row in articles if row["slug"] == slug and row["status"] == "published"), None)
    if item is None:
        raise KeyError("Článek neexistuje")
    if count_view:
        bump_views(store, slug)
        item = get_article(store, slug)
    return public_article(item)


def save_upload(filename: str, data: bytes) -> str:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename or "").suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    name = f"{secrets.token_hex(12)}{ext}"
    path = MEDIA_DIR / name
    path.write_bytes(data)
    return f"/media/cms/{name}"


def media_path(name: str) -> Path:
    safe = Path(name).name
    path = (MEDIA_DIR / safe).resolve()
    if MEDIA_DIR.resolve() not in path.parents and path != MEDIA_DIR.resolve():
        raise KeyError("Soubor neexistuje")
    if not path.is_file():
        raise KeyError("Soubor neexistuje")
    return path


def _read_inquiries(store: Store) -> list[dict[str, Any]]:
    raw = store.get_meta(INQ_KEY) or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "new")
        if status not in INQ_STATUSES:
            status = "new"
        out.append(
            {
                "id": str(item.get("id") or secrets.token_hex(8)),
                "created_at": str(item.get("created_at") or _now()),
                "name": str(item.get("name") or "").strip()[:120],
                "email": str(item.get("email") or "").strip()[:160],
                "phone": str(item.get("phone") or "").strip()[:40],
                "message": str(item.get("message") or "").strip()[:4000],
                "subject": str(item.get("subject") or "").strip()[:160],
                "source": str(item.get("source") or "Kontaktní stránka").strip()[:180],
                "status": status,
            }
        )
    return out


def _write_inquiries(store: Store, rows: list[dict[str, Any]]) -> None:
    store.set_meta(INQ_KEY, json.dumps(rows, ensure_ascii=False))


def _inquiry_source(store: Store, payload: dict[str, Any]) -> str:
    slug = str(payload.get("slug") or payload.get("article_slug") or "").strip().strip("/")
    if slug:
        articles = _read(store)
        item = next((row for row in articles if row.get("slug") == slug), None)
        if item:
            label = str(item.get("crumb") or item.get("title") or slug).strip()
            return f"Success story: {label}"[:180]
        return "Success story"
    source = str(payload.get("source") or "").strip()
    if source.startswith("Success story:"):
        return source[:180]
    return "Kontaktní stránka"


def list_inquiries(store: Store) -> dict[str, Any]:
    rows = sorted(_read_inquiries(store), key=lambda row: row.get("created_at") or "", reverse=True)
    new_count = sum(1 for row in rows if row["status"] == "new")
    return {"items": rows, "new_count": new_count}


def create_inquiry(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or payload.get("jmeno") or "").strip()
    email = str(payload.get("email") or "").strip()
    message = str(payload.get("message") or payload.get("zprava") or "").strip()
    if not name or not email or not message:
        raise ValueError("Vyplňte jméno, e-mail a zprávu")
    if "@" not in email or "." not in email.split("@")[-1]:
        raise ValueError("Zadejte platný e-mail")
    row = {
        "id": secrets.token_hex(8),
        "created_at": _now(),
        "name": name[:120],
        "email": email[:160],
        "phone": str(payload.get("phone") or payload.get("telefon") or "").strip()[:40],
        "message": message[:4000],
        "subject": str(payload.get("subject") or payload.get("predmet") or "").strip()[:160],
        "source": _inquiry_source(store, payload),
        "status": "new",
    }
    rows = _read_inquiries(store)
    rows.append(row)
    _write_inquiries(store, rows)
    return {"ok": True}


def update_inquiry(store: Store, inquiry_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    rows = _read_inquiries(store)
    current = next((row for row in rows if row["id"] == inquiry_id), None)
    if current is None:
        raise KeyError("Dotaz neexistuje")
    if "name" in payload:
        current["name"] = str(payload.get("name") or "").strip()[:120]
    if "email" in payload:
        current["email"] = str(payload.get("email") or "").strip()[:160]
    if "phone" in payload:
        current["phone"] = str(payload.get("phone") or "").strip()[:40]
    if "message" in payload:
        current["message"] = str(payload.get("message") or "").strip()[:4000]
    if "status" in payload:
        status = str(payload.get("status") or "")
        if status not in INQ_STATUSES:
            raise ValueError("Neplatný stav")
        current["status"] = status
    _write_inquiries(store, rows)
    return current


def delete_inquiry(store: Store, inquiry_id: str) -> dict[str, Any]:
    rows = _read_inquiries(store)
    kept = [row for row in rows if row["id"] != inquiry_id]
    if len(kept) == len(rows):
        raise KeyError("Dotaz neexistuje")
    _write_inquiries(store, kept)
    return {"ok": True}
