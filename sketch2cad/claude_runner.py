"""Optional local run of headless Claude for one work package.

Off until Settings has auto-run, a claude.exe path (or one found on PATH or in %USERPROFILE%/.local/bin),
and at least one CLAUDE_CONFIG_DIR account folder. The manual bridge is unchanged either way:
the run only writes the reply envelope into the project inbox. The engineer still receives it,
reads the QA report, and applies.

A package that was already in the outbox when this watcher first looked is left alone. A package
sent from the drawing screen is started by ``watch_package``. The launcher calls ``tick`` so a
package that lands while the screen is closed is picked up too. Nothing here schedules a Windows
task; the existing supervisor calls ``tick``.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .config import Settings
from .procutil import run as hidden_run

PROMPT = (
    "You are headless Claude on this PC. Read TASK.md in this folder and carry it out, "
    "including its hard rules. Write the reply JSON, then REPORT.md, then DONE.txt.\n"
)
_LIMITS = ("usage limit", "session limit", "rate limit", "hit your limit", "too many requests")
_LOCK_STALE_S = 120


def detect_claude_exe() -> str:
    """claude.exe on PATH, or under %USERPROFILE%\\.local\\bin. Empty when neither exists."""
    import shutil as _shutil
    for name in ("claude.exe", "claude"):
        found = _shutil.which(name)
        if found:
            return found
    home = os.environ.get("USERPROFILE") or str(Path.home())
    for name in ("claude.exe", "claude"):
        candidate = Path(home) / ".local" / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return ""


def resolve_claude_exe(settings: Settings) -> str:
    chosen = str(getattr(settings, "claude_exe", "") or "").strip()
    return chosen or detect_claude_exe()


def configured(settings: Settings) -> bool:
    """Auto-run is on, an executable was resolved, and there is at least one account folder."""
    return bool(getattr(settings, "claude_auto_run", False) and _accounts(settings) and resolve_claude_exe(settings))


def task_markdown(package_name: str, reply: Path, *, has_qa: bool) -> str:
    """Instructions for the headless run. The precision lines are the office rules."""
    from .bridge import PRECISION_RULES, RETURN_ENVELOPE
    standing = "\n".join(f"- {rule}" for rule in PRECISION_RULES)
    envelope = json.dumps(RETURN_ENVELOPE, ensure_ascii=False, indent=2)
    qa_line = (
        "- `qa_report.json` in this folder — the QA report\n"
        if has_qa else
        "- No QA report was attached. If a value cannot be derived, mark it TBC in questions.\n"
    )
    return (
        "# Headless Claude on this PC\n\n"
        "You are headless Claude on this PC. Read the package and write the reply. Do nothing else.\n\n"
        "## Read\n\n"
        "- `package/request.txt` — the request text. If it is missing, the request is empty.\n"
        "- `package/spec.json` — the current spec. If it is missing, draft one from the sources.\n"
        "- `package/preview.png` and any other rendered sheet in `package/` — the renders\n"
        f"{qa_line}"
        "- `package/AGENT.md`, `package/spec_format.md`, `package/catalog.txt`, `package/manifest.json`\n\n"
        "## Reply\n\n"
        "Write the reply envelope JSON to:\n\n"
        f"`{reply}`\n\n"
        f"The file name is `{package_name}-reply.json` under `agent_exchange/inbox`. "
        "The object has the keys spec, changes, assumptions, and questions. "
        "`spec` is the complete spec, not a diff.\n\n"
        "```json\n"
        f"{envelope}\n"
        "```\n\n"
        "## Precision\n\n"
        "- Change ONLY what was requested.\n"
        "- Keep units, scale, and all other elements.\n"
        "- Never drop content.\n"
        "- Mark underivable values TBC in questions.\n"
        "- Never invent numbers.\n\n"
        f"{standing}\n\n"
        "## Then\n\n"
        "1. Write the reply JSON.\n"
        "2. Then write `REPORT.md` in this folder.\n"
        "3. Then write `DONE.txt` in this folder. `DONE.txt` is the last file you write.\n\n"
        "## Hard rules\n\n"
        "- no logins\n"
        "- no reading credentials\n"
        "- no network\n"
        "- no git\n"
        "- no email\n"
        "- no deleting files\n"
    )


def render_run_ps1(task: Path, exe: str, account: str, inbox: Path) -> str:
    """The script the hidden process runs. It clears a leftover model key from the environment."""
    task_dir = str(task)
    return "\r\n".join([
        "Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue",
        f"$env:CLAUDE_CONFIG_DIR = {_ps_quote(account)}",
        f"Set-Location -LiteralPath {_ps_quote(task_dir)}",
        f"$p = Get-Content -LiteralPath {_ps_quote(str(Path(task_dir) / 'p.txt'))} -Raw -Encoding UTF8",
        (
            f"& {_ps_quote(exe)} -p $p --permission-mode acceptEdits "
            f"--add-dir {_ps_quote(task_dir)} --add-dir {_ps_quote(str(inbox))} "
            "--allowedTools Read Write Edit *> run.log"
        ),
        "",
    ])


def hidden_launch_command(run_ps1: Path) -> str:
    """Command line passed to Win32_Process.Create. conhost --headless never shows a console."""
    return (
        "conhost.exe --headless powershell.exe -NoProfile -NonInteractive "
        "-WindowStyle Hidden -ExecutionPolicy Bypass -File "
        + _win_quote(str(run_ps1))
    )


def wmi_launcher_script(command: str) -> str:
    """PowerShell that starts `command` with ShowWindow 0. No process-creation flag is set."""
    safe = command.replace("'", "''")
    return (
        "$startup = ([wmiclass]'Win32_ProcessStartup').CreateInstance()\r\n"
        "$startup.ShowWindow = 0\r\n"
        f"$commandLine = '{safe}'\r\n"
        "$created = ([wmiclass]'Win32_Process').Create($commandLine, $null, $startup)\r\n"
        "Write-Output $created.ReturnValue\r\n"
        "Write-Output $created.ProcessId\r\n"
    )


def launch_hidden(command: str) -> int:
    """Start `command` with no window. Returns the new process id.

    The process is created by WMI so the window stays hidden. The PowerShell that calls WMI
    is a normal hidden subprocess (no console). WMI is not given a creation flag: that call
    fails with return value 21.
    """
    if os.name != "nt":
        raise OSError("hidden Claude launch is only available on Windows")
    script = wmi_launcher_script(command)
    proc = hidden_run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, check=False,
    )
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    detail = (proc.stderr or "").strip()
    if proc.returncode != 0 or len(lines) < 2 or lines[0] != "0":
        raise OSError(f"hidden launch failed ({proc.returncode}): {lines[:1] or detail}")
    return int(lines[1])


def watch_package(package: Path | str, settings: Settings | None = None) -> Path | None:
    """Queue one outbox package and start it when no other run is active. None when not configured."""
    package = Path(package).resolve()
    if not (package / "manifest.json").is_file():
        return None
    settings = settings or Settings.load()
    if not configured(settings):
        return None
    exchange = package.parent.parent
    with _lock(exchange) as got:
        if not got:
            return _task_for(exchange, package.name)
        existing = _task_for(exchange, package.name)
        if existing is not None:
            return existing
        task = _create_task(package, settings)
        seen_path = _seen_path(exchange)
        if seen_path.exists():
            seen = _read_names(seen_path)
        else:
            seen = {item.name for item in _packages(exchange)}
        seen.add(package.name)
        _write_names(seen_path, seen)
        _pump(exchange, settings, time.time())
        return task


def tick(settings: Settings | None = None, projects: list | None = None) -> None:
    """Advance queued and running tasks. Picks up a package that landed after the first look."""
    settings = settings or Settings.load()
    for root in _project_roots(settings, projects):
        exchange = root / "agent_exchange"
        if not exchange.is_dir():
            continue
        with _lock(exchange) as got:
            if not got:
                continue
            _sync(exchange, settings)
            _pump(exchange, settings, time.time())


def status_for_document(project_dir: Path | str, document_code: str) -> dict | None:
    """Runner status for the drawing's newest package, or None when no run was started."""
    project_dir = Path(project_dir)
    outbox = project_dir / "agent_exchange" / "outbox"
    if not outbox.is_dir():
        return None
    packages = []
    for folder in outbox.iterdir():
        manifest_path = folder / "manifest.json"
        if not folder.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("document_code") == document_code:
            packages.append(folder)
    if not packages:
        return None
    package = sorted(packages, key=lambda path: path.name)[-1]
    task = _task_for(project_dir / "agent_exchange", package.name)
    if task is None:
        return None
    state = _read_state(task)
    status = str(state.get("status") or "queued")
    tail = str(state.get("log_tail") or "")
    if status == "failed" and not tail:
        tail = _log_tail(task)
    return {"status": status, "log_tail": tail, "package": package.name, "task": str(task)}


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    from .launcher import pid_alive as live
    return live(pid)


# -------------------------------------------------------------------------------------------- internals
def _accounts(settings: Settings) -> list[str]:
    raw = getattr(settings, "claude_accounts", None) or []
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _timeout(settings: Settings) -> int:
    try:
        return max(30, int(getattr(settings, "claude_timeout_s", 600)))
    except (TypeError, ValueError):
        return 600


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _win_quote(value: str) -> str:
    if not value or any(ch in value for ch in ' \t"'):
        return '"' + value.replace('"', r"\"") + '"'
    return value


def _packages(exchange: Path) -> list[Path]:
    outbox = exchange / "outbox"
    if not outbox.is_dir():
        return []
    found = [folder for folder in outbox.iterdir() if folder.is_dir() and (folder / "manifest.json").is_file()]
    found.sort(key=lambda path: path.name)
    return found


def _seen_path(exchange: Path) -> Path:
    return exchange / "claude_tasks" / "seen.txt"


def _read_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _write_names(path: Path, names: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(sorted(names))
    path.write_text((text + "\n") if text else "", encoding="utf-8")


def _task_dirs(exchange: Path) -> list[Path]:
    root = exchange / "claude_tasks"
    if not root.is_dir():
        return []
    found = [path for path in root.iterdir() if path.is_dir() and (path / "state.json").is_file()]
    found.sort(key=lambda path: path.name)
    return found


def _task_for(exchange: Path, package_name: str) -> Path | None:
    matches = [task for task in _task_dirs(exchange) if _read_state(task).get("package") == package_name]
    return matches[-1] if matches else None


def _read_state(task: Path) -> dict:
    path = task / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(task: Path, state: dict) -> None:
    _write_json(task / "state.json", state)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _create_task(package: Path, settings: Settings) -> Path:
    exchange = package.parent.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    task = exchange / "claude_tasks" / f"{stamp}-{package.name}"
    task.mkdir(parents=True)
    try:
        shutil.copytree(package, task / "package")
        _copy_qa_report(package, task)
        inbox = exchange / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        reply = inbox / f"{package.name}-reply.json"
        has_qa = (task / "qa_report.json").is_file()
        (task / "TASK.md").write_text(
            task_markdown(package.name, reply, has_qa=has_qa), encoding="utf-8")
        (task / "p.txt").write_text(PROMPT, encoding="utf-8")
        account = _accounts(settings)[0]
        (task / "run.ps1").write_text(
            render_run_ps1(task, resolve_claude_exe(settings), account, inbox), encoding="utf-8")
        _write_state(task, {
            "package": package.name,
            "status": "queued",
            "account_index": 0,
            "retried": False,
            "pid": 0,
            "started": 0,
            "timeout_s": _timeout(settings),
            "log_tail": "",
        })
    except Exception:
        shutil.rmtree(task, ignore_errors=True)
        raise
    return task


def _copy_qa_report(package: Path, task: Path) -> None:
    """Newest trial report for this drawing, when a previous receive already wrote one."""
    try:
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(manifest, dict):
        return
    code = manifest.get("document_code")
    if not code:
        return
    project_dir = package.resolve().parents[2]
    try:
        from .documents import list_documents
        from .projects import Project
        project = Project.load(project_dir)
    except (OSError, json.JSONDecodeError, ValueError):
        return
    newest = None
    for doc in list_documents(project):
        if doc.code != code:
            continue
        trials = doc.path / "bridge_trials"
        if not trials.is_dir():
            return
        reports = [path for path in trials.glob("*/report.json") if path.is_file()]
        if reports:
            newest = max(reports, key=lambda path: (path.stat().st_mtime, path.name))
        break
    if newest is not None:
        shutil.copy2(newest, task / "qa_report.json")


def _sync(exchange: Path, settings: Settings) -> None:
    """Remember packages already sitting in the outbox. Only a later arrival is queued."""
    packages = _packages(exchange)
    path = _seen_path(exchange)
    names = {item.name for item in packages}
    if not path.exists():
        _write_names(path, names)
        return
    seen = _read_names(path)
    if not configured(settings):
        extra = names - seen
        if extra:
            _write_names(path, seen | extra)
        return
    changed = False
    for package in packages:
        if package.name in seen or _task_for(exchange, package.name) is not None:
            continue
        _create_task(package, settings)
        seen.add(package.name)
        changed = True
    if changed:
        _write_names(path, seen)


def _pump(exchange: Path, settings: Settings, now: float) -> None:
    for task in _task_dirs(exchange):
        _observe(task, settings, now)
    if _has_working(exchange) or _other_working(settings, exchange):
        return
    if configured(settings):
        _launch_oldest(exchange, settings, now)


def _has_working(exchange: Path) -> bool:
    return any(_read_state(task).get("status") == "working" for task in _task_dirs(exchange))


def _other_working(settings: Settings, exchange: Path) -> bool:
    here = exchange.resolve()
    for root in _project_roots(settings, None):
        other = root / "agent_exchange"
        if not other.is_dir() or other.resolve() == here:
            continue
        if _has_working(other):
            return True
    return False


def _observe(task: Path, settings: Settings, now: float) -> None:
    state = _read_state(task)
    if not state:
        return
    if (task / "DONE.txt").is_file():
        if state.get("status") != "ready":
            state["status"] = "ready"
            state["pid"] = 0
            _write_state(task, state)
        return
    if state.get("status") != "working":
        return
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    log = _current_log(task)
    limited = _limit_hit(log)
    alive = pid_alive(pid) if pid else False
    try:
        started = float(state.get("started") or 0)
        timeout_s = int(state.get("timeout_s") or _timeout(settings))
    except (TypeError, ValueError):
        started, timeout_s = 0, _timeout(settings)
    timed_out = started > 0 and (now - started) >= timeout_s
    if alive and not limited and not timed_out:
        return
    if alive and pid:
        _stop_pid(pid)
    _finish_attempt(task, state, settings)


def _finish_attempt(task: Path, state: dict, settings: Settings) -> None:
    accounts = _accounts(settings)
    try:
        index = int(state.get("account_index") or 0)
    except (TypeError, ValueError):
        index = 0
    if not state.get("retried") and index + 1 < len(accounts):
        _archive_log(task)
        state["retried"] = True
        state["account_index"] = index + 1
        state["status"] = "queued"
        state["pid"] = 0
        state["started"] = 0
        _write_state(task, state)
        return
    state["status"] = "failed"
    state["pid"] = 0
    state["log_tail"] = _log_tail(task)
    _write_state(task, state)


def _launch_oldest(exchange: Path, settings: Settings, now: float) -> bool:
    queued = [task for task in _task_dirs(exchange) if _read_state(task).get("status") == "queued"]
    if not queued:
        return False
    _launch(queued[0], settings, now)
    return True


def _launch(task: Path, settings: Settings, now: float) -> None:
    state = _read_state(task)
    accounts = _accounts(settings)
    try:
        index = int(state.get("account_index") or 0)
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(accounts):
        state["status"] = "failed"
        state["log_tail"] = _log_tail(task) or "no account folder"
        _write_state(task, state)
        return
    inbox = task.parent.parent / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (task / "run.ps1").write_text(
        render_run_ps1(task, resolve_claude_exe(settings), accounts[index], inbox),
        encoding="utf-8")
    try:
        pid = launch_hidden(hidden_launch_command(task / "run.ps1"))
    except OSError as exc:
        (task / "run.log").write_text(str(exc) + "\n", encoding="utf-8")
        pid = 0
    state["status"] = "working"
    state["pid"] = int(pid or 0)
    state["started"] = now
    state["timeout_s"] = _timeout(settings)
    _write_state(task, state)


def _current_log(task: Path) -> str:
    path = task / "run.log"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _archive_log(task: Path) -> None:
    path = task / "run.log"
    if not path.is_file():
        return
    previous = task / "run.previous.log"
    if previous.exists():
        previous.unlink()
    path.replace(previous)


def _log_tail(task: Path, limit: int = 2000) -> str:
    chunks = []
    for name in ("run.previous.log", "run.log"):
        path = task / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)[-limit:]


def _limit_hit(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _LIMITS)


def _stop_pid(pid: int) -> None:
    if not isinstance(pid, int) or pid <= 0:
        return
    if os.name == "nt":
        hidden_run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _project_roots(settings: Settings, projects: list | None) -> list[Path]:
    raw = list(projects or []) + list(getattr(settings, "projects", None) or [])
    roots, seen = [], set()
    for item in raw:
        path = Path(item)
        key = str(path)
        if key in seen or not (path / "project.json").is_file():
            continue
        seen.add(key)
        roots.append(path)
    return roots


@contextmanager
def _lock(exchange: Path):
    root = exchange / "claude_tasks"
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".lock"
    acquired = False
    try:
        path.mkdir()
        acquired = True
    except FileExistsError:
        try:
            stale = time.time() - path.stat().st_mtime > _LOCK_STALE_S
        except OSError:
            stale = True
        if stale:
            try:
                path.rmdir()
            except OSError:
                pass
            try:
                path.mkdir()
                acquired = True
            except FileExistsError:
                acquired = False
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            path.rmdir()
        except OSError:
            pass
