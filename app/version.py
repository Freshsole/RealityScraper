from __future__ import annotations

from pathlib import Path

from app import config


def current_version() -> str:
    for path in (config.ROOT / "VERSION", config.resource_root() / "VERSION"):
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "0.0.0"


def parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in (value or "").strip().lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or (0,))


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)
