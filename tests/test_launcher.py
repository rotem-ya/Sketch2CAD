"""The background launcher: serve (keep the server running, restart it when it falls, give up after repeated
failures), open (start if needed, then the browser), stop (supervisor first), and the update command."""
import pytest

from sketch2cad import launcher, updater


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launcher, "_sleep", lambda s: None)
    monkeypatch.setattr(launcher, "START_TIMEOUT", 0)        # wait_alive never waits on the clock


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
    _alive(monkeypatch, [True, False])
    assert launcher.main(["--stop"]) == 0 and killed == [4242] and not launcher.pid_file().exists()
    assert "stopped" in capsys.readouterr().out


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
