"""The desktop launcher: find a browser, start and watch the server, stop it when the window closes,
report a server that did not start, and the idle timer that stops a server nobody looks at."""
import pytest

from sketch2cad import launcher


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(launcher, "_sleep", lambda s: None)


class FakeProc:
    """poll() returns the exit code after `exits_after` polls (or after terminate())."""
    def __init__(self, exits_after=None, code=0):
        self.polls, self.exits_after, self.code = 0, exits_after, code
        self.terminated = self.killed = False

    def poll(self):
        self.polls += 1
        if self.terminated or (self.exits_after is not None and self.polls > self.exits_after):
            return self.code
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return self.code


def test_find_browser_override_known_paths_then_none(tmp_path, monkeypatch):
    exe = tmp_path / "msedge.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("SKETCH2CAD_BROWSER", str(exe))
    assert launcher.find_browser() == str(exe)
    monkeypatch.delenv("SKETCH2CAD_BROWSER")
    monkeypatch.setattr(launcher, "BROWSER_PATHS", (str(tmp_path / "missing.exe"), str(exe)))
    monkeypatch.setattr(launcher, "BROWSER_NAMES", ())
    assert launcher.find_browser() == str(exe)
    monkeypatch.setattr(launcher, "BROWSER_PATHS", ("%NoSuchVariable%\\x.exe",))
    assert launcher.find_browser() is None


def _wire(monkeypatch, server, window, alive):
    seq = iter(alive)
    calls = {}
    monkeypatch.setattr(launcher, "server_alive", lambda port=launcher.PORT, timeout=1.0: next(seq, True))
    monkeypatch.setattr(launcher, "start_server",
                        lambda port=launcher.PORT, log=None: calls.setdefault("started", server))
    monkeypatch.setattr(launcher, "open_window",
                        lambda target, port=launcher.PORT: calls.setdefault("url", target) and window)
    return calls


def test_window_closed_stops_the_server(monkeypatch):
    server, window = FakeProc(), FakeProc(exits_after=2)
    calls = _wire(monkeypatch, server, window, [False, False, True])
    assert launcher.main() == 0
    assert calls["url"] == "http://localhost:8517" and calls["started"] is server
    assert server.terminated and not window.terminated


def test_server_stopped_closes_the_window(monkeypatch):
    server, window = FakeProc(exits_after=3), FakeProc()
    _wire(monkeypatch, server, window, [False, True])
    assert launcher.main() == 0
    assert window.terminated and not server.terminated


def test_default_browser_waits_for_the_server(monkeypatch):
    server = FakeProc(exits_after=2)
    _wire(monkeypatch, server, None, [False, True])          # no window process to watch
    assert launcher.main() == 0 and not server.terminated


def test_running_server_gets_another_window_only(monkeypatch):
    window = FakeProc(exits_after=0)
    calls = _wire(monkeypatch, FakeProc(), window, [True])
    assert launcher.main() == 0
    assert "started" not in calls and calls["url"] == "http://localhost:8517"


def test_server_that_did_not_start_is_reported(monkeypatch):
    server = FakeProc(exits_after=0, code=1)
    _wire(monkeypatch, server, None, [False, False, False])
    errors = []
    monkeypatch.setattr(launcher, "report_error", errors.append)
    log = launcher.log_file()
    log.write_text("Port 8517 is already in use", encoding="utf-8")
    assert launcher.main() == 1
    assert "already in use" in errors[0] and str(log) in errors[0]


def test_idle_timer():
    t = launcher.IdleTimer(grace=3, startup=10)
    assert t.tick(0) is False                           # the first window is still opening
    assert t.tick(1) is False
    assert [t.tick(0), t.tick(0)] == [False, False]     # closed: the grace period runs
    assert t.tick(1) is False                           # a window came back (F5): reset
    assert [t.tick(0), t.tick(0), t.tick(0)] == [False, False, True]
    never = launcher.IdleTimer(grace=3, startup=10)
    assert [never.tick(0) for _ in range(9)] == [False] * 9
    assert never.tick(0) is True                        # no window ever came


def test_watchdog_only_under_the_launcher(monkeypatch):
    monkeypatch.delenv("SKETCH2CAD_LAUNCHER", raising=False)
    assert launcher.start_idle_watchdog() is False
