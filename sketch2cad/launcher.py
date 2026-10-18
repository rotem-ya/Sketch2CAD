"""Sketch2CAD as a background program, the way the office's procurement system runs: always on, starts with
Windows, restarts by itself when it falls, and a bookmark or the desktop shortcut just opens the browser.

Not a Windows service: a service runs as LocalSystem in session 0 and cannot see the user's drives (the
Google Drive letter), show the folder picker or open a file in AutoCAD. The server therefore runs in the
user's session, hidden, under a small supervisor; a shortcut in the Startup folder starts it at logon.

Modes (python -m sketch2cad.launcher ...):
  (none)    open: make sure the server runs (start it in the background if not) and open the browser.
  --serve   run the server in the background and keep it running (the Startup shortcut). Exits quietly
            when a server already answers.
  --stop    stop the background server and its supervisor.
  --status  print whether the server answers.
Output of the server and of the supervisor: <data folder>/logs/app.log.
"""
from __future__ import annotations

import os
import signal
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
MAX_FAILED_STARTS = 5                   # consecutive starts without an answer before the supervisor gives up
STABLE_AFTER = 120                      # a server that lived this long resets the restart delay
_sleep = time.sleep


def url(port: int = PORT) -> str:
    return f"http://localhost:{port}"


def server_alive(port: int = PORT, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url(port)}/_stcore/health", timeout=timeout) as r:
            return r.read().strip() == b"ok"
    except OSError:
        return False


def wait_alive(port: int = PORT, timeout: float = START_TIMEOUT, proc=None) -> bool:
    """Poll until the server answers; False on timeout or when `proc` (the server) exits first."""
    deadline = time.monotonic() + timeout
    while True:
        if server_alive(port):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        if time.monotonic() >= deadline:
            return False
        _sleep(0.3)


# -------------------------------------------------------------------------------------- files and logs
def log_file() -> Path:
    logs = app_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "app.log"
    if path.exists() and path.stat().st_size > 5_000_000:          # keep it small: start over
        path.unlink()
    return path


def log(message: str) -> None:
    with open(log_file(), "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} launcher: {message}\n")


def pid_file() -> Path:
    return app_home() / "server.pid"


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python(windowless: bool) -> str:
    """The interpreter of this venv: pythonw.exe for a process that must never show a console, python.exe
    for the server (a console program, started hidden)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        want = "pythonw.exe" if windowless else "python.exe"
        alt = exe.with_name(want)
        if alt.exists():
            return str(alt)
    return str(exe)


def report_error(text: str) -> None:
    """A message box on Windows (there is no console to print to), stderr elsewhere."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "Sketch2CAD", 0x10)
    else:
        print(text, file=sys.stderr)


def _log_tail(n: int = 1500) -> str:
    path = log_file()
    return path.read_text(encoding="utf-8", errors="replace")[-n:].strip() if path.exists() else ""


# --------------------------------------------------------------------------------------- the processes
def start_server(port: int = PORT, log_handle=None) -> subprocess.Popen:
    """The Streamlit server as a hidden child process; its output goes to the log file."""
    handle = log_handle if log_handle is not None else open(log_file(), "ab")    # noqa: SIM115 - the child owns it
    env = {**os.environ, "SKETCH2CAD_SUPERVISOR_PID": str(os.getpid())}
    return subprocess.Popen([_python(windowless=False), "-m", "streamlit", "run", str(APP_PY),
                             "--server.port", str(port), "--server.headless", "true"],
                            cwd=APP_DIR, env=env, stdout=handle, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=_no_window())


def spawn_serve(port: int = PORT) -> None:
    """Start `--serve` as a process of its own that outlives this one."""
    kwargs: dict = {"cwd": APP_DIR, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([_python(windowless=True), "-m", "sketch2cad.launcher", "--serve"], **kwargs)


def _stop(proc, grace: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()


def terminate_pid(pid: int) -> None:
    """Stop another process: taskkill on Windows (SIGTERM cannot be caught there), SIGTERM elsewhere."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=_no_window())
    else:
        os.kill(pid, signal.SIGTERM)


# ---------------------------------------------------------------------------------------------- modes
def serve(port: int = PORT) -> int:
    """Keep the server running until this supervisor is stopped: a server that falls is started again
    (after 2 s, then 4, 8 ... up to 60 s; a server that lived two minutes resets the delay)."""
    if server_alive(port):
        log("serve: a server already answers; nothing to do")
        return 0
    pid_file().write_text(str(os.getpid()), encoding="utf-8")
    state = {"stopping": False, "child": None}

    def on_signal(signum, frame):            # noqa: ARG001 - signal handler signature
        state["stopping"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):        # not the main thread, or unsupported on this platform
            pass
    log(f"serve: supervisor {os.getpid()} started")
    delay, failed_starts, code = 2, 0, 0
    try:
        while not state["stopping"]:
            child = state["child"] = start_server(port)
            started = time.monotonic()
            if wait_alive(port, START_TIMEOUT, child):
                failed_starts = 0
                log(f"serve: server {child.pid} answers at {url(port)}")
            else:
                failed_starts += 1
            while child.poll() is None and not state["stopping"]:
                _sleep(1)
            if state["stopping"]:
                break
            uptime = time.monotonic() - started
            if failed_starts >= MAX_FAILED_STARTS:
                log(f"serve: the server did not answer {failed_starts} times in a row; giving up")
                report_error(f"Sketch2CAD could not start ({failed_starts} attempts).\n\n{_log_tail()}\n\n"
                             f"The full log: {log_file()}")
                code = 1
                break
            if server_alive(port):           # someone else took the port meanwhile (run-debug.bat)
                log("serve: another server answers on the port; this supervisor exits")
                break
            log(f"serve: server exited with code {child.returncode} after {uptime:.0f} s; restarting in {delay} s")
            _sleep(delay)
            delay = 2 if uptime >= STABLE_AFTER else min(delay * 2, 60)
    except KeyboardInterrupt:
        state["stopping"] = True
    finally:
        _stop(state["child"])
        if pid_file().exists() and pid_file().read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_file().unlink()
        log("serve: supervisor stopped")
    return code


def open_app(port: int = PORT) -> int:
    """The desktop shortcut: a server that runs just gets a browser tab; otherwise it is started first."""
    if not server_alive(port):
        spawn_serve(port)
        if not wait_alive(port, START_TIMEOUT):
            report_error(f"Sketch2CAD did not start.\n\n{_log_tail()}\n\nThe full log: {log_file()}")
            return 1
    webbrowser.open(url(port))
    return 0


def stop(port: int = PORT) -> int:
    """Stop the supervisor (so it does not restart the server) and then the server."""
    pid = None
    if pid_file().exists():
        try:
            pid = int(pid_file().read_text(encoding="utf-8").strip())
        except ValueError:
            pid = None
    if pid is not None:
        try:
            terminate_pid(pid)
        except OSError:                      # already gone
            pass
        pid_file().unlink(missing_ok=True)
    deadline = time.monotonic() + 15
    while server_alive(port) and time.monotonic() < deadline:
        _sleep(0.5)
    if server_alive(port):
        print(f"A server still answers at {url(port)}; it was not started by the launcher (run-debug.bat?). "
              "Close its window.")
        return 1
    print("Sketch2CAD is stopped." if pid is not None else "Sketch2CAD was not running.")
    return 0


def status(port: int = PORT) -> int:
    alive = server_alive(port)
    print(f"Sketch2CAD is running at {url(port)}" if alive else "Sketch2CAD is not running.")
    return 0 if alive else 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0] if args else "open"
    if mode == "--serve":
        return serve()
    if mode == "--stop":
        return stop()
    if mode == "--status":
        return status()
    return open_app()


if __name__ == "__main__":
    sys.exit(main())
