from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app import config
from app.version import current_version, is_newer


def _headers() -> dict[str, str]:
    headers = {"User-Agent": "SrealityMonitor"}
    if config.UPDATE_TOKEN:
        headers["Authorization"] = f"Bearer {config.UPDATE_TOKEN}"
    return headers


def _github_repo(feed: str) -> str | None:
    raw = feed.strip()
    if raw.startswith("github:"):
        return raw.split(":", 1)[1].strip()
    prefix = "https://api.github.com/repos/"
    if raw.startswith(prefix) and raw.rstrip("/").endswith("/releases/latest"):
        return raw[len(prefix) :].split("/releases")[0]
    return None


async def latest_release() -> dict[str, str] | None:
    feed = config.UPDATE_FEED
    if not feed:
        return None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=_headers()) as client:
        repo = _github_repo(feed)
        if repo:
            response = await client.get(f"https://api.github.com/repos/{repo}/releases/latest")
            response.raise_for_status()
            data = response.json()
            version = str(data.get("tag_name") or "").lstrip("v")
            asset = next(
                (
                    item
                    for item in data.get("assets") or []
                    if str(item.get("name") or "").lower().endswith(".zip")
                ),
                None,
            )
            if not version or not asset:
                return None
            return {"version": version, "url": asset["browser_download_url"]}
        response = await client.get(feed)
        response.raise_for_status()
        data = response.json()
        version = str(data.get("version") or "").lstrip("v")
        url = str(data.get("url") or "")
        if not version or not url:
            return None
        return {"version": version, "url": url}


async def version_info() -> dict[str, Any]:
    current = current_version()
    info: dict[str, Any] = {
        "version": current,
        "latest": current,
        "update_available": False,
        "url": None,
        "feed": bool(config.UPDATE_FEED),
        "can_install": sys.platform == "win32",
    }
    if not config.UPDATE_FEED:
        return info
    try:
        latest = await latest_release()
    except Exception as exc:
        info["error"] = str(exc)
        return info
    if not latest:
        return info
    info["latest"] = latest["version"]
    info["url"] = latest["url"]
    info["update_available"] = is_newer(latest["version"], current)
    return info


def _payload_dir(extracted: Path) -> Path:
    for candidate in extracted.rglob("run_app.py"):
        return candidate.parent
    return extracted


def _write_apply_script(payload: Path, root: Path) -> Path:
    script = root / "_apply_update.bat"
    script.write_text(
        "\r\n".join(
            [
                "@echo off",
                "ping 127.0.0.1 -n 3 >nul",
                f'robocopy "{payload}" "{root}" /E /XD data /XF .env _apply_update.bat',
                f'start "" "{root}\\SrealityMonitor.exe"',
                'del "%~f0"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


async def apply_update(url: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Aktualizace se instaluje jen na Windows")
    root = config.ROOT
    tmp = Path(tempfile.mkdtemp(prefix="sreality-upd-"))
    zip_path = tmp / "update.zip"
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True, headers=_headers()) as client:
        response = await client.get(url)
        response.raise_for_status()
        zip_path.write_bytes(response.content)
    extracted = tmp / "extract"
    extracted.mkdir()
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extracted)
    script = _write_apply_script(_payload_dir(extracted), root)
    subprocess.Popen(["cmd", "/c", str(script)], cwd=str(root), close_fds=True)

    def shutdown() -> None:
        time.sleep(0.8)
        os._exit(0)

    threading.Thread(target=shutdown, daemon=True).start()
