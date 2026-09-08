#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "SrealityMonitor"
PYTHON_VERSION = "3.12.10"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, **kwargs)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"cached {dest.name}")
        return
    print(f"download {url}")
    urllib.request.urlretrieve(url, dest)


def extract_wheel(wheel: Path, target: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(target)


def main() -> None:
    cache = ROOT / "dist" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    if DIST.exists():
        shutil.rmtree(DIST, ignore_errors=True)
        if DIST.exists():
            subprocess.check_call(["rm", "-rf", str(DIST)])
    DIST.mkdir(parents=True)

    embed_zip = cache / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    download(EMBED_URL, embed_zip)
    python_dir = DIST / "python"
    python_dir.mkdir()
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(python_dir)

    pth = next(python_dir.glob("python*._pth"))
    pth.write_text("python312.zip\n.\n..\nLib\\site-packages\nimport site\n", encoding="utf-8")

    wheels = cache / "wheels"
    if wheels.exists():
        shutil.rmtree(wheels)
    wheels.mkdir()
    pip = ROOT / ".venv" / "bin" / "pip"
    if not pip.exists():
        pip = Path(sys.executable)
        pip_cmd = [str(pip), "-m", "pip"]
    else:
        pip_cmd = [str(pip)]
    run(
        [
            *pip_cmd,
            "download",
            "-d",
            str(wheels),
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            "312",
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            "fastapi==0.116.1",
            "uvicorn==0.35.0",
            "httpx==0.28.1",
            "python-dotenv==1.1.1",
            "httptools",
            "watchfiles",
            "websockets",
            "colorama",
            "pyyaml",
            "sniffio",
        ]
    )
    site = python_dir / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for wheel in sorted(wheels.glob("*.whl")):
        print(f"extract {wheel.name}")
        extract_wheel(wheel, site)

    shutil.copy2(ROOT / "run_app.py", DIST / "run_app.py")
    shutil.copy2(ROOT / "VERSION", DIST / "VERSION")
    shutil.copytree(ROOT / "app", DIST / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "web", DIST / "web")
    shutil.copy2(ROOT / ".env.example", DIST / ".env.example")
    include_env = "--release" not in sys.argv
    if include_env and (ROOT / ".env").exists():
        shutil.copy2(ROOT / ".env", DIST / ".env")

    gcc = shutil.which("x86_64-w64-mingw32-gcc")
    if not gcc:
        raise SystemExit("Chybí x86_64-w64-mingw32-gcc. Nainstaluj mingw-w64.")
    run(
        [
            gcc,
            "-mconsole",
            "-O2",
            "-o",
            str(DIST / "SrealityMonitor.exe"),
            str(ROOT / "packaging" / "launcher.c"),
        ]
    )

    (DIST / "README.txt").write_text(
        "Sreality monitor pro Windows\n\n"
        "1. Vedle SrealityMonitor.exe musí zůstat složky python, app, web a soubor run_app.py.\n"
        "2. Webhook dej do souboru .env (zkopíruj .env.example).\n"
        "3. Spusť SrealityMonitor.exe. Otevře se prohlížeč na http://127.0.0.1:8080\n"
        "4. Zavřením černého okna hlídání vypneš.\n"
        "5. Novou verzi stáhne samo, pokud je v .env vyplněné UPDATE_FEED.\n",
        encoding="utf-8",
    )
    print(f"OK {DIST / 'SrealityMonitor.exe'}")


if __name__ == "__main__":
    main()
