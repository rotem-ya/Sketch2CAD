"""Start Sketch2CAD like a desktop program: no console window, the app in a window of its own.

Flow: start the Streamlit server hidden (its output goes to <data folder>/logs/app.log) -> wait until it
answers -> open an app window (Edge or Chrome in --app mode with a profile of their own, so the window has
its own taskbar button and closing it ends the program; without either, the default browser) -> watch both:
the window closes -> the server is stopped; the server stops (quit button, update) -> the window is closed.
When the server is already running (the icon was clicked twice) only a window is opened on it.

Inside the server, start_idle_watchdog() is the safety net: once every window has been closed for a while,
the server stops by itself (also covers the default-browser case, where no window process can be watched).

Started by the desktop shortcut and by run.bat:  .venv\\Scripts\\pythonw.exe -m sketch2cad.launcher
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .config import app_home

PORT = 8517
APP_DIR = Path(__file__).resolve().parents[1]
APP_PY = APP_DIR / "app" / "app.py"
START_TIMEOUT = 90                      # seconds for the server to answer (the first start after an update is slow)
IDLE_GRACE = 30                         # seconds without any open window before the server stops itself
IDLE_STARTUP = 300                      # seconds to wait for the first window before giving up
WINDOW_SIZE = "1500,950"
# Edge first (it is on every Windows 10/11), then Chrome; otherwise the default browser.
BROWSER_PATHS = (
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)
BROWSER_NAMES = ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome")     # on PATH
_sleep = time.sleep


def url(port: int = PORT) -> str:
    return f"http://localhost:{port}"


def server_alive(port: int = PORT, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url(port)}/_stcore/health", timeout=timeout) as r:
            return r.read().strip() == b"ok"
    except OSError:
        return False


def find_browser() -> str | None:
    """Edge or Chrome: SKETCH2CAD_BROWSER when set, the usual install folders, then PATH."""
    override = os.environ.get("SKETCH2CAD_BROWSER")
    if override:
        return override if Path(override).is_file() else shutil.which(override)
    for candidate in BROWSER_PATHS:
        full = os.path.expandvars(candidate)
        if "%" not in full and Path(full).is_file():
            return full
    for name in BROWSER_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def log_file() -> Path:
    logs = app_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "app.log"
    if path.exists() and path.stat().st_size > 5_000_000:          # keep it small: start over
        path.unlink()
    return path


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def start_server(port: int = PORT, log=None) -> subprocess.Popen:
    """The Streamlit server as a hidden child process; its output goes to the log file."""
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":                      # the server is a console program, hidden below
        python = python.with_name("python.exe")
    handle = log if log is not None else open(log_file(), "ab")    # noqa: SIM115 - the child owns the handle
    env = {**os.environ, "SKETCH2CAD_LAUNCHER": "1"}
    return subprocess.Popen([str(python), "-m", "streamlit", "run", str(APP_PY), "--server.port", str(port),
                             "--server.headless", "true"],
                            cwd=APP_DIR, env=env, stdout=handle, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=_no_window())


def wait_until_alive(proc, port: int = PORT, timeout: float = START_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_alive(port):
            return True
        if proc.poll() is not None:                                # the server quit (port in use, import error)
            return False
        _sleep(0.3)
    return False


def open_window(target: str, port: int = PORT) -> subprocess.Popen | None:
    """A window of its own in Edge/Chrome (app mode, own profile); None when the default browser was used."""
    browser = find_browser()
    if browser is None:
        webbrowser.open(target)
        return None
    profile = app_home() / "browser"
    args = [browser, f"--app={target}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-background-mode", f"--window-size={WINDOW_SIZE}"]
    args += os.environ.get("SKETCH2CAD_BROWSER_ARGS", "").split()
    return subprocess.Popen(args, creationflags=_no_window(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def report_error(text: str) -> None:
    """A message box on Windows (there is no console to print to), stderr elsewhere."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Sketch2CAD", 0x10)
    else:
        print(text, file=sys.stderr)


def _stop(proc, grace: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()


def supervise(server, window) -> None:
    """Until one of them ends: the window closed -> stop the server; the server stopped -> close the window."""
    while True:
        if window is not None and window.poll() is not None:
            _stop(server)
            return
        if server.poll() is not None:
            _stop(window)
            return
        _sleep(0.5)


def main() -> int:
    port = PORT
    if server_alive(port):                                         # already running: another window on it
        window = open_window(url(port), port)
        if window is not None:
            window.wait()
        return 0
    log = log_file()
    with open(log, "ab") as handle:
        handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} Sketch2CAD start\n".encode())
    server = start_server(port)
    if not wait_until_alive(server, port):
        _stop(server)
        tail = log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else ""
        report_error(f"Sketch2CAD did not start.\n\n{tail.strip()}\n\nThe full log: {log}")
        return 1
    window = open_window(url(port), port)
    supervise(server, window)
    return 0


# ------------------------------------------------------------------------------ inside the server
class IdleTimer:
    """Decides when a server nobody looks at should stop: `grace` consecutive idle ticks after a window was
    seen, or `startup` ticks without any window at all. Ticks rather than clock time: a sleeping computer
    does not tick, so waking up while the browser is still reconnecting cannot stop the server."""

    def __init__(self, grace: int = 6, startup: int = 60):
        self.grace, self.startup = grace, startup
        self.seen, self.idle, self.ticks = False, 0, 0

    def tick(self, sessions: int) -> bool:
        """True when the server should exit."""
        self.ticks += 1
        if sessions:
            self.seen, self.idle = True, 0
            return False
        if not self.seen:
            return self.ticks >= self.startup
        self.idle += 1
        return self.idle >= self.grace


def start_idle_watchdog() -> bool:
    """Inside the Streamlit server, when the launcher started it: stop once every window has been closed
    for IDLE_GRACE seconds (IdleTimer). Returns False when not started by the launcher."""
    if not os.environ.get("SKETCH2CAD_LAUNCHER"):
        return False
    import threading

    from streamlit import runtime
    grace_s = float(os.environ.get("SKETCH2CAD_IDLE_GRACE", IDLE_GRACE))
    interval = min(5.0, grace_s / 3)
    timer = IdleTimer(grace=max(1, round(grace_s / interval)), startup=max(1, round(IDLE_STARTUP / interval)))

    def watch() -> None:
        while True:
            time.sleep(interval)
            try:
                sessions = runtime.get_instance()._session_mgr.num_active_sessions()
            except Exception:  # noqa: BLE001 - the private API changed: live without the watchdog
                return
            if timer.tick(sessions):
                os._exit(0)

    threading.Thread(target=watch, name="idle-watchdog", daemon=True).start()
    return True


if __name__ == "__main__":
    sys.exit(main())
