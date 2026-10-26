"""The background launcher: serve (keep the server running, restart it when it falls, give up after repeated
failures), open (start if needed, then the browser), stop (supervisor first), and the update command."""
import os

import pytest

from sketch2cad import launcher, updater

_REAL_ENSURE = launcher.ensure_startup_shortcut


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launcher, "_sleep", lambda s: None)
    monkeypatch.setattr(launcher, "START_TIMEOUT", 0)        # wait_alive never waits on the clock
    # Never touch the real Startup folder or start PowerShell from these tests.
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", lambda: "skipped")


class FakeProc:
    """poll() returns the exit code after `exits_after` polls (or after terminate()); `interrupt_at` raises
    KeyboardInterrupt on that poll, like Ctrl+C in a console."""
    def __init__(self, exits_after=None, code=0, interrupt_at=None, pid=100):
        self.polls, self.exits_after, self.code, self.interrupt_at, self.pid = 0, exits_after, code, interrupt_at, pid
        self.terminated = self.killed = False
        self.returncode = None

    def poll(self):
        self.polls += 1
        if self.interrupt_at is not None and self.polls == self.interrupt_at:
            raise KeyboardInterrupt
        if self.terminated or (self.exits_after is not None and self.polls > self.exits_after):
            self.returncode = self.code
            return self.code
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


def _alive(monkeypatch, answers):
    seq = iter(answers)
    monkeypatch.setattr(launcher, "server_alive", lambda port=launcher.PORT, timeout=1.0: next(seq, answers[-1]))


def _servers(monkeypatch, procs):
    it = iter(procs)
    started = []
    def fake_start(port=launcher.PORT, log_handle=None):
        started.append(next(it))
        return started[-1]
    monkeypatch.setattr(launcher, "start_server", fake_start)
    return started


# ------------------------------------------------------------------------------------------- serve
def test_serve_restarts_a_fallen_server_and_stops_on_interrupt(monkeypatch):
    crashed = FakeProc(exits_after=3, code=1, pid=11)              # answers, then dies
    second = FakeProc(interrupt_at=4, pid=12)                      # answers, then Ctrl+C
    started = _servers(monkeypatch, [crashed, second])
    # not running -> first server answers -> (after the crash) nobody answers -> second answers
    _alive(monkeypatch, [False, True, False, True])
    assert launcher.serve() == 0
    assert started == [crashed, second] and second.terminated
    assert not launcher.pid_file().exists()
    text = launcher.log_file().read_text(encoding="utf-8")
    assert "server 11 answers" in text and "exited with code 1" in text and "restarting in 2 s" in text
    assert "supervisor stopped" in text


def test_serve_does_nothing_when_a_server_already_answers(monkeypatch):
    started = _servers(monkeypatch, [])
    _alive(monkeypatch, [True])
    assert launcher.serve() == 0 and started == [] and not launcher.pid_file().exists()


def test_serve_gives_up_after_repeated_failed_starts(monkeypatch):
    procs = [FakeProc(exits_after=0, code=1, pid=20 + i) for i in range(launcher.MAX_FAILED_STARTS)]
    started = _servers(monkeypatch, procs)
    _alive(monkeypatch, [False])
    errors = []
    monkeypatch.setattr(launcher, "report_error", errors.append)
    assert launcher.serve() == 1
    assert len(started) == launcher.MAX_FAILED_STARTS and "5 attempts" in errors[0]
    assert str(launcher.log_file()) in errors[0]


def test_serve_yields_to_another_server_on_the_port(monkeypatch):
    started = _servers(monkeypatch, [FakeProc(exits_after=2, code=1)])
    _alive(monkeypatch, [False, True, True])                       # after the crash someone else answers
    assert launcher.serve() == 0 and len(started) == 1
    assert "another server answers" in launcher.log_file().read_text(encoding="utf-8")


# -------------------------------------------------------------------------------------------- open
def test_open_starts_the_background_server_then_the_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: calls.append("serve"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: calls.append(u))
    _alive(monkeypatch, [False, True])
    assert launcher.main([]) == 0 and calls == ["serve", "http://localhost:8517"]


def test_open_with_a_running_server_only_opens_the_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: calls.append("serve"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: calls.append(u))
    _alive(monkeypatch, [True])
    assert launcher.main([]) == 0 and calls == ["http://localhost:8517"]


def test_open_reports_a_server_that_does_not_come_up(monkeypatch):
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: pytest.fail("no browser without a server"))
    errors = []
    monkeypatch.setattr(launcher, "report_error", errors.append)
    launcher.log_file().write_text("Port 8517 is already in use", encoding="utf-8")
    _alive(monkeypatch, [False])
    assert launcher.main([]) == 1 and "already in use" in errors[0]


# -------------------------------------------------------------------------------------- stop/status
def test_stop_terminates_the_supervisor_from_the_pid_file(monkeypatch, capsys):
    launcher.pid_file().write_text("4242", encoding="utf-8")
    killed = []
    monkeypatch.setattr(launcher, "terminate_pid", killed.append)
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: True)
    monkeypatch.setattr(launcher, "_process_is_python", lambda pid: True)
    monkeypatch.setattr(launcher, "_image_basename", lambda pid: "pythonw.exe" if pid == 4242 else None)
    monkeypatch.setattr(launcher, "_child_pids", lambda pid: [])
    _alive(monkeypatch, [True, False])
    assert launcher.main(["--stop"]) == 0 and killed == [4242] and not launcher.pid_file().exists()
    assert "stopped" in capsys.readouterr().out


def test_stop_kills_the_server_tree_only_after_the_image_matches(monkeypatch, capsys):
    """The supervisor's child server is stopped too. A reused pid with another image is not."""
    launcher.pid_file().write_text("4242", encoding="utf-8")
    images = {4242: "pythonw.exe", 5001: "python.exe", 5002: "notepad.exe"}
    children = {4242: [5001, 5002], 5001: [], 5002: []}
    killed = []
    monkeypatch.setattr(launcher, "terminate_pid", killed.append)
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: True)
    monkeypatch.setattr(launcher, "_process_is_python", lambda pid: True)
    monkeypatch.setattr(launcher, "_image_basename", lambda pid: images.get(pid))
    monkeypatch.setattr(launcher, "_child_pids", lambda pid: list(children.get(pid, [])))
    _alive(monkeypatch, [True, False])
    assert launcher.main(["--stop"]) == 0
    assert killed == [5001, 4242]
    assert 5002 not in killed
    assert not launcher.pid_file().exists()
    assert "stopped" in capsys.readouterr().out


def test_stop_does_not_kill_when_the_image_cannot_be_read(monkeypatch):
    launcher.pid_file().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(launcher, "terminate_pid", lambda pid: pytest.fail("unverified pid must not be killed"))
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: True)
    monkeypatch.setattr(launcher, "_process_is_python", lambda pid: True)
    monkeypatch.setattr(launcher, "_image_basename", lambda pid: None)
    monkeypatch.setattr(launcher, "_child_pids", lambda pid: [])
    _alive(monkeypatch, [False])
    assert launcher.main(["--stop"]) == 0
    assert launcher.pid_file().read_text(encoding="utf-8").strip() == "4242"


def test_stop_removes_a_stale_pid_without_killing(monkeypatch, capsys):
    launcher.pid_file().write_text("4242", encoding="utf-8")
    monkeypatch.setattr(launcher, "terminate_pid", lambda pid: pytest.fail("stale pid must not be killed"))
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: False)
    _alive(monkeypatch, [False])
    assert launcher.main(["--stop"]) == 0 and not launcher.pid_file().exists()
    assert "not running" in capsys.readouterr().out


def test_stop_reports_a_server_it_did_not_start(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "terminate_pid", lambda pid: pytest.fail("nothing to terminate"))
    monkeypatch.setattr(launcher.time, "monotonic", iter([0, 100, 200]).__next__)
    _alive(monkeypatch, [True])
    assert launcher.main(["--stop"]) == 1 and "run-debug" in capsys.readouterr().out


def test_status(monkeypatch, capsys):
    _alive(monkeypatch, [True])
    assert launcher.main(["--status"]) == 0 and "running at http://localhost:8517" in capsys.readouterr().out
    _alive(monkeypatch, [False])
    assert launcher.main(["--status"]) == 1


# ------------------------------------------------------------------------------------------ update
def test_update_command_stops_the_supervisor_too(monkeypatch, tmp_path):
    monkeypatch.delenv("SKETCH2CAD_SUPERVISOR_PID", raising=False)
    assert updater.update_command(77, tmp_path)[-2:] == [str(tmp_path), "77"]
    monkeypatch.setenv("SKETCH2CAD_SUPERVISOR_PID", "55")
    assert updater.update_command(77, tmp_path)[-3:] == [str(tmp_path), "77", "55"]
    monkeypatch.setenv("SKETCH2CAD_SUPERVISOR_PID", "junk")
    assert updater.update_command(77, tmp_path)[-1] == "77"


# ------------------------------------------------------------------------------------ pid and repair
def test_pid_alive_for_this_process_and_a_missing_one():
    assert launcher.pid_alive(os.getpid()) is True
    assert launcher._process_is_python(os.getpid()) is True
    missing = 2_147_483_647
    if missing == os.getpid():
        missing -= 1
    assert launcher.pid_alive(missing) is False
    assert launcher.pid_alive(0) is False


def test_serve_drops_a_stale_pid_and_starts(monkeypatch):
    launcher.pid_file().write_text("999999", encoding="utf-8")
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: False)
    proc = FakeProc(interrupt_at=2, pid=7)
    started = _servers(monkeypatch, [proc])
    _alive(monkeypatch, [False, True])
    assert launcher.serve() == 0 and started == [proc]
    assert "removed stale pid 999999" in launcher.log_file().read_text(encoding="utf-8")
    assert not launcher.pid_file().exists()


def test_serve_leaves_a_live_supervisor_alone(monkeypatch):
    launcher.pid_file().write_text("55", encoding="utf-8")
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: pid == 55)
    monkeypatch.setattr(launcher, "_process_is_python", lambda pid: True)
    started = _servers(monkeypatch, [])
    _alive(monkeypatch, [False])
    assert launcher.serve() == 0 and started == []
    assert launcher.pid_file().read_text(encoding="utf-8").strip() == "55"
    assert "already running" in launcher.log_file().read_text(encoding="utf-8")


def test_serve_repairs_the_shortcut_before_anything_else(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", lambda: calls.append("shortcut") or "unchanged")
    _alive(monkeypatch, [True])
    assert launcher.serve() == 0 and calls == ["shortcut"]


def test_open_repairs_the_shortcut(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", lambda: calls.append("shortcut") or "unchanged")
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: calls.append("serve"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: calls.append(u))
    _alive(monkeypatch, [True])
    assert launcher.open_app() == 0 and calls[0] == "shortcut"


def _clock(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(launcher.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(launcher, "_sleep", lambda seconds: clock.__setitem__("t", clock["t"] + seconds))
    return clock


def test_watchdog_restarts_a_server_that_stops_answering(monkeypatch):
    _clock(monkeypatch)
    monkeypatch.setattr(launcher, "HEALTH_INTERVAL", 10)
    monkeypatch.setattr(launcher, "HEALTH_MISS_LIMIT", 2)
    hung = FakeProc(pid=41)
    healthy = FakeProc(interrupt_at=3, pid=42)
    started = _servers(monkeypatch, [hung, healthy])

    def alive(port=launcher.PORT, timeout=1.0):
        if not started:
            return False
        current = started[-1]
        if current is hung and not hung.terminated:
            hung.checks = getattr(hung, "checks", 0) + 1
            return hung.checks == 1                          # answers once, then goes silent
        return current is healthy

    monkeypatch.setattr(launcher, "server_alive", alive)
    assert launcher.serve() == 0
    assert hung.terminated and started == [hung, healthy]
    text = launcher.log_file().read_text(encoding="utf-8")
    assert "server stopped answering" in text and "server 42 answers" in text


def test_watchdog_gives_up_after_the_retry_limit(monkeypatch):
    _clock(monkeypatch)
    monkeypatch.setattr(launcher, "HEALTH_INTERVAL", 10)
    monkeypatch.setattr(launcher, "HEALTH_MISS_LIMIT", 2)
    monkeypatch.setattr(launcher, "MAX_WATCHDOG_RESTARTS", 1)
    hung = FakeProc(pid=31)
    started = _servers(monkeypatch, [hung])
    calls = {"n": 0}

    def alive(port=launcher.PORT, timeout=1.0):
        calls["n"] += 1
        return calls["n"] == 2                               # the first wait_alive, then silence

    monkeypatch.setattr(launcher, "server_alive", alive)
    errors = []
    monkeypatch.setattr(launcher, "report_error", errors.append)
    assert launcher.serve() == 1 and started == [hung] and hung.terminated
    assert errors and "stopped answering" in errors[0]
    assert "gave up" in launcher.log_file().read_text(encoding="utf-8")


def test_repair_clears_a_bad_pid_and_starts_the_server(monkeypatch, capsys):
    launcher.pid_file().write_text("not-a-pid", encoding="utf-8")
    calls = []
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", lambda: calls.append("shortcut") or "repaired")
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: calls.append("serve"))
    _alive(monkeypatch, [False, True])
    assert launcher.main(["--repair"]) == 0 and calls == ["shortcut", "serve"]
    assert not launcher.pid_file().exists()
    assert "repaired" in capsys.readouterr().out


def test_repair_when_the_server_is_up_does_not_spawn(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: calls.append("serve"))
    _alive(monkeypatch, [True])
    assert launcher.repair() == 0 and calls == []


def test_repair_leaves_a_live_supervisor(monkeypatch, capsys):
    launcher.pid_file().write_text("55", encoding="utf-8")
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: True)
    monkeypatch.setattr(launcher, "_process_is_python", lambda pid: True)
    monkeypatch.setattr(launcher, "spawn_serve", lambda port=launcher.PORT: pytest.fail("second supervisor"))
    _alive(monkeypatch, [False])
    assert launcher.repair() == 0
    assert "55" in capsys.readouterr().out


def test_startup_shortcut_is_skipped_off_windows(monkeypatch):
    monkeypatch.setattr(launcher, "_is_windows", lambda: False)
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", _REAL_ENSURE)
    monkeypatch.setattr(launcher, "run", lambda *args, **kwargs: pytest.fail("no powershell off windows"))
    assert launcher.ensure_startup_shortcut() == "skipped"


def test_startup_shortcut_repairs_idempotently(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "_is_windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(launcher, "ensure_startup_shortcut", _REAL_ENSURE)
    answers = iter([b"unchanged\n", b"repaired\n"])

    def fake_run(cmd, **kwargs):
        class _Proc:
            returncode = 0
            stdout = next(answers)
            stderr = b""
        fake_run.cmd = cmd
        fake_run.env = kwargs["env"]
        return _Proc()

    monkeypatch.setattr(launcher, "run", fake_run)
    assert launcher.ensure_startup_shortcut() == "unchanged"
    assert launcher.ensure_startup_shortcut() == "repaired"
    assert fake_run.cmd[:5] == ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden"]
    env = fake_run.env
    assert env["S2C_ARGS"] == "-m sketch2cad.launcher --serve"
    assert env["S2C_WD"] == str(launcher.APP_DIR)
    assert env["S2C_LNK"].endswith("Sketch2CAD server.lnk")
    assert "Startup" in env["S2C_LNK"]
    assert "WScript.Shell" in fake_run.cmd[-1]
    assert "shortcut: Startup shortcut repaired" in launcher.log_file().read_text(encoding="utf-8")


def test_update_is_spawned_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(updater, "_is_windows", lambda: True)
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(updater, "popen", fake_popen)
    updater.start_update_and_restart(77, tmp_path)
    assert seen["cmd"][0] == "cmd"
    flags = seen["kwargs"]["extra_flags"]
    assert flags & updater.DETACHED_PROCESS
    assert flags & updater.CREATE_BREAKAWAY_FROM_JOB
    assert seen["kwargs"]["stdin"] is not None
