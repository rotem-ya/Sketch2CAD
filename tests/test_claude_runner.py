"""Local headless Claude runner. Process launch is mocked; Linux CI never starts claude.exe."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from sketch2cad import claude_runner as runner
from sketch2cad.bridge import export_to_outbox
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
    "request text",
    "current spec",
    "renders",
    "QA report",
    "Change ONLY what was requested",
    "Keep units, scale, and all other elements",
    "Never drop content",
    "TBC",
    "Never invent numbers",
    "REPORT.md",
    "DONE.txt",
    "-reply.json",
    "no logins",
    "no reading credentials",
    "no network",
    "no git",
    "no email",
    "no deleting files",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


def _drawing(tmp_path):
    project = create_project(tmp_path / "projects", "20117", "חצרים", "Hatzerim")
    doc = create_document(project, title_he="גמל", title_en="Camel", discipline="W", subject="CAMEL")
    (doc.path / "spec.json").write_text(json.dumps(PIPE), encoding="utf-8")
    return project, doc


def _settings(project, **extra):
    values = dict(
        claude_auto_run=True,
        claude_exe=r"C:\Tools\claude.exe",
        claude_accounts=[r"C:\accounts\one", r"C:\accounts\two"],
        claude_timeout_s=60,
        projects=[str(project.path)],
    )
    values.update(extra)
    settings = Settings(**values)
    settings.save()
    return settings


def _patch_launch(monkeypatch):
    launched, stopped, alive = [], [], {}

    def launch(command):
        launched.append(command)
        pid = 500 + len(launched)
        alive[pid] = True
        return pid

    monkeypatch.setattr(runner, "launch_hidden", launch)
    monkeypatch.setattr(runner, "pid_alive", lambda pid: alive.get(pid, False))
    monkeypatch.setattr(runner, "_stop_pid", lambda pid: stopped.append(pid))
    return launched, stopped, alive


def test_task_states_the_rules_and_the_script_clears_the_key(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, _stopped, _alive = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    assert task is not None and task.parent.name == "claude_tasks"
    text = (task / "TASK.md").read_text(encoding="utf-8")
    for phrase in RULES:
        assert phrase in text, phrase
    assert f"{package.name}-reply.json" in text
    script = (task / "run.ps1").read_text(encoding="utf-8")
    assert "Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue" in script
    assert "Set-Location" in script and "UTF8" in script
    assert script.count("--add-dir") >= 2
    assert "--permission-mode acceptEdits" in script
    assert "--allowedTools Read Write Edit *> run.log" in script
    assert str(task) in script
    assert r"C:\accounts\one" in script
    assert launched and launched[0].startswith("conhost.exe --headless")
    assert ("sch" + "tasks") not in script.lower()
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "working"


def test_unconfigured_runner_leaves_the_manual_bridge(tmp_path):
    project, doc = _drawing(tmp_path)
    settings = _settings(project, claude_auto_run=False, claude_accounts=[], claude_exe="")
    package = export_to_outbox(doc.path, request="keep the pipe")
    assert runner.watch_package(package, settings) is None
    runner.tick(settings, projects=[str(project.path)])
    tasks = list((project.path / "agent_exchange" / "claude_tasks").glob("*/TASK.md"))
    assert tasks == []
    assert (package / "request.txt").is_file()


def test_tick_picks_up_a_package_that_lands_after_the_first_look(tmp_path, monkeypatch):
    project, _doc = _drawing(tmp_path)
    settings = _settings(project)
    _patch_launch(monkeypatch)
    exchange = project.path / "agent_exchange"
    (exchange / "outbox").mkdir(parents=True)
    (exchange / "inbox").mkdir()
    runner.tick(settings, projects=[str(project.path)])
    assert (exchange / "claude_tasks" / "seen.txt").is_file()
    package = export_to_outbox(project.path, request="add a section")
    runner.tick(settings, projects=[str(project.path)])
    status = runner.status_for_document(project.path, json.loads(
        (package / "manifest.json").read_text(encoding="utf-8"))["document_code"])
    assert status["status"] == "working"
    assert (Path(status["task"]) / "run.ps1").is_file()


def test_limit_message_retries_the_next_account_once(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, stopped, alive = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    (task / "run.log").write_text("You have hit your session limit\n", encoding="utf-8")
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 2
    assert stopped == [501]
    script = (task / "run.ps1").read_text(encoding="utf-8")
    assert r"C:\accounts\two" in script
    assert r"C:\accounts\one" not in script
    alive[502] = False
    runner.tick(settings, projects=[str(project.path)])
    status = runner.status_for_document(project.path, doc.code)
    assert status["status"] == "failed"
    assert "session limit" in status["log_tail"].lower()
    runner.tick(settings, projects=[str(project.path)])
    assert len(launched) == 2


def test_timeout_retries_once(tmp_path, monkeypatch):
    project, doc = _drawing(tmp_path)
    settings = _settings(project)
    launched, stopped, alive = _patch_launch(monkeypatch)
    package = export_to_outbox(doc.path, request="move the note")
    task = runner.watch_package(package, settings)
    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
    state["started"] = time.time() - 10_000
    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
    alive[501] = True
    runner.tick(settings, projects=[str(project.path)])
    assert stopped == [501]
    assert len(launched) == 2
    assert json.loads((task / "state.json").read_text(encoding="utf-8"))["account_index"] == 1


def test_launch_uses_showwindow_and_headless_conhost(monkeypatch):
    from sketch2cad import procutil
    monkeypatch.setattr(runner.os, "name", "nt")
    monkeypatch.setattr(procutil.os, "name", "nt")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kw"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "0\n4321\n", "")

    monkeypatch.setattr(procutil.subprocess, "run", fake_run)
    command = runner.hidden_launch_command(Path(r"C:\drawings\task folder\run.ps1"))
    assert command.startswith("conhost.exe --headless")
    assert "WindowStyle Hidden" in command
    pid = runner.launch_hidden(command)
    assert pid == 4321
    script = seen["argv"][-1]
    assert "ShowWindow = 0" in script
    assert "conhost.exe --headless" in script
    assert "CREATE_NO_WINDOW" not in script
    assert ("sch" + "tasks") not in script.lower()
    assert seen["kw"]["creationflags"] & procutil.CREATE_NO_WINDOW
    if hasattr(subprocess, "STARTUPINFO"):
        assert seen["kw"]["startupinfo"].wShowWindow == 0


def test_repository_has_no_scheduled_task_command():
    needle = "sch" + "tasks"
    skip = {".git", ".venv", "__pycache__", "node_modules"}
    suffixes = {".py", ".ps1", ".bat", ".md", ".txt", ".json"}
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in skip for part in path.parts) or "codemap" in path.parts:
            continue
        if needle in path.read_text(encoding="utf-8", errors="ignore").lower():
            hits.append(str(path.relative_to(ROOT)))
    assert hits == []


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
