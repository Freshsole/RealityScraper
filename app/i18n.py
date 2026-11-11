"""Automatic UI translation (Czech source -> sk, uk, en, ru, vi, de).

Nothing is hand-translated: the browser (web/site/i18n.js) sends the Czech strings it
finds on a page, we translate the unseen ones with the LLM and keep every result in a
SQLite cache keyed by (sha1(source), language). Whatever is added to the UI later is
therefore translated on first sight in all languages, with no translation files to keep.

Only UI strings travel through here (never user data). Numbers are replaced by {0}, {1}
placeholders on the client, so "Nalezeno 1 234 nabídek" is one cache entry, not thousands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from app import config, rate_limit

logger = logging.getLogger("realitify.i18n")

LANGUAGES: dict[str, str] = {
    "cs": "Czech",
    "sk": "Slovak Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Nájdite byt skôr, než ho zoberie niekto iný'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Nájdite byt skôr, než ho zoberie niekto iný\'.",
    "uk": "Ukrainian Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Знайдіть квартиру, поки її не забрав хтось інший'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Знайдіть квартиру, поки її не забрав хтось інший\'.",
    "en": "English Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Beat other renters to the apartment you want'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Beat other renters to the apartment you want\'.",
    "ru": "Russian Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Найдите квартиру раньше, чем её снимет кто-то другой'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Найдите квартиру раньше, чем её снимет кто-то другой\'.",
    "vi": "Vietnamese Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Tìm căn hộ ưng ý trước khi người khác kịp ra tay'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Tìm căn hộ ưng ý trước khi người khác kịp ra tay\'.",
    "de": "German Be bold like this: 'Najděte byt{#1}dřív než ostatní' -> 'Sichern Sie sich die Wohnung, bevor es ein anderer tut'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Sichern Sie sich die Wohnung, bevor es ein anderer tut\'.",
}
DEFAULT_LANG = "cs"

MAX_STRINGS = 200
MAX_STRING_LEN = 1500
MAX_TOTAL_CHARS = 30000
LLM_BATCH = 40
LLM_CONCURRENCY = 4
RATE_LIMIT = (40, 60.0)  # requests per window per IP

_PLACEHOLDER = re.compile(r"\{#?\d+\}")
_db_lock = threading.Lock()
_db_ready = False

STYLE_GUIDE = {
    "sk": "Slovak: natural, modern Slovak, formal 'vy' (Vy) address. No Czechisms. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Nájdite byt skôr, než ho zoberie niekto iný\'.",
    "uk": "Ukrainian: natural modern Ukrainian, formal 'ви' address. No Russianisms, no word-for-word calques. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Знайдіть квартиру, поки її не забрав хтось інший\'.",
    "en": "English: natural, punchy international English (say 'apartment', 'rent', 'listings'). Direct 'you' address. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Beat other renters to the apartment you want\'.",
    "ru": "Russian: natural modern Russian, formal 'вы' address. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Найдите квартиру раньше, чем её снимет кто-то другой\'.",
    "vi": "Vietnamese: natural modern Vietnamese, friendly and clear, address the reader as 'bạn'. Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Tìm căn hộ ưng ý trước khi người khác kịp ra tay\'.",
    "de": "German: natural modern German, formal 'Sie' address, concise (avoid long compound words where a shorter phrase works). Be this bold: \'Najděte byt{#1}dřív než ostatní\' -> \'Sichern Sie sich die Wohnung, bevor es ein anderer tut\'.",
}

SYSTEM_PROMPT = (
    "You are a senior localisation copywriter for Realitify, a Czech service that aggregates rental and sale "
    "listings from all major portals and alerts people the moment a new matching apartment appears (the alerting "
    "feature is called 'hlídací pes' = a watchdog / saved search, the mascot is Bára the dog).\n"
    "Translate each Czech UI string from the JSON array into {language}. {style}\n"
    "This is localisation, NOT literal translation: write what a native speaker and a good marketer would have "
    "written for that market. Headlines and claims must be confident, vivid and benefit-led; rebuild the phrase "
    "when a word-for-word version sounds flat, foreign or weak (e.g. 'Najděte byt dřív než ostatní' is about "
    "beating other renters to a flat - express that with energy, do not just say 'before others'). Buttons and "
    "labels stay short and idiomatic; legal and help text stays precise and neutral. Use consistent terminology "
    "across strings: byt=apartment, nájem=rent, nabídky=listings, hlídací pes=watchdog (render it naturally and "
    "consistently), index nájmů=rent index. Fixed term for 'hlídací pes' per language: English 'Watchdog', "
    "Slovak 'Strážny pes', Ukrainian 'Пес-охоронець', Russian 'Сторожевой пёс', Vietnamese 'Chó canh gác', "
    "German 'Watchdog' (lower-case/inflect it as the grammar needs).\n"
    "Technical rules: keep placeholders such as {{0}}, {{1}} and part markers such as {{#1}}, {{#2}} exactly as "
    "written, each exactly once, in the same order ({{#n}} marks where one sentence is split across HTML elements, "
    "e.g. a line break or a coloured word - translate the whole sentence naturally and put each marker at the "
    "matching break). Do not translate brand and product names (Realitify, Sreality, Bezrealitky, Bazoš, iDNES, "
    "RE/MAX, Discord, Bára, MCP, Google, Stripe); keep 'Kč', m², emojis and surrounding punctuation. No "
    "explanations. Answer with ONLY a JSON array of strings, same length and order as the input."
)


def _db_path() -> Path:
    return Path(os.getenv("I18N_DB_PATH") or (config.DATA_DIR / "i18n.sqlite"))


def _connect() -> sqlite3.Connection:
    global _db_ready
    conn = sqlite3.connect(str(_db_path()), timeout=10)
    if not _db_ready:
        with _db_lock:
            if not _db_ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS i18n_cache ("
                    "h TEXT NOT NULL, lang TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, "
                    "PRIMARY KEY (h, lang))"
                )
                conn.commit()
                _db_ready = True
    return conn


def key_for(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def _cache_get(lang: str, keys: list[str]) -> dict[str, str]:
    if not keys:
        return {}
    out: dict[str, str] = {}
    with _connect() as conn:
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(f"SELECT h, dst FROM i18n_cache WHERE lang=? AND h IN ({marks})", [lang, *chunk])
            out.update({h: dst for h, dst in rows})
    return out


def _cache_put(lang: str, rows: list[tuple[str, str, str]]) -> None:
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO i18n_cache (h, lang, src, dst) VALUES (?, ?, ?, ?)",
            [(h, lang, src, dst) for h, src, dst in rows],
        )


_client: Any = None


def _llm() -> tuple[Any, str]:
    """Lazy OpenAI-compatible client (same LLM_* env as the AI chat, but independent of AI_ENABLED)."""
    global _client
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = (os.getenv("I18N_MODEL") or os.getenv("LLM_MODEL") or "").strip()
    if not api_key or not model:
        raise RuntimeError("LLM_API_KEY / LLM_MODEL are not configured")
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(
            base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            timeout=60,
            max_retries=1,
        )
    return _client, model


def _parse_array(text: str) -> list[Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, list) else None


def _valid(src: str, dst: Any) -> bool:
    if not isinstance(dst, str) or not dst.strip():
        return False
    return sorted(_PLACEHOLDER.findall(src)) == sorted(_PLACEHOLDER.findall(dst))


async def _translate_batch(lang: str, sources: list[str]) -> dict[str, str]:
    client, model = _llm()
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(language=LANGUAGES[lang], style=STYLE_GUIDE[lang])},
            {"role": "user", "content": json.dumps(sources, ensure_ascii=False)},
        ],
        temperature=0.2,
        max_tokens=min(8000, 400 + sum(len(s) for s in sources) * 3),
        extra_body={"thinking": {"type": "disabled"}},
    )
    parsed = _parse_array((response.choices[0].message.content or ""))
    if parsed is None or len(parsed) != len(sources):
        logger.warning("i18n batch mismatch lang=%s expected=%d", lang, len(sources))
        return {}
    return {src: dst.strip() for src, dst in zip(sources, parsed) if _valid(src, dst)}


async def translate(lang: str, strings: list[str]) -> dict[str, str]:
    """Return {sha1-key: translation} for every string we could translate (others are omitted)."""
    if lang not in LANGUAGES or lang == DEFAULT_LANG:
        return {}
    unique = list(dict.fromkeys(strings))
    keys = {s: key_for(s) for s in unique}
    loop = asyncio.get_running_loop()
    cached = await loop.run_in_executor(None, _cache_get, lang, list(keys.values()))
    missing = [s for s in unique if keys[s] not in cached]
    if missing:
        sem = asyncio.Semaphore(LLM_CONCURRENCY)

        async def run(chunk: list[str]) -> dict[str, str]:
            async with sem:
                done: dict[str, str] = {}
                for _attempt in range(2):  # one retry for strings the model mangled (lost placeholders/markers)
                    todo = [s for s in chunk if s not in done]
                    if not todo:
                        break
                    try:
                        done.update(await _translate_batch(lang, todo))
                    except Exception as exc:  # noqa: BLE001 - the page just stays Czech
                        logger.warning("i18n LLM call failed lang=%s error=%s", lang, type(exc).__name__)
                        break
                return done

        chunks = [missing[i : i + LLM_BATCH] for i in range(0, len(missing), LLM_BATCH)]
        results = await asyncio.gather(*(run(c) for c in chunks))
        fresh: dict[str, str] = {}
        for part in results:
            fresh.update(part)
        await loop.run_in_executor(None, _cache_put, lang, [(keys[s], s, d) for s, d in fresh.items()])
        cached.update({keys[s]: d for s, d in fresh.items()})
    return cached


def register(app: Any, client_ip: Any) -> None:
    @app.post("/api/i18n/translate")
    async def i18n_translate(request: Request):
        if not rate_limit.check("i18n", client_ip(request), *RATE_LIMIT):
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
        lang = str((payload or {}).get("lang") or "")
        strings = (payload or {}).get("strings")
        if lang not in LANGUAGES or not isinstance(strings, list):
            return JSONResponse({"detail": "Invalid request"}, status_code=400)
        strings = [s for s in strings if isinstance(s, str) and s.strip()]
        if (
            len(strings) > MAX_STRINGS
            or any(len(s) > MAX_STRING_LEN for s in strings)
            or sum(len(s) for s in strings) > MAX_TOTAL_CHARS
        ):
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        cached_only = not (os.getenv("LLM_API_KEY", "").strip() and (os.getenv("I18N_MODEL") or os.getenv("LLM_MODEL")))
        if cached_only:
            unique = list(dict.fromkeys(strings))
            hits = _cache_get(lang, [key_for(s) for s in unique]) if unique else {}
            return JSONResponse({"translations": hits}, headers={"Cache-Control": "no-store"})
        translations = await translate(lang, strings)
        return JSONResponse({"translations": translations}, headers={"Cache-Control": "no-store"})
