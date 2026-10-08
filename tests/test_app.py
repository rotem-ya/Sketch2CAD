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

    # tool pages render
    for nav in ("nav_import", "nav_templates", "nav_catalog", "nav_settings"):
        next(b for b in at.button if b.label == t(nav, lang)).click().run()
        assert not at.exception, nav


def test_typical_render_checks_bom_package(tmp_path):
    """Document page end to end: typical detail → spec → render → outputs, checks, BOM, package."""
    from sketch2cad.config import Settings
    from sketch2cad.documents import Document, create_document
    from sketch2cad.i18n import t
    from sketch2cad.projects import create_project
    p = create_project(tmp_path / "projects", "20117", "חצרים", "Hatzerim")
    d = create_document(p, title_he="חיבור לגמל קיים", title_en="Camel", discipline="W", subject="CAMEL")
    s = Settings(ui_language="en", projects=[p.folder])
    s.save()

    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["page"] = "document"
    at.session_state["project_folder"] = p.folder
    at.session_state["doc_folder"] = d.folder
    at.run()
    assert not at.exception
    next(b for b in at.button if b.label == t("generate", "en")).click().run()
    assert not at.exception
    assert (d.path / "spec.json").exists()
    next(b for b in at.button if b.label == t("render", "en")).click().run()
    assert not at.exception
    d = Document.load(d.folder)
    assert {"dxf", "pdf", "preview"} <= set(d.outputs)
    assert (d.path / d.outputs["dxf"]).exists()
    assert any("🟠" in m.value for m in at.markdown)                 # checks tab: flange drilling warning
    assert (d.path / f"{d.code}_BOM.xlsx").exists()
    next(b for b in at.button if b.key == "pkg_build").click().run()
    assert not at.exception
    assert (d.path / Document.load(d.folder).outputs["package"]).exists()


def test_new_project_validation(tmp_path):
    """Missing code / parent folder give clear Hebrew errors; the folder-picker and AI-fill buttons render."""
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    Settings(ui_language="he", default_projects_root="").save()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any(b.key == "np_parent_browse" for b in at.button) and any(b.key == "np_fill" for b in at.button)
    next(b for b in at.button if b.label == t("create", "he")).click().run()
    assert any(t("code_required", "he") in e.value for e in at.error)
    _by_label(at.text_input, t("project_code", "he")).input("20117")
    next(b for b in at.button if b.label == t("create", "he")).click().run()
    assert any(t("parent_required", "he") in e.value for e in at.error)
    _by_label(at.text_input, t("parent_folder", "he")).input(str(tmp_path / "p"))
    _by_label(at.text_input, t("contract_no", "he")).input("W912")
    next(b for b in at.button if b.label == t("create", "he")).click().run()
    assert not at.exception
    from sketch2cad.projects import Project
    assert Project.load(tmp_path / "p" / "20117").contract_no == "W912"
