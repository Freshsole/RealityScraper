#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)


def bump(kind: str, current: str) -> str:
    major, minor, patch = (int(part) for part in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> None:
    current = VERSION_FILE.read_text(encoding="utf-8").strip()
    arg = sys.argv[1] if len(sys.argv) > 1 else "patch"
    version = bump(arg, current) if arg in {"patch", "minor", "major"} else arg
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    print(f"verze {current} → {version}")

    run([sys.executable, str(ROOT / "packaging" / "build_windows.py"), "--release"])
    dist_dir = ROOT / "dist"
    folder = dist_dir / "SrealityMonitor"
    zip_path = dist_dir / "SrealityMonitor-windows.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(dist_dir / "SrealityMonitor-windows"), "zip", dist_dir, "SrealityMonitor")
    (dist_dir / "latest.json").write_text(
        json.dumps({"version": version, "url": ""}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"zip {zip_path}")

    repo = "Freshsole/RealityScraper"
    remote = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True,
        text=True,
    )
    if remote.returncode != 0:
        print(f"GitHub repo {repo} ještě není. Vytvoř ho a pak:")
        print(f"  gh release create v{version} {zip_path} --repo {repo} --title v{version} --notes 'Sreality monitor {version}'")
        return
    run(
        [
            "gh",
            "release",
            "create",
            f"v{version}",
            str(zip_path),
            "--title",
            f"v{version}",
            "--notes",
            f"Sreality monitor {version}",
            "--repo",
            repo,
        ]
    )
    print(f"release v{version} na {repo}")


if __name__ == "__main__":
    main()
