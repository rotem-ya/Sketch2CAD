"""UI smoke test: create project -> create document -> search, in both languages (Streamlit AppTest)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "app.py")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


def _by_label(widgets, label):
    return next(w for w in widgets if w.label == label)


@pytest.mark.parametrize("lang", ["en", "he"])
def test_project_document_search_flow(tmp_path, lang):
    from sketch2cad.config import Settings
    s = Settings(ui_language=lang, default_projects_root=str(tmp_path / "projects"))
    s.save()
    from sketch2cad.i18n import t

    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception

    # create project
    _by_label(at.text_input, t("project_code", lang)).input("20117")
    _by_label(at.text_input, t("name_he", lang)).input("חצרים")
    _by_label(at.text_input, t("name_en", lang)).input("Hatzerim")
    next(b for b in at.button if b.label == t("create", lang)).click().run()
    assert not at.exception
    assert any("20117 - " in h.value for h in at.header)
    assert (tmp_path / "projects" / "20117_Hatzerim" / "project.json").exists()

    # new document (tab "new document")
    _by_label(at.text_input, t("subject", lang)).input("CAMEL").run()
    assert any("20117-W-CAMEL-01" in m.value for m in at.markdown)
    _by_label(at.text_input, t("title_en", lang)).input("Connection to existing camel")
    _by_label(at.text_input, t("title_he", lang)).input("חיבור לגמל קיים")
    next(b for b in at.button if b.label == t("create", lang)).click().run()
    assert not at.exception
    assert any("20117-W-CAMEL-01" in m.value for m in at.markdown)

    # global search
    next(b for b in at.button if b.label == t("nav_search", lang)).click().run()
    _by_label(at.text_input, t("search_text", lang)).input("camel").run()
    assert not at.exception
    assert any("20117-W-CAMEL-01" in m.value for m in at.markdown)
    _by_label(at.text_input, t("search_text", lang)).input("גמל").run()
    assert any("20117-W-CAMEL-01" in m.value for m in at.markdown)

    # settings page renders
    next(b for b in at.button if b.label == t("nav_settings", lang)).click().run()
    assert not at.exception
