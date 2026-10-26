"""Self-update from GitHub (rotem-ya/Sketch2CAD, branch main).

The app checks the latest commit, and "update now" starts update.bat with no console: it stops the
running app, runs `python -m sketch2cad.updater apply` (download + copy files + pip install when
requirements changed) and starts run.bat again. User data is never touched: settings live in
%APPDATA%/Sketch2CAD (or the data folder named in data_home.txt) and projects in their own folders.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import app_home
from .procutil import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    popen,
    run,
)

REPO = "rotem-ya/Sketch2CAD"
BRANCH = "main"
APP_DIR = Path(__file__).resolve().parents[1]
VERSION_FILE = ".version"
KEEP = {".venv", ".git", VERSION_FILE, "data_home.txt"}   # never overwritten by an update
TIMEOUT_S = 30


def _is_windows() -> bool:
    return os.name == "nt"


@dataclass
class Release:
    sha: str
    date: str
    message: str


def requirements_digest(data: bytes) -> str:
    """Hash of a requirements file. CRLF and LF are the same file (Windows text vs the GitHub zip)."""
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _req_digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return requirements_digest(path.read_bytes())


def installed_version(app_dir: Path = APP_DIR) -> str | None:
    """Commit sha of the installed files (.version, or git HEAD for a clone); None when unknown."""
    f = app_dir / VERSION_FILE
    if f.exists():
        return f.read_text(encoding="utf-8").strip() or None
    if (app_dir / ".git").exists() and shutil.which("git"):
        proc = run(["git", "-C", str(app_dir), "rev-parse", "HEAD"], capture_output=True, text=True)
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
    """Bring app_dir to `sha` (default: latest main); returns the installed sha.

    pip runs only when requirements.txt changed (newline-insensitive hash). An unchanged file,
    including one that only differs by CRLF, does not spawn pip.
    """
    req = app_dir / "requirements.txt"
    old_digest = _req_digest(req)
    if (app_dir / ".git").exists() and shutil.which("git"):
        log("git pull ...")
        run(["git", "-C", str(app_dir), "pull", "--ff-only", "origin", BRANCH],
            check=True, capture_output=True, text=True)
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
    new_digest = _req_digest(req)
    if new_digest is not None and new_digest != old_digest:
        log("Installing new packages ...")
        run([sys.executable, "-m", "pip", "install", "-r", str(req)],
            check=True, capture_output=True, text=True)
    else:
        log("Requirements unchanged; skipping pip.")
    (app_dir / VERSION_FILE).write_text(sha, encoding="utf-8")
    log(f"Updated to {sha[:7]}.")
    return sha


def update_command(app_pid: int, app_dir: Path = APP_DIR) -> list[str]:
    """update.bat <app folder> <server pid> [<supervisor pid>]. Under the launcher the supervisor is stopped
    first (its pid comes in SKETCH2CAD_SUPERVISOR_PID), or it would start the server again mid-update."""
    cmd = ["cmd", "/c", str(app_dir / "update.bat"), str(app_dir), str(app_pid)]
    supervisor = os.environ.get("SKETCH2CAD_SUPERVISOR_PID", "").strip()
    if supervisor.isdigit():
        cmd.append(supervisor)
    return cmd


def start_update_and_restart(app_pid: int, app_dir: Path = APP_DIR) -> None:
    """Windows: run update.bat with no console. It stops this app, updates and restarts it.

    Output goes to <data folder>/logs/update.log. The process is detached so it survives this app
    being stopped. No console window is created.
    """
    if not _is_windows():
        raise OSError("update.bat runs on Windows")
    logs = app_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handle = open(logs / "update.log", "ab")
    kwargs = {
        "cwd": tempfile.gettempdir(),
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    detached = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        try:
            popen(update_command(app_pid, app_dir),
                  extra_flags=detached | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:                                    # job objects that forbid breakaway
            popen(update_command(app_pid, app_dir), extra_flags=detached, **kwargs)
    finally:
        handle.close()


def _log_line(message: str) -> None:
    """Print, and also append to the data-folder log. pythonw has no console, so the file is the record."""
    text = str(message).rstrip()
    print(text)
    try:
        folder = app_home() / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "update.log", "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    if sys.argv[1:] != ["apply"]:
        sys.exit("usage: python -m sketch2cad.updater apply")
    try:
        apply_update(log=_log_line)
    except Exception as exc:  # noqa: BLE001 - the hidden update has no console; keep the reason in the log
        detail = getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or ""
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "ignore")
        _log_line(f"Update failed: {exc}")
        if str(detail).strip():
            _log_line(str(detail).strip())
        sys.exit(1)
