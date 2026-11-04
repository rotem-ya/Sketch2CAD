"""In-app folder browser: server-side listing (no native dialog) and the Browse flow in the UI."""
import os
import subprocess
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sketch2cad import folderbrowse as fb

APP = str(Path(__file__).resolve().parents[1] / "app" / "app.py")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


def test_subfolders_sorted_skip_files_and_hidden(tmp_path):
    for name in ("beta", "Alpha", ".git", "gamma"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("x")
    names, more, err = fb.subfolders(str(tmp_path))
    assert names == ["Alpha", "beta", "gamma"] and not more and err == ""


def test_subfolders_limit_and_unreadable(tmp_path):
    for i in range(5):
        (tmp_path / f"d{i}").mkdir()
    names, more, _ = fb.subfolders(str(tmp_path), limit=3)
    assert names == ["d0", "d1", "d2"] and more
    names, more, err = fb.subfolders(str(tmp_path / "missing"))
    assert names == [] and not more and err


def test_start_dir_falls_back_to_the_nearest_existing_parent(tmp_path):
    assert fb.start_dir(str(tmp_path)) == str(tmp_path)
    assert fb.start_dir(str(tmp_path / "no" / "such")) == str(tmp_path)
    assert fb.start_dir("") == ""


def test_parent_stops_at_the_root(tmp_path):
    assert fb.parent(str(tmp_path / "a")) == str(tmp_path)
    root = os.path.abspath(os.sep)
    assert fb.parent(root) == ""
    assert fb.parent("") == ""


def test_drives_lists_existing_roots():
    roots = fb.drives()
    assert roots and all(os.path.isdir(r) for r in roots)


def test_native_picker_time_limit_never_blocks(monkeypatch):
    from sketch2cad import native

    monkeypatch.setattr(native.os, "name", "nt")
    seen = {}

    def hung(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(native, "hidden_run", hung)
    assert native.pick_folder("C:\\", "t") is None
    assert seen["timeout"] == native.PICK_TIMEOUT_S


def test_browse_button_picks_a_folder_without_a_native_dialog(tmp_path, monkeypatch):
    from sketch2cad import native
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t

    monkeypatch.setattr(native, "pick_folder", lambda *a, **k: pytest.fail("no native dialog"))
    (tmp_path / "work" / "Projects").mkdir(parents=True)
    (tmp_path / "work" / "Other").mkdir()
    Settings(ui_language="en", default_projects_root="").save()
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(w for w in at.text_input if w.key == "np_parent").input(str(tmp_path / "work" / "gone")).run()
    next(b for b in at.button if b.key == "np_parent_browse").click().run()
    assert not at.exception
    labels = [b.label for b in at.button if (b.key or "").startswith("np_parent_fb_")]
    assert "📁 Other" in labels and "📁 Projects" in labels      # opened at the nearest existing parent
    next(b for b in at.button if b.label == "📁 Projects").click().run()
    next(b for b in at.button if b.key == "np_parent_fb_up").click().run()
    next(b for b in at.button if b.label == "📁 Projects").click().run()
    next(b for b in at.button if b.key == "np_parent_fb_ok").click().run()
    assert not at.exception
    assert next(w for w in at.text_input if w.key == "np_parent").value == str(tmp_path / "work" / "Projects")
    assert not any((b.key or "").startswith("np_parent_fb_") for b in at.button)    # browser closed
    next(b for b in at.button if b.key == "np_parent_browse").click().run()
    next(b for b in at.button if b.key == "np_parent_fb_drives").click().run()
    assert any(b.label == f"📁 {fb.drives()[0]}" for b in at.button)
    next(b for b in at.button if b.label == f"✖️ {t('cancel', 'en')}").click().run()
    assert not at.exception and not any((b.key or "").startswith("np_parent_fb_") for b in at.button)
