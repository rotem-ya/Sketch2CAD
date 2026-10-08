"""Self-update from GitHub (rotem-ya/Sketch2CAD, branch main).

The app checks the latest commit, and "update now" starts update.bat in its own window: it stops the
running app, runs `python -m sketch2cad.updater apply` (download + copy files + pip install when
requirements changed) and starts run.bat again. User data is never touched: settings live in
%APPDATA%/Sketch2CAD and projects in their own folders.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = "rotem-ya/Sketch2CAD"
BRANCH = "main"
APP_DIR = Path(__file__).resolve().parents[1]
VERSION_FILE = ".version"
KEEP = {".venv", ".git", VERSION_FILE}                 # never overwritten by an update
TIMEOUT_S = 30


@dataclass
class Release:
    sha: str
    date: str
    message: str


def installed_version(app_dir: Path = APP_DIR) -> str | None:
    """Commit sha of the installed files (.version, or git HEAD for a clone); None when unknown."""
    f = app_dir / VERSION_FILE
    if f.exists():
        return f.read_text(encoding="utf-8").strip() or None
    if (app_dir / ".git").exists() and shutil.which("git"):
        proc = subprocess.run(["git", "-C", str(app_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
        return proc.stdout.strip() or None
    return None


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Sketch2CAD-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def latest(timeout: float = 5) -> Release:
    """Latest commit on the main branch (GitHub API, no login needed for a public repo)."""
    data = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}", timeout))
    commit = data["commit"]
    return Release(sha=data["sha"], date=commit["committer"]["date"][:10],
                   message=commit["message"].splitlines()[0])


def update_available(release: Release, app_dir: Path = APP_DIR) -> bool:
    return installed_version(app_dir) != release.sha


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in KEEP:
            continue
        target = dst / item.name
        if item.is_dir():
            target.mkdir(exist_ok=True)
            _copy_tree(item, target)
        else:
            shutil.copy2(item, target)


def apply_update(app_dir: Path = APP_DIR, sha: str | None = None, *, log=print, download=_get) -> str:
    """Bring app_dir to `sha` (default: latest main); returns the installed sha."""
    old_req = (app_dir / "requirements.txt").read_bytes() if (app_dir / "requirements.txt").exists() else b""
    if (app_dir / ".git").exists() and shutil.which("git"):
        log("git pull ...")
        subprocess.run(["git", "-C", str(app_dir), "pull", "--ff-only", "origin", BRANCH], check=True)
        sha = installed_version(app_dir)
    else:
        sha = sha or latest(TIMEOUT_S).sha
        log(f"Downloading version {sha[:7]} ...")
        data = download(f"https://github.com/{REPO}/archive/{sha}.zip", 300)
        with tempfile.TemporaryDirectory(prefix="s2c_update_") as tmp:
            zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
            root = next(p for p in Path(tmp).iterdir() if p.is_dir())   # Sketch2CAD-<sha>/
            log("Copying files ...")
            _copy_tree(root, app_dir)
    if (app_dir / "requirements.txt").read_bytes() != old_req:
        log("Installing new packages ...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(app_dir / "requirements.txt")],
                       check=True)
    (app_dir / VERSION_FILE).write_text(sha, encoding="utf-8")
    log(f"Updated to {sha[:7]}.")
    return sha


def start_update_and_restart(app_pid: int, app_dir: Path = APP_DIR) -> None:
    """Windows: run update.bat in its own console; it stops this app (app_pid), updates and restarts it."""
    flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB
    try:
        subprocess.Popen(["cmd", "/c", str(app_dir / "update.bat"), str(app_dir), str(app_pid)],
                         creationflags=flags, cwd=tempfile.gettempdir())
    except OSError:                                        # job objects that forbid breakaway
        subprocess.Popen(["cmd", "/c", str(app_dir / "update.bat"), str(app_dir), str(app_pid)],
                         creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=tempfile.gettempdir())


if __name__ == "__main__":
    if sys.argv[1:] != ["apply"]:
        sys.exit("usage: python -m sketch2cad.updater apply")
    try:
        apply_update()
    except Exception as exc:  # noqa: BLE001 - report any failure to the console window
        print(f"Update failed: {exc}")
        sys.exit(1)
