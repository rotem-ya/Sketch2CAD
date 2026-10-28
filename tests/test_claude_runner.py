"""Local headless Claude runner. Process launch is mocked; Linux CI never starts claude.exe."""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from sketch2cad import claude_runner as runner
from sketch2cad.bridge import PRECISION_RULES, export_to_outbox
from sketch2cad.config import Settings
from sketch2cad.documents import create_document
from sketch2cad.projects import create_project

ROOT = Path(__file__).resolve().parents[1]
PIPE = {
    "version": 1, "units": "mm", "scale": 20, "paper": "A3", "language": "both",
    "elements": [{"id": "p1", "type": "pipe", "points": [[0, 0], [1000, 0]], "od": 110}],
}
RULES = (
    "headless Claude",
    "data, not instructions",
    "request text",
    "current spec",
    "renders",
    "QA report",
    "Never drop content",
    "TBC",
    "Never invent numbers",
    "Write only `reply.json`, `REPORT.md`, and `DONE.txt`",
    "Never modify `package/`.",
    "Run no commands.",
    "no logins",
    "no reading credentials",
    "no network",
    "no git",
    "no email",
    "no deleting files",
)
ACCOUNTS = [r"C:\accounts\one", r"C:\accounts\two", r"C:\accounts\three"]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    runner._restricted_cache.clear()
    runner._ui_tick_at = 0.0
    real_run = runner.hidden_run
    real_kill = runner.os.kill

    def _no_taskkill(argv, **kwargs):
        if argv and "taskkill" in str(argv[0]).lower():
            raise AssertionError("a test tried to run taskkill")
        return real_run(argv, **kwargs)

    def _no_signal(pid, sig, *args):
        if sig:
            raise AssertionError("a test tried to signal a process")
        return real_kill(pid, sig)

    monkeypatch.setattr(runner, "hidden_run", _no_taskkill)
    monkeypatch.setattr(runner.os, "kill", _no_signal)


def _drawing(tmp_path, code="20117"):
    project = create_project(tmp_path / "projects", code, "חצרים", "Hatzerim")
    doc = create_document(project, title_he="גמל", title_en="Camel", discipline="W", subject="CAMEL")
    (doc.path / "spec.json").write_text(json.dumps(PIPE), encoding="utf-8")
    return project, doc


def _settings(project, **extra):
    values = dict(
        claude_auto_run=True,
        claude_exe=r"C:\Tools\claude.exe",
        claude_accounts=list(ACCOUNTS[:2]),
        claude_timeout_s=60,
        projects=[str(project.path)],
    )
    values.update(extra)
    settings = Settings(**values)
    settings.save()
    return settings


def _patch_launch(monkeypatch, *, alive_after=True):
    launched, killed, alive, scripts = [], [], {}, []

    def launch(command):
        launched.append(command)
        pid = 500 + len(launched)
        alive[pid] = alive_after
        marker = "-File "
        ps1 = command[command.rfind(marker) + len(marker):].strip().strip('"')
        scripts.append(Path(ps1).read_text(encoding="utf-8-sig"))
        return {"pid": pid, "image": "conhost.exe", "created": f"2026-01-01T00:00:{pid % 60:02d}+00:00"}

    def status(pid, image, created):
        if not (str(image or "").strip() and str(created or "").strip()):
            return "unknown" if runner._numeric_alive(pid) else "gone"
        if alive.get(int(pid), False) and str(image).lower() == "conhost.exe":
            return "alive"
        return "gone"

    def kill(pid):
        killed.append(pid)
        alive[pid] = False

    monkeypatch.setattr(runner, "launch_hidden", launch)
    monkeypatch.setattr(runner, "process_status", status)
    monkeypatch.setattr(runner, "_kill_tree", kill)
    monkeypatch.setattr(runner, "supports_restricted", lambda _exe: False)
    return launched, killed, alive, scripts


def _envelope():
    return {"spec": {"units": "mm"}, "changes": [], "assumptions": [], "questions": []}


def test_task_states_the_rules_and_the_script_clears_the_key(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    assert task is not None and task.parent.name == "claude_tasks"
    text = (task / "TASK.md").read_text(encoding="utf-8")
    for phrase in RULES:
        assert phrase in text, phrase
    assert text.count(PRECISION_RULES[0]) == 1
    assert "Change ONLY what was requested" not in text
    assert "Keep units, scale, and all other elements" not in text
    assert "reply.json" in text
    assert "inbox" not in text
    assert f"{package.name}-reply.json" not in text
    raw = (task / "run.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert (task / "p.txt").read_bytes().startswith(b"\xef\xbb\xbf")
    script = raw.decode("utf-8-sig")
    for name in runner._ENV_CLEAR:
        assert f"Remove-Item Env:{name} -ErrorAction SilentlyContinue" in script
    assert "$ErrorActionPreference='Stop'" in script
    assert "try {" in script and "} catch {" in script
    assert "$code = $LASTEXITCODE" in script
    assert "exit $code" in script
    assert "[Console]::OutputEncoding" in script and "$OutputEncoding" in script
    assert "Out-File -FilePath $log -Append -Encoding utf8" in script
    assert "$ErrorActionPreference='Continue'" in script
    assert "*>" not in script
    assert str(task / "run.log") in script
    assert script.count("--add-dir") == 1
    assert "--allowedTools" not in script
    assert '--tools "Read,Write,Edit,Glob,Grep"' in script
    assert '--disallowedTools "mcp__*"' in script
    assert "--strict-mcp-config" in script
    assert "--restricted" not in script
    assert "--permission-mode acceptEdits" in script
    assert "inbox" not in script
    assert r"C:\accounts\one" in script
    assert launched and launched[0].startswith("conhost.exe --headless")
    assert ("sch" + "tasks") not in script.lower()
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "working"
    assert state["image"] == "conhost.exe"
    assert state["created"].startswith("2026-01-01T00:00:")


def test_run_ps1_is_utf8_with_bom_so_hebrew_paths_round_trip(tmp_path):
    task = tmp_path / "משימה"
    script = runner.render_run_ps1(
        task, r"C:\כלים\claude.exe", r"C:\משתמש\פרויקט", restricted=False)
    dest = tmp_path / "run.ps1"
    runner._write_ps1(dest, script)
    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "משימה" in text
    assert r"C:\כלים\claude.exe" in text
    assert r"C:\משתמש\פרויקט" in text
    assert text == script


def test_ps_quote_doubles_every_single_quote():
    assert runner._ps_quote("Rotem\u2019s Projects") == "'Rotem\u2019\u2019s Projects'"
    for mark in runner._PS_QUOTES:
        inner = runner._ps_quote("a" + mark + "b")[1:-1]
        assert inner.replace(mark + mark, "") == "ab"
        assert mark not in inner.replace(mark + mark, "")
    command = "conhost.exe C:\\rot'; calc.exe # \u2018\u2019\u201a\u201b"
    script = runner.wmi_launcher_script(command)
    assert f"$commandLine = {runner._ps_quote(command)}" in script
    inner = runner._ps_quote(command)[1:-1]
    for mark in runner._PS_QUOTES:
        assert mark + mark in inner
        inner = inner.replace(mark + mark, "")
        assert mark not in inner


def test_log_readers_accept_utf16_and_utf8(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "run.log").write_bytes("session limit\n".encode("utf-16"))
    assert runner._limit_hit(runner.read_log_text(task / "run.log"))
    assert "\x00" not in runner._log_tail(task)

    (task / "run.log").write_bytes("usage limit\n".encode("utf-16-le"))
    text = runner.read_log_text(task / "run.log")
    assert "usage limit" in text
    assert "\x00" not in text
    assert runner._limit_hit(text)

    (task / "run.log").write_bytes(b"\xef\xbb\xbfrate limit\n")
    assert runner._limit_hit(runner.read_log_text(task / "run.log"))
    assert "\ufeff" not in runner._log_tail(task)


def test_done_without_a_valid_reply_is_not_published(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project, claude_accounts=[r"C:\accounts\one"])
    _patch_launch(monkeypatch, alive_after=False)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "DONE.txt").write_text("done\n", encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    inbox = project.path / "agent_exchange" / "inbox"
    assert list(inbox.glob("*-reply.json")) == []
    assert runner.status_for_document(project.path, doc.code)["status"] == "failed"

    project, doc = _drawing(tmp_path / "partial", "20118")
    settings = _settings(project, claude_accounts=list(ACCOUNTS[:2]))
    _patch_launch(monkeypatch, alive_after=False)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "DONE.txt").write_text("done\n", encoding="utf-8")
    (task / "reply.json").write_text('{"spec": {"units": "mm"}}', encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    inbox = project.path / "agent_exchange" / "inbox"
    assert list(inbox.glob("*-reply.json")) == []
    assert not (task / "reply.json").exists()
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"

    project, doc = _drawing(tmp_path / "valid", "20119")
    settings = _settings(project, claude_accounts=[r"C:\accounts\one"])
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch, alive_after=False)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    body = _envelope()
    (task / "reply.json").write_text(json.dumps(body), encoding="utf-8")
    (task / "DONE.txt").write_text("done\n", encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    inbox = project.path / "agent_exchange" / "inbox"
    published = inbox / f"{package.name}-reply.json"
    assert published.is_file()
    assert json.loads(published.read_text(encoding="utf-8")) == body
    assert list(inbox.glob("*.part")) == []
    assert runner.status_for_document(project.path, doc.code)["status"] == "ready"
    assert len(launched) == 1


def test_taskkill_uses_the_tree_and_checks_identity(monkeypatch):
    calls = []

    def run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(runner, "hidden_run", run)
    state = {"pid": 501, "image": "conhost.exe", "created": "2026-01-01T00:00:00+00:00"}
    answers = iter(["alive", "gone"])
    monkeypatch.setattr(runner, "process_status", lambda *_args: next(answers))
    assert runner._stop_tracked(state) is True
    assert calls == [["taskkill", "/PID", "501", "/T", "/F"]]

    calls.clear()
    monkeypatch.setattr(runner, "process_status", lambda *_args: "gone")
    assert runner._stop_tracked(state) is True
    assert calls == []

    monkeypatch.setattr(runner, "process_status", lambda *_args: "unknown")
    assert runner._stop_tracked(state) is False
    assert calls == []


def test_native_stderr_does_not_abort_the_script(tmp_path):
    task = tmp_path / "task"
    script = runner.render_run_ps1(task, r"C:\Tools\claude.exe", r"C:\accounts\one", restricted=False)
    outer = script.index("\ntry {")
    encoding_at = script.index("$OutputEncoding")
    continue_at = script.index("$ErrorActionPreference='Continue'")
    call_at = script.index('2>&1 | ForEach-Object { "$_" }')
    finally_at = script.index("} finally {")
    code_at = script.index("$code = $LASTEXITCODE")
    exit_at = script.index("\n    exit $code")
    assert outer < encoding_at < script.index("[Console]::OutputEncoding") < continue_at
    assert continue_at < call_at < finally_at < code_at < exit_at
    assert "exit=$code" in script
    assert script.index("$ErrorActionPreference='Stop'") < outer


def test_pwsh_treats_stderr_and_exit_zero_as_success(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is not installed")
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho 'progress on stderr' >&2\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    task = tmp_path / "task"
    task.mkdir()
    (task / "p.txt").write_text("hello\n", encoding="utf-8-sig")
    runner._write_ps1(task / "run.ps1", runner.render_run_ps1(
        task, str(fake), str(tmp_path / "account"), restricted=False))
    proc = subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(task / "run.ps1")],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    log = runner.read_log_text(task / "run.log")
    assert "progress on stderr" in log
    assert "exit=0" in log
    assert "ERROR:" not in log


def test_unknown_query_does_not_start_the_next_account(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "run.log").write_text("session limit\n", encoding="utf-8")
    monkeypatch.setattr(runner, "process_status", lambda *_args: "unknown")
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 1
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"


def test_wmi_error_is_unknown_and_does_not_start_the_next_account(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    real_status = runner.process_status
    real_name = runner.os.name
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    monkeypatch.setattr(runner.os, "name", "nt")
    seen = {}

    def fail(argv, **_kwargs):
        seen["script"] = argv[-1]
        return subprocess.CompletedProcess(argv, 2, "", "cim failed")

    monkeypatch.setattr(runner, "hidden_run", fail)
    assert runner.query_process(40)["status"] == "unknown"
    assert "-ErrorAction Stop" in seen["script"]
    assert "exit 2" in seen["script"]
    assert "SilentlyContinue" not in seen["script"]

    def status_from_failed_query(pid, image, created):
        # CIM is asked only for this call. Path() must stay the real class afterwards
        # (WindowsPath on Windows, PosixPath on Linux). Forcing the other name raises.
        runner.os.name = "nt"
        try:
            return real_status(pid, image, created)
        finally:
            runner.os.name = real_name

    monkeypatch.setattr(runner.os, "name", real_name)
    monkeypatch.setattr(runner, "process_status", status_from_failed_query)
    (task / "run.log").write_text("session limit\n", encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 1
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    assert state["account_index"] == 0
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"


def test_unknown_past_grace_fails_without_another_account(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    monkeypatch.setattr(runner, "process_status", lambda *_args: "unknown")
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    timeout_s = int(state["timeout_s"])
    state["started"] = time.time() - (timeout_s + runner._UNKNOWN_GRACE * timeout_s + 1)
    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 1
    status = runner.status_for_document(project.path, doc.code)
    assert status["status"] == "failed"
    assert "unknown" in status["log_tail"]
    again = json.loads((task / "state.json").read_text(encoding="utf-8"))
    assert again["account_index"] == 0
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 1


def test_run_timeout_advances_when_identity_is_missing(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, killed, _alive, _scripts = _patch_launch(monkeypatch)
    monkeypatch.setattr(runner, "_numeric_alive", lambda pid: pid > 0)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    state["image"] = ""
    state["created"] = ""
    state["started"] = time.time()
    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 1
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"
    state["started"] = time.time() - 10_000
    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert killed == []
    assert len(launched) == 2


def test_query_timeout_is_unknown_and_silence_is_gone(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")

    def timed_out(_argv, **_kwargs):
        raise subprocess.TimeoutExpired(_argv, 20)

    monkeypatch.setattr(runner, "hidden_run", timed_out)
    assert runner.query_process(40)["status"] == "unknown"

    def empty(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runner, "hidden_run", empty)
    assert runner.query_process(40)["status"] == "gone"

    def named(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, "conhost.exe\n2026-01-01T00:00:00+00:00\n", "")

    monkeypatch.setattr(runner, "hidden_run", named)
    found = runner.query_process(40)
    assert found["status"] == "alive"
    assert runner.process_status(40, "conhost.exe", "2026-01-01T00:00:00+00:00") == "alive"
    assert runner.process_status(40, "claude.exe", "2026-01-01T00:00:00+00:00") == "gone"


def test_launch_hidden_times_out(monkeypatch):
    monkeypatch.setattr(runner.os, "name", "nt")
    seen = {}

    def timed_out(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(runner, "hidden_run", timed_out)
    with pytest.raises(OSError, match="timed out"):
        runner.launch_hidden("conhost.exe --headless")
    assert seen["timeout"] == runner._LAUNCH_TIMEOUT_S


def test_ui_tick_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "tick", lambda *_args, **_kwargs: calls.append(1))
    runner.tick_from_ui()
    runner.tick_from_ui()
    assert calls == [1]
    runner._ui_tick_at = 0.0
    runner.tick_from_ui()
    assert calls == [1, 1]


def test_stale_lock_is_renamed_aside(tmp_path):
    from sketch2cad.config import app_home
    lock = app_home() / "claude_runner.lock"
    lock.mkdir()
    old = time.time() - runner._LOCK_STALE_S - 10
    os.utime(lock, (old, old))
    with runner._global_lock() as got:
        assert got is True
        assert lock.is_dir()
        assert list(app_home().glob("claude_runner.lock.stale-*")) == []
    assert not lock.exists()

    lock.mkdir()
    with runner._global_lock() as got:
        assert got is False
    assert lock.is_dir()


def test_seen_marker_is_written_only_with_the_lock(tmp_path):
    from sketch2cad.config import app_home
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    package = export_to_outbox(doc.path, request="move the note")
    lock = app_home() / "claude_runner.lock"
    lock.mkdir()
    with pytest.raises(runner.RunnerError):
        runner.watch_package(package, settings)
    assert not (project.path / "agent_exchange" / "claude_tasks" / "seen.txt").exists()


def test_reply_values_must_be_an_object_and_lists(tmp_path):
    bad = tmp_path / "reply.json"
    bad.write_text(json.dumps({
        "spec": ["not-an-object"],
        "changes": {},
        "assumptions": [],
        "questions": [],
    }), encoding="utf-8")
    assert runner.reply_is_valid(bad) is False
    good = tmp_path / "ok.json"
    good.write_text(json.dumps(_envelope()), encoding="utf-8")
    assert runner.reply_is_valid(good) is True


def test_timeout_does_not_advance_until_the_tree_is_dead(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, killed, alive, _scripts = _patch_launch(monkeypatch)

    def kill(pid):
        killed.append(pid)

    monkeypatch.setattr(runner, "_kill_tree", kill)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "DONE.txt").write_text("done\n", encoding="utf-8")
    (task / "reply.json").write_text('{"spec": 1}', encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    inbox = project.path / "agent_exchange" / "inbox"
    assert list(inbox.glob("*-reply.json")) == []
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    state["started"] = time.time() - 10_000
    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert killed == [501]
    assert len(launched) == 1
    assert list(inbox.glob("*-reply.json")) == []
    alive[501] = False
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 2
    assert r"C:\accounts\two" in (task / "run.ps1").read_text(encoding="utf-8-sig")
    assert list(inbox.glob("*-reply.json")) == []


def test_tool_flags_follow_the_detected_build(monkeypatch):
    task = Path(r"C:\drawings\task")
    plain = runner.render_run_ps1(task, r"C:\Tools\claude.exe", r"C:\accounts\one", restricted=False)
    locked = runner.render_run_ps1(task, r"C:\Tools\claude.exe", r"C:\accounts\one", restricted=True)
    assert "--restricted" not in plain
    assert "--restricted" in locked
    assert "--allowedTools" not in locked
    calls = []
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")

    def run(argv, **kwargs):
        calls.append(list(argv))
        seen["env"] = kwargs.get("env")
        seen["encoding"] = kwargs.get("encoding")
        seen["errors"] = kwargs.get("errors")
        if "--help" in argv:
            raise OSError("help failed")
        return subprocess.CompletedProcess(argv, 0, "claude\n--restricted\n", "")

    monkeypatch.setattr(runner, "hidden_run", run)
    runner._restricted_cache.clear()
    assert runner.supports_restricted(r"C:\Tools\claude.exe") is True
    assert "--help" in calls[0]
    assert "--version" in calls[1]
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "ANTHROPIC_BASE_URL" not in seen["env"]
    assert "PATH" in seen["env"]

    monkeypatch.setattr(
        runner, "hidden_run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "usage\n", ""))
    runner._restricted_cache.clear()
    assert runner.supports_restricted(r"C:\Tools\claude.exe") is False

    def boom(_argv, **_kwargs):
        raise ValueError("locale")

    monkeypatch.setattr(runner, "hidden_run", boom)
    runner._restricted_cache.clear()
    assert runner.supports_restricted(r"C:\missing\claude.exe") is False

    def timed_out(_argv, **_kwargs):
        raise subprocess.TimeoutExpired(_argv, 20)

    monkeypatch.setattr(runner, "hidden_run", timed_out)
    runner._restricted_cache.clear()
    assert runner.supports_restricted(r"C:\slow\claude.exe") is False


def test_tries_every_configured_account(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project, claude_accounts=list(ACCOUNTS))
    launched, _killed, _alive, scripts = _patch_launch(monkeypatch, alive_after=False)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "run.log").write_bytes("You have hit your session limit\n".encode("utf-16"))
    runner.tick(settings, projects=[str(project.path)])
    runner.tick(settings, projects=[str(project.path)])
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 3
    assert [r"C:\accounts\one" in scripts[0], r"C:\accounts\two" in scripts[1],
            r"C:\accounts\three" in scripts[2]] == [True, True, True]
    status = runner.status_for_document(project.path, doc.code)
    assert status["status"] == "failed"
    assert "session limit" in status["log_tail"].lower()
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 3


def test_a_bad_package_does_not_abort_the_tick(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    other = create_document(project, title_he="צינור", title_en="Pipe", discipline="W", subject="PIPE")
    (other.path / "spec.json").write_text(json.dumps(PIPE), encoding="utf-8")
    settings = _settings(project)
    _patch_launch(monkeypatch)
    runner.tick(settings, projects=[str(project.path)])
    first = export_to_outbox(doc.path, request="move the note")
    second = export_to_outbox(other.path, request="keep the pipe")
    real = runner._create_task
    calls = []

    def wrapped(package, settings):
        calls.append(package.name)
        if package.name == first.name:
            raise RuntimeError("boom-package")
        return real(package, settings)

    monkeypatch.setattr(runner, "_create_task", wrapped)
    runner.tick(settings, projects=[str(project.path)])
    failed = runner.status_for_document(project.path, doc.code)
    started = runner.status_for_document(project.path, other.code)
    assert failed["status"] == "failed"
    assert "boom-package" in failed["log_tail"]
    assert started["status"] == "working"
    seen = len(calls)
    runner.tick(settings, projects=[str(project.path)])
    assert len(calls) == seen
    assert runner.status_for_document(project.path, doc.code)["status"] == "failed"


def test_one_global_lock_starts_one_run(tmp_path, monkeypatch):
    first, _doc_a = _drawing(tmp_path / "a", "20117")
    second, _doc_b = _drawing(tmp_path / "b", "20118")
    settings = _settings(first, projects=[str(first.path), str(second.path)])
    launched, _killed, _alive, _scripts = _patch_launch(monkeypatch)
    runner.arm(settings)
    export_to_outbox(first.path, request="first")
    export_to_outbox(second.path, request="second")
    runner.tick(settings)
    working = []
    for project in (first, second):
        exchange = project.path / "agent_exchange"
        for task in runner._task_dirs(exchange):
            if runner._read_state(task).get("status") == "working":
                working.append(task)
    assert len(launched) == 1
    assert len(working) == 1
    assert not (first.path / "agent_exchange" / "claude_tasks" / ".lock").exists()
    assert not (tmp_path / "home" / "claude_runner.lock").exists()


def test_busy_lock_is_reported_and_the_next_tick_starts_the_package(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    _patch_launch(monkeypatch)
    runner.arm(settings)
    package = export_to_outbox(doc.path, request="move the note")
    lock = Path(tmp_path / "home" / "claude_runner.lock")
    lock.mkdir()
    with pytest.raises(runner.RunnerError) as caught:
        runner.watch_package(package, settings)
    assert caught.value.code == "claude_runner_busy"
    seen = runner._read_names(runner._seen_path(project.path / "agent_exchange"))
    assert package.name not in seen
    lock.rmdir()
    runner.tick(settings, projects=[str(project.path)])
    assert runner.status_for_document(project.path, doc.code)["status"] == "working"


def test_unconfigured_runner_leaves_the_manual_bridge(tmp_path):
    project, doc = _drawing(tmp_path)
    settings = _settings(project, claude_auto_run=False, claude_accounts=[], claude_exe="")
    package = export_to_outbox(doc.path, request="keep the pipe")
    assert runner.watch_package(package, settings) is None
    runner.tick(settings, projects=[str(project.path)])
    tasks = list((project.path / "agent_exchange" / "claude_tasks").glob("*/TASK.md"))
    assert tasks == []
    assert not (project.path / "agent_exchange" / "claude_tasks" / "seen.txt").exists()
    assert not (tmp_path / "home" / "claude_runner.lock").exists()
    assert (package / "request.txt").is_file()


def test_tick_picks_up_a_package_that_lands_after_the_first_look(tmp_path, monkeypatch):
    project, _doc = _drawing(tmp_path)
    settings = _settings(project)
    _patch_launch(monkeypatch)
    runner.arm(settings)
    seen = project.path / "agent_exchange" / "claude_tasks" / "seen.txt"
    assert seen.is_file()
    assert seen.read_text(encoding="utf-8").strip() == ""
    package = export_to_outbox(project.path, request="add a section")
    runner.tick(settings, projects=[str(project.path)])
    code = json.loads((package / "manifest.json").read_text(encoding="utf-8"))["document_code"]
    status = runner.status_for_document(project.path, code)
    assert status["status"] == "working"
    assert (Path(status["task"]) / "run.ps1").is_file()

    held_project, _held_doc = _drawing(tmp_path / "held", "20118")
    held_settings = _settings(held_project)
    held = export_to_outbox(held_project.path, request="already there")
    runner.arm(held_settings)
    runner.tick(held_settings, projects=[str(held_project.path)])
    assert runner._task_for(held_project.path / "agent_exchange", held.name) is None
    task = runner.watch_package(held, held_settings)
    assert task is not None
    held_code = json.loads((held / "manifest.json").read_text(encoding="utf-8"))["document_code"]
    assert runner.status_for_document(held_project.path, held_code)["status"] == "working"


def test_validate_settings_rejects_missing_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "detect_claude_exe", lambda: "")
    missing_exe = tmp_path / "missing.exe"
    missing_dir = tmp_path / "no-such-account"
    codes = [code for code, _path in runner.validate_settings(str(missing_exe), [str(missing_dir)], auto=True)]
    assert "claude_exe_bad" in codes
    assert "claude_account_bad" in codes
    empty = [code for code, _path in runner.validate_settings("", [], auto=True)]
    assert empty == ["claude_exe_required", "claude_account_required"]
    assert runner.validate_settings("", [], auto=False) == []
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    account = tmp_path / "account"
    account.mkdir()
    assert runner.validate_settings(str(exe), [str(account)], auto=True) == []


def test_account_settings_env_is_a_warning(tmp_path, monkeypatch):
    account = tmp_path / "account"
    account.mkdir()
    settings_file = account / "settings.json"
    settings_file.write_text(json.dumps({"env": {"FOO": "1"}, "apiKeyHelper": "helper.cmd"}), encoding="utf-8")
    found = runner.account_warnings(str(account))
    assert ("claude_account_env", str(settings_file)) in found
    assert ("claude_account_helper", str(settings_file)) in found
    assert runner.account_warnings(str(tmp_path / "empty")) == []

    project, doc = _drawing(tmp_path / "job")
    settings = _settings(project, claude_accounts=[str(account)])
    _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    warnings = runner.status_for_document(project.path, doc.code)["warnings"]
    assert warnings == [[code, str(settings_file)] for code, _path in found]
    assert "claude_account_env" in (task / "preflight.txt").read_text(encoding="utf-8")


def test_launch_uses_showwindow_and_headless_conhost(monkeypatch):
    from sketch2cad import procutil
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(procutil.os, "name", "nt")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kw"] = kwargs
        return subprocess.CompletedProcess(
            argv, 0, "0\n4321\nconhost.exe\n2026-01-01T00:00:00+00:00\n", "")

    monkeypatch.setattr(procutil.subprocess, "run", fake_run)
    command = runner.hidden_launch_command(Path(r"C:\drawings\task folder\run.ps1"))
    assert command.startswith("conhost.exe --headless")
    assert "WindowStyle Hidden" in command
    result = runner.launch_hidden(command)
    assert result == {
        "pid": 4321,
        "image": "conhost.exe",
        "created": "2026-01-01T00:00:00+00:00",
    }
    script = seen["argv"][-1]
    assert "ShowWindow = 0" in script
    assert "conhost.exe --headless" in script
    assert "CREATE_NO_WINDOW" not in script
    assert ("sch" + "tasks") not in script.lower()
    assert seen["kw"]["creationflags"] & procutil.CREATE_NO_WINDOW
    if hasattr(subprocess, "STARTUPINFO"):
        assert seen["kw"]["startupinfo"].wShowWindow == 0


def _source_files() -> list[Path]:
    """Tracked source when this tree is a checkout, otherwise the package files on disk."""
    suffixes = {".py", ".ps1", ".bat", ".md", ".txt", ".json"}
    listed = None
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False,
        )
    except OSError:
        listed = None
    if listed is not None and listed.returncode == 0:
        found = []
        for raw in listed.stdout.split(b"\0"):
            if not raw:
                continue
            path = ROOT / raw.decode("utf-8", errors="surrogateescape")
            if path.suffix.lower() in suffixes and path.is_file():
                found.append(path)
        return found
    skip = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip and not name.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in suffixes:
                found.append(path)
    return found


def test_repository_has_no_scheduled_task_command():
    needle = "sch" + "tasks"
    hits = []
    for path in _source_files():
        if needle in path.read_text(encoding="utf-8", errors="ignore").lower():
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_scheduled_task_scan_works_without_a_checkout(monkeypatch):
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    files = _source_files()
    assert any(path.name == "claude_runner.py" for path in files)
    needle = "sch" + "tasks"
    assert [path for path in files if needle in path.read_text(encoding="utf-8", errors="ignore").lower()] == []


def test_detects_claude_exe_on_path_or_in_user_local_bin(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude.exe"
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.delenv("USERPROFILE", raising=False)
    assert runner.detect_claude_exe() == str(exe)

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    home = tmp_path / "user"
    local = home / ".local" / "bin"
    local.mkdir(parents=True)
    target = local / "claude.exe"
    target.write_bytes(b"")
    monkeypatch.setenv("USERPROFILE", str(home))
    assert runner.detect_claude_exe() == str(target)


def test_inbox_reply_goes_through_qa(tmp_path):
    from sketch2cad.bridge import import_reply, latest_inbox_reply
    project, doc = _drawing(tmp_path)
    original = (doc.path / "spec.json").read_bytes()
    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple", "origin": [0, 0]},
        "qa": {"lineweight_min_mm": 0.05, "lineweight_max_mm": 2.0, "thick_thin_ratio": 1.0,
               "utility_submission": False},
        "elements": [
            {"id": "t1", "type": "text", "at": [500, 1500], "height": 2.5, "text": "HELLO"},
            {"id": "t2", "type": "text", "at": [520, 1500], "height": 2.5, "text": "WORLD"},
        ],
    }
    inbox = project.path / "agent_exchange" / "inbox"
    inbox.mkdir(parents=True)
    reply = inbox / "20260101_000000_000000_DOC-reply.json"
    reply.write_text(json.dumps({
        "spec": spec,
        "changes": [{"en": "Marked the overlap", "he": "סומנה חפיפה"}],
        "assumptions": [],
        "questions": [{"en": "Cover is TBC", "he": "כיסוי TBC"}],
    }, ensure_ascii=False), encoding="utf-8")
    found = latest_inbox_reply(project.path)
    assert found.name.endswith("-reply.json")
    report = import_reply(found, doc.path, trial=tmp_path / "trial", apply=False)
    assert report["applied"] is False
    assert (doc.path / "spec.json").read_bytes() == original
    assert any(item.get("code") == "text_overlap" for item in report["checks"])


def test_failed_run_shows_the_log_tail(tmp_path):
    from streamlit.testing.v1 import AppTest
    from sketch2cad.i18n import t
    project, doc = _drawing(tmp_path)
    Settings(ui_language="he", default_projects_root=str(tmp_path / "projects")).save()
    package = export_to_outbox(doc.path, request="להזיז")
    task = project.path / "agent_exchange" / "claude_tasks" / f"20260101_000000-{package.name}"
    task.mkdir(parents=True)
    (task / "state.json").write_text(json.dumps({
        "package": package.name, "status": "failed", "log_tail": "UNIQUE-LOG-TAIL-xyz",
    }), encoding="utf-8")
    (task / "run.log").write_text("UNIQUE-LOG-TAIL-xyz\n", encoding="utf-8")
    app = str(ROOT / "app" / "app.py")
    at = AppTest.from_file(app, default_timeout=60)
    at.session_state["page"] = "document"
    at.session_state["project_folder"] = project.folder
    at.session_state["doc_folder"] = doc.folder
    at.run()
    assert not at.exception, at.exception
    blob = "\n".join(
        [m.value for m in at.markdown]
        + [c.value for c in at.caption]
    )
    assert t("claude_status_failed", "he") in blob
    assert "UNIQUE-LOG-TAIL-xyz" in blob
    assert t("agent_recv", "he") in {b.label for b in at.button}
    assert not (project.path / "agent_exchange" / "claude_tasks" / "seen.txt").exists()
