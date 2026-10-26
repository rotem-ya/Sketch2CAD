"""Sketch2CAD as a background program, the way the office's procurement system runs: always on, starts with
Windows, restarts by itself when it falls, and a bookmark or the desktop shortcut just opens the browser.

Not a Windows service: a service runs as LocalSystem in session 0 and cannot see the user's drives (the
Google Drive letter), show the folder picker or open a file in AutoCAD. The server therefore runs in the
user's session, hidden, under a small supervisor; a shortcut in the Startup folder starts it at logon.
Every launch repairs that shortcut if it is missing or points at an old folder.

Modes (pythonw -m sketch2cad.launcher ...):
  (none)    open: repair the Startup shortcut, make sure the server runs, and open the browser.
  --serve   run the server in the background and keep it running (the Startup shortcut). Exits quietly
            when a server already answers. A server that stops answering is restarted.
  --stop    stop the background server and its supervisor.
  --status  print whether the server answers.
  --repair  recreate the Startup shortcut, drop a stale server.pid, and start the server if it is down.
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
from .procutil import (
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    popen,
    run,
)

PORT = 8517
APP_DIR = Path(__file__).resolve().parents[1]
APP_PY = APP_DIR / "app" / "app.py"
START_TIMEOUT = 90                      # seconds for the server to answer (the first start after an update is slow)
MAX_FAILED_STARTS = 5                   # consecutive starts without an answer before the supervisor gives up
MAX_WATCHDOG_RESTARTS = 5               # a server that stops answering is restarted this many times, then we give up
STABLE_AFTER = 120                      # a server that lived this long resets the restart delay
HEALTH_INTERVAL = 15                    # seconds between health checks once the server is up
HEALTH_MISS_LIMIT = 3                   # consecutive missed health checks before a restart
SHORTCUT_NAME = "Sketch2CAD server.lnk"
_sleep = time.sleep


def _is_windows() -> bool:
    return os.name == "nt"

# Hidden PowerShell: read the Startup shortcut and rewrite it only when it does not match this install.
_PS_SHORTCUT = r"""
$ErrorActionPreference = 'Stop'
$sh = New-Object -ComObject WScript.Shell
$path = $env:S2C_LNK
$dir = Split-Path -Parent $path
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$curTarget = ''
$curArgs = ''
$curWd = ''
if (Test-Path -LiteralPath $path) {
    $s = $sh.CreateShortcut($path)
    $curTarget = [string]$s.TargetPath
    $curArgs = [string]$s.Arguments
    $curWd = [string]$s.WorkingDirectory
}
function Norm([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return '' }
    try { return ([IO.Path]::GetFullPath($p)).TrimEnd('\').ToLowerInvariant() }
    catch { return $p.Trim().ToLowerInvariant() }
}
$same = (Norm $curTarget) -eq (Norm $env:S2C_TARGET) -and $curArgs -eq $env:S2C_ARGS -and (Norm $curWd) -eq (Norm $env:S2C_WD)
if ($same) { Write-Output 'unchanged'; exit 0 }
$s = $sh.CreateShortcut($path)
$s.TargetPath = $env:S2C_TARGET
$s.Arguments = $env:S2C_ARGS
$s.WorkingDirectory = $env:S2C_WD
if ($env:S2C_ICON) { $s.IconLocation = $env:S2C_ICON }
$s.Description = 'Sketch2CAD background server'
$s.WindowStyle = 7
$s.Save()
Write-Output 'repaired'
"""


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


def _python(windowless: bool) -> str:
    """The interpreter of this venv: pythonw.exe for a process that must never show a console, python.exe
    for the server (a console program, started hidden)."""
    exe = Path(sys.executable)
    if os.name == "nt":
        want = "pythonw.exe" if windowless else "python.exe"
        alt = exe.with_name(want)
        if alt.exists():
            return str(alt)
        venv = APP_DIR / ".venv" / "Scripts" / want
        if venv.exists():
            return str(venv)
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


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists. A dead pid (the usual orphan after a crash) is False."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        handle = kernel.OpenProcess(0x1000, 0, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# Exact interpreter names. A prefix match would also accept an unrelated image.
_OWNED_IMAGES = {"python", "python.exe", "pythonw", "pythonw.exe"}


def _is_owned_image(name: str | None) -> bool:
    """True only for python, python.exe, pythonw, or pythonw.exe."""
    if not name:
        return False
    return Path(name).name.lower() in _OWNED_IMAGES


def _linux_stat_fields(stat: str) -> list[str] | None:
    """Fields of ``/proc/<pid>/stat`` after the comm field (field 3 onward)."""
    end = stat.rfind(")")
    if end < 0:
        return None
    return stat[end + 2:].split()


def _linux_start_token(stat: str) -> str | None:
    """Clock ticks since boot, ``/proc/<pid>/stat`` field 22. None when the line is short."""
    fields = _linux_stat_fields(stat)
    if not fields or len(fields) < 20:
        return None
    token = fields[19]
    return token if token.isdigit() else None


def _same_start(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    if left.isdigit() and right.isdigit():
        return int(left) == int(right)
    return left == right


def _started_after(child: str | None, parent: str | None) -> bool:
    """True when the child process was created after the parent."""
    if not child or not parent:
        return False
    if child.isdigit() and parent.isdigit():
        return int(child) > int(parent)
    return child > parent


def _process_facts(pid: int) -> tuple[str | None, str | None]:
    """``(image basename, start token)``. ``(None, None)`` when the process cannot be read."""
    if not isinstance(pid, int) or pid <= 0:
        return (None, None)
    if os.name == "nt":
        return _windows_process_facts(pid)
    try:
        image = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return (None, None)
    return (image or None, _linux_start_token(stat))


def _child_facts(pid: int) -> list[tuple[int, str | None, str | None]]:
    """Direct children as ``(pid, image, start)``. An unreadable query returns none."""
    if not isinstance(pid, int) or pid <= 0:
        return []
    if os.name == "nt":
        return _windows_child_facts(pid)
    found = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields = _linux_stat_fields(stat)
        if not fields or len(fields) < 2 or fields[1] != str(pid):
            continue
        child = int(entry.name)
        image, started = _process_facts(child)
        found.append((child, image, started))
    return found


def _windows_cim(script: str) -> subprocess.CompletedProcess | None:
    """One hidden PowerShell CIM query. None when it cannot be run."""
    try:
        return run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _windows_process_facts(pid: int) -> tuple[str | None, str | None]:
    script = (
        "try {\r\n"
        f"  $p = Get-CimInstance -ClassName Win32_Process -Filter 'ProcessId = {pid}' -ErrorAction Stop\r\n"
        "} catch {\r\n"
        "  exit 2\r\n"
        "}\r\n"
        "if (-not $p) { exit 0 }\r\n"
        "$when = ''\r\n"
        "if ($p.CreationDate) { $when = ([datetime]$p.CreationDate).ToUniversalTime().ToString('o') }\r\n"
        "Write-Output ($p.Name + \"`t\" + $when)\r\n"
    )
    proc = _windows_cim(script)
    if proc is None or proc.returncode not in (0, None):
        return (None, None)
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return (None, None)
    name, _, started = line[0].partition("\t")
    return (name.strip() or None, started.strip() or None)


def _windows_child_facts(pid: int) -> list[tuple[int, str | None, str | None]]:
    script = (
        "try {\r\n"
        f"  $rows = Get-CimInstance -ClassName Win32_Process -Filter 'ParentProcessId = {pid}' -ErrorAction Stop\r\n"
        "} catch {\r\n"
        "  exit 2\r\n"
        "}\r\n"
        "foreach ($row in @($rows)) {\r\n"
        "  if (-not $row) { continue }\r\n"
        "  $when = ''\r\n"
        "  if ($row.CreationDate) { $when = ([datetime]$row.CreationDate).ToUniversalTime().ToString('o') }\r\n"
        "  Write-Output ($row.ProcessId.ToString() + \"`t\" + $row.Name + \"`t\" + $when)\r\n"
        "}\r\n"
    )
    proc = _windows_cim(script)
    if proc is None or proc.returncode not in (0, None):
        return []
    children = []
    for line in (proc.stdout or "").splitlines():
        pid_text, sep, rest = line.strip().partition("\t")
        if not sep or not pid_text.isdigit():
            continue
        name, _, started = rest.partition("\t")
        children.append((int(pid_text), name.strip() or None, started.strip() or None))
    return children


def _process_is_python(pid: int) -> bool:
    """False only when the process is visibly not Python. Unknown (cannot read the image) stays True,
    so a supervisor we failed to identify is not treated as stale and its pid file is kept."""
    image, _started = _process_facts(pid)
    if image is None:
        return True
    return _is_owned_image(image)


def _owned_tree(root: int, recorded_start: str | None) -> list[int]:
    """Python descendants created after their parent, then the root, children first.

    A child older than its parent is an orphan whose original parent pid was reused. A non-Python
    process is not entered, so its own children are not treated as ours. The root is included only
    when its image is Python and, when the pid file recorded a start time, that time still matches.
    """
    image, started = _process_facts(root)
    if not _is_owned_image(image):
        return []
    if recorded_start is not None and not _same_start(started, recorded_start):
        return []
    ordered: list[int] = []
    seen: set[int] = {root}

    def walk(pid: int, parent_started: str | None) -> None:
        for child, child_image, child_started in _child_facts(pid):
            if child in seen or not _is_owned_image(child_image):
                continue
            if not _started_after(child_started, parent_started):
                continue
            seen.add(child)
            walk(child, child_started)
            ordered.append(child)

    walk(root, started)
    ordered.append(root)
    return ordered


def _parse_pid_record(raw: str) -> tuple[int, str | None] | None:
    parts = raw.split()
    if not parts:
        return None
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    started = parts[1] if len(parts) > 1 and parts[1] else None
    return pid, started


def _write_supervisor_pid() -> None:
    pid = os.getpid()
    _image, started = _process_facts(pid)
    text = str(pid) if not started else f"{pid} {started}"
    pid_file().write_text(text + "\n", encoding="utf-8")


def _supervisor_record() -> tuple[int, str | None] | None:
    """Live supervisor pid and the start time recorded with it.

    A dead pid, a pid that is visibly not Python, or a pid whose start time no longer matches
    (the number was reused) is removed. A process whose image cannot be read is left in the file
    and returned, so a permission error does not drop the only handle we have.
    """
    path = pid_file()
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    parsed = _parse_pid_record(raw)
    if parsed is None:
        path.unlink(missing_ok=True)
        log(f"removed unreadable pid file {raw!r}")
        return None
    pid, recorded = parsed
    if not pid_alive(pid):
        path.unlink(missing_ok=True)
        log(f"removed stale pid {pid}")
        return None
    if not _process_is_python(pid):
        path.unlink(missing_ok=True)
        log(f"removed stale pid {pid}")
        return None
    _image, started = _process_facts(pid)
    if recorded is not None and started is not None and not _same_start(started, recorded):
        path.unlink(missing_ok=True)
        log(f"removed stale pid {pid}")
        return None
    return pid, recorded


def read_live_supervisor() -> int | None:
    """Pid recorded in server.pid when that process is still a live Python. Anything else is removed."""
    record = _supervisor_record()
    return None if record is None else record[0]


# ------------------------------------------------------------------------------------------- shortcut
def startup_folder() -> Path | None:
    """Per-user Startup folder. None off Windows or when APPDATA is missing."""
    if not _is_windows():
        return None
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_spec() -> dict[str, str]:
    """Where the Startup shortcut must point: this install's pythonw, ``--serve``."""
    icon = APP_DIR / "app" / "icon.ico"
    folder = startup_folder()
    return {
        "lnk": str((folder / SHORTCUT_NAME) if folder else SHORTCUT_NAME),
        "target": _python(windowless=True),
        "arguments": "-m sketch2cad.launcher --serve",
        "workdir": str(APP_DIR),
        "icon": str(icon) if icon.is_file() else "",
    }


def _powershell(script: str, env: dict) -> subprocess.CompletedProcess:
    """PowerShell with no console. Shortcut repair has no dialog, so the process window is hidden too."""
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
        capture_output=True, env=env,
    )


def ensure_startup_shortcut() -> str:
    """Create or repair the Startup shortcut so it points at this install.

    Windows only. Idempotent: an already-correct shortcut is left untouched. Returns
    ``skipped`` (not Windows), ``unchanged``, ``repaired``, or ``failed``. A failure is logged
    and does not stop the server from starting.
    """
    if not _is_windows():
        return "skipped"
    spec = shortcut_spec()
    if startup_folder() is None:
        log("shortcut: APPDATA is not set; Startup shortcut not repaired")
        return "failed"
    target = spec["target"]
    if not target.lower().endswith("pythonw.exe"):
        log(f"shortcut: pythonw.exe was not found; the shortcut will use {target}")
    env = os.environ.copy()
    env["S2C_LNK"] = spec["lnk"]
    env["S2C_TARGET"] = target
    env["S2C_ARGS"] = spec["arguments"]
    env["S2C_WD"] = spec["workdir"]
    env["S2C_ICON"] = spec["icon"]
    try:
        proc = _powershell(_PS_SHORTCUT, env)
    except OSError as exc:
        log(f"shortcut: could not run PowerShell: {exc}")
        return "failed"
    text = proc.stdout.decode("utf-8", "ignore").strip().splitlines()
    state = text[-1].strip() if text else ""
    if proc.returncode != 0 or state not in ("unchanged", "repaired"):
        err = proc.stderr.decode("utf-8", "ignore").strip()
        log(f"shortcut: repair failed ({proc.returncode}): {err or state or 'no output'}")
        return "failed"
    if state == "repaired":
        log(f"shortcut: Startup shortcut repaired -> {target}")
    return state


# --------------------------------------------------------------------------------------- the processes
def start_server(port: int = PORT, log_handle=None) -> subprocess.Popen:
    """The Streamlit server as a hidden child process; its output goes to the log file."""
    handle = log_handle if log_handle is not None else open(log_file(), "ab")    # noqa: SIM115 - the child owns it
    env = {**os.environ, "SKETCH2CAD_SUPERVISOR_PID": str(os.getpid())}
    return popen([_python(windowless=False), "-m", "streamlit", "run", str(APP_PY),
                  "--server.port", str(port), "--server.headless", "true"],
                 cwd=APP_DIR, env=env, stdout=handle, stderr=subprocess.STDOUT,
                 stdin=subprocess.DEVNULL)


def spawn_serve(port: int = PORT) -> None:
    """Start `--serve` as a hidden process of its own that outlives this one."""
    kwargs: dict = {"cwd": APP_DIR, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["extra_flags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    popen([_python(windowless=True), "-m", "sketch2cad.launcher", "--serve"], **kwargs)


def _stop(proc, grace: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()


def _kill_succeeded(returncode: int | None, output: str, pid: int) -> bool:
    """True when the process was stopped or was already gone.

    ``taskkill`` returns 128 once a venv wrapper has exited on its own after its child died.
    That is not a refusal. A permission error while the pid is still alive is.
    """
    if returncode in (0, None, 128):
        return True
    if "not found" in (output or "").lower():
        return True
    return not pid_alive(pid)


def terminate_pid(pid: int) -> bool:
    """Stop one verified process. Hidden on Windows. False when the signal is refused."""
    if os.name == "nt":
        proc = run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, check=False)
        output = f"{proc.stdout or ''}{proc.stderr or ''}"
        return _kill_succeeded(proc.returncode, output, pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


# ---------------------------------------------------------------------------------------------- modes
def _claude_tick() -> None:
    """One pass of the local headless runner. A failure here must not stop the server."""
    try:
        from .claude_runner import tick
        tick()
    except Exception as exc:  # noqa: BLE001 - the supervisor stays up if a run cannot start
        log(f"claude runner: {exc}")


def _give_up(reason: str, detail: str) -> None:
    log(reason)
    report_error(f"{detail}\n\n{_log_tail()}\n\nThe full log: {log_file()}")


def serve(port: int = PORT) -> int:
    """Keep the server running until this supervisor is stopped.

    A server that exits is started again (after 2 s, then 4, 8 ... up to 60 s; a server that lived
    two minutes resets the delay). A server that stays up but stops answering the health check is
    killed and started again, up to MAX_WATCHDOG_RESTARTS times.
    """
    ensure_startup_shortcut()
    other = read_live_supervisor()
    if other is not None and other != os.getpid():
        log(f"serve: supervisor {other} is already running; nothing to do")
        return 0
    if server_alive(port):
        log("serve: a server already answers; nothing to do")
        return 0
    _write_supervisor_pid()
    state = {"stopping": False, "child": None}

    def on_signal(signum, frame):            # noqa: ARG001 - signal handler signature
        state["stopping"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):        # not the main thread, or unsupported on this platform
            pass
    log(f"serve: supervisor {os.getpid()} started")
    delay, failed_starts, watchdog_restarts, code = 2, 0, 0, 0
    try:
        while not state["stopping"]:
            child = state["child"] = start_server(port)
            started = time.monotonic()
            if wait_alive(port, START_TIMEOUT, child):
                failed_starts = 0
                log(f"serve: server {child.pid} answers at {url(port)}")
                _claude_tick()
            else:
                failed_starts += 1
            misses = 0
            killed_by_watchdog = False
            next_check = time.monotonic() + HEALTH_INTERVAL
            while child.poll() is None and not state["stopping"]:
                _sleep(1)
                if time.monotonic() < next_check:
                    continue
                next_check = time.monotonic() + HEALTH_INTERVAL
                if server_alive(port):
                    misses = 0
                    _claude_tick()
                    if time.monotonic() - started >= STABLE_AFTER:
                        watchdog_restarts = 0
                        failed_starts = 0
                else:
                    misses += 1
                    log(f"watchdog: server did not answer ({misses}/{HEALTH_MISS_LIMIT})")
                    if misses >= HEALTH_MISS_LIMIT:
                        killed_by_watchdog = True
                        watchdog_restarts += 1
                        _stop(child)
                        if watchdog_restarts < MAX_WATCHDOG_RESTARTS:
                            log("watchdog: server stopped answering; "
                                f"restarting ({watchdog_restarts}/{MAX_WATCHDOG_RESTARTS})")
                        break
            if state["stopping"]:
                break
            uptime = time.monotonic() - started
            if killed_by_watchdog and watchdog_restarts >= MAX_WATCHDOG_RESTARTS:
                _give_up(f"watchdog: gave up after {watchdog_restarts} restarts",
                         f"Sketch2CAD stopped answering and was restarted {watchdog_restarts} times.")
                code = 1
                break
            if not killed_by_watchdog and failed_starts >= MAX_FAILED_STARTS:
                _give_up(f"serve: the server did not answer {failed_starts} times in a row; giving up",
                         f"Sketch2CAD could not start ({failed_starts} attempts).")
                code = 1
                break
            if not killed_by_watchdog and server_alive(port):    # someone else took the port (run-debug.bat)
                log("serve: another server answers on the port; this supervisor exits")
                break
            why = "stopped answering" if killed_by_watchdog else f"exited with code {child.returncode}"
            log(f"serve: server {why} after {uptime:.0f} s; restarting in {delay} s")
            _sleep(delay)
            delay = 2 if uptime >= STABLE_AFTER else min(delay * 2, 60)
    except KeyboardInterrupt:
        state["stopping"] = True
    finally:
        _stop(state["child"])
        if pid_file().exists():
            recorded = _parse_pid_record(pid_file().read_text(encoding="utf-8").strip())
            if recorded is not None and recorded[0] == os.getpid():
                pid_file().unlink()
        log("serve: supervisor stopped")
    return code


def open_app(port: int = PORT) -> int:
    """The desktop shortcut: repair Startup, then a running server just gets a browser tab.

    On Windows the browser is opened with the shell (os.startfile), which does not create a console.
    """
    ensure_startup_shortcut()
    live = read_live_supervisor()
    if not server_alive(port):
        if live is None:
            spawn_serve(port)
        if not wait_alive(port, START_TIMEOUT):
            report_error(f"Sketch2CAD did not start.\n\n{_log_tail()}\n\nThe full log: {log_file()}")
            return 1
    webbrowser.open(url(port))
    return 0


def stop(port: int = PORT) -> int:
    """Stop the supervisor and the server it started.

    ``taskkill`` without the tree leaves the Streamlit child running, and a forced kill does not
    run the supervisor's cleanup. Each pid is killed only after its image is Python and it was
    created after its parent. Children first. A process that has already exited (``taskkill`` 128)
    counts as stopped. A permission error keeps the pid file.
    """
    record = _supervisor_record()
    stopped = False
    if record is not None:
        pid, recorded = record
        owned = _owned_tree(pid, recorded)
        refused = False
        for item in owned:
            try:
                delivered = terminate_pid(item)
            except OSError:
                delivered = False
            if delivered is False:
                refused = True
        if owned and not refused:
            pid_file().unlink(missing_ok=True)
            stopped = True
    deadline = time.monotonic() + 15
    while server_alive(port) and time.monotonic() < deadline:
        _sleep(0.5)
    if server_alive(port):
        print(f"A server still answers at {url(port)}; it was not started by the launcher (run-debug.bat?). "
              "Close its window.")
        return 1
    if stopped:
        print("Sketch2CAD is stopped.")
    elif record is None:
        print("Sketch2CAD was not running.")
    else:
        print("Sketch2CAD's process could not be confirmed, so it was left running.")
    return 0


def status(port: int = PORT) -> int:
    alive = server_alive(port)
    print(f"Sketch2CAD is running at {url(port)}" if alive else "Sketch2CAD is not running.")
    return 0 if alive else 1


def repair(port: int = PORT) -> int:
    """Recreate the Startup shortcut, drop an orphan server.pid, and start the server when it is down."""
    shortcut = ensure_startup_shortcut()
    live = read_live_supervisor()
    log(f"repair: shortcut {shortcut}; supervisor {live if live is not None else 'none'}")
    if server_alive(port):
        print(f"Sketch2CAD is running at {url(port)}; shortcut {shortcut}.")
        return 0
    if live is not None and live != os.getpid():
        print(f"Supervisor {live} is running and will restart the server if needed. Shortcut {shortcut}.")
        return 0
    spawn_serve(port)
    if not wait_alive(port, START_TIMEOUT):
        report_error(f"Sketch2CAD did not start.\n\n{_log_tail()}\n\nThe full log: {log_file()}")
        return 1
    print(f"Sketch2CAD repaired and running at {url(port)}; shortcut {shortcut}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    mode = args[0] if args else "open"
    if mode == "--serve":
        return serve()
    if mode == "--stop":
        return stop()
    if mode == "--status":
        return status()
    if mode == "--repair":
        return repair()
    return open_app()


if __name__ == "__main__":
    sys.exit(main())
