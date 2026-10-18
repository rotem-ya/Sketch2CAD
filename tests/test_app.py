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

    # drawing tab: typical detail -> generate spec -> render DXF/PDF/PNG (DWG is skipped without a converter)
    assert _by_label(at.radio, t("spec_source", lang)).value == "typical"
    next(b for b in at.button if b.label == t("generate", lang)).click().run()
    assert not at.exception
    assert any(s.value == t("spec_saved", lang) for s in at.success)
    next(b for b in at.button if b.label == t("render", lang)).click().run()
    assert not at.exception
    assert any(s.value == t("rendered", lang) for s in at.success)
    doc_dir = tmp_path / "projects" / "20117_Hatzerim" / "10_Drawings" / "20117-W-CAMEL-01"
    if not doc_dir.exists():
        doc_dir = next(f.parent for f in (tmp_path / "projects").rglob("document.json"))
    assert (doc_dir / "spec.json").exists()
    for ext in ("dxf", "pdf", "png"):
        assert (doc_dir / f"20117-W-CAMEL-01_RevA.{ext}").exists(), ext
    assert (doc_dir / "20117-W-CAMEL-01_BOM.xlsx").exists()     # BOM tab rendered its table and Excel file
    assert not at.error

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
    """Missing code / parent folder give clear Hebrew errors; the folder picker renders, without an API fill."""
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    Settings(ui_language="he", default_projects_root="").save()
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert any(b.key == "np_parent_browse" for b in at.button)
    assert not any(b.key == "np_fill" for b in at.button)
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


def test_update_box(monkeypatch):
    from sketch2cad import updater
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    Settings(ui_language="he").save()
    monkeypatch.setattr(updater, "latest", lambda timeout=5: updater.Release("f" * 40, "2026-10-08", "New"))
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label.endswith(t("check_updates", "he"))).click().run()
    assert not at.exception
    assert any(t("update_available", "he") in w.value for w in at.warning)


def test_settings_change_data_folder(tmp_path, monkeypatch):
    """Settings page: choosing a data folder writes data_home.txt next to the app and copies the data there."""
    from sketch2cad import config
    from sketch2cad.i18n import t
    monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
    (tmp_path / "app").mkdir()
    config.Settings(ui_language="en", default_projects_root="D:/Projects").save()

    at = AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label == t("nav_settings", "en")).click().run()
    assert _by_label(at.text_input, t("data_home", "en")).value == str(tmp_path / "home")
    new = tmp_path / "E" / "Data"
    _by_label(at.text_input, t("data_home", "en")).input(str(new))
    next(b for b in at.button if b.label == t("save", "en")).click().run()
    assert not at.exception
    assert config.data_home_file().read_text(encoding="utf-8").strip() == str(new)
    assert (new / "settings.json").exists()
    assert any(t("data_home_changed", "en") in s.value for s in at.success)


def test_settings_rejects_folder_in_oda_field(tmp_path):
    """A folder typed into the ODA converter field is refused with a clear message, and nothing is saved."""
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    Settings(ui_language="he").save()
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label == t("nav_settings", "he")).click().run()
    _by_label(at.text_input, t("oda_path", "he")).input(str(tmp_path))
    _by_label(at.text_input, t("default_root", "he")).input(str(tmp_path / "Projects"))
    next(b for b in at.button if b.label == t("save", "he")).click().run()
    assert not at.exception
    assert any(t("oda_not_exe", "he") in e.value for e in at.error)
    assert Settings.load().oda_converter_path == "" and Settings.load().default_projects_root == ""
    assert all("Claude" not in (w.label or "") for w in at.text_input)


def test_agent_revision_and_undo(tmp_path):
    """Render a typical detail, send it to the agent, apply a reply, then undo. No API key."""
    import json
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    lang = "he"
    Settings(ui_language=lang, default_projects_root=str(tmp_path / "projects")).save()
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not any(b.label == t("fill_go", lang) for b in at.button)
    _by_label(at.text_input, t("project_code", lang)).input("20117")
    _by_label(at.text_input, t("name_en", lang)).input("Hatzerim")
    next(b for b in at.button if b.label == t("create", lang)).click().run()
    _by_label(at.text_input, t("subject", lang)).input("CAMEL").run()
    _by_label(at.text_input, t("title_en", lang)).input("Camel")
    next(b for b in at.button if b.label == t("create", lang)).click().run()
    next(b for b in at.button if b.label == t("generate", lang)).click().run()
    next(b for b in at.button if b.label == t("render", lang)).click().run()
    assert not at.exception
    doc_dir = next(f.parent for f in (tmp_path / "projects").rglob("document.json"))
    original = json.loads((doc_dir / "spec.json").read_text(encoding="utf-8"))
    assert not any(b.label.endswith(t("revise_go", lang)) for b in at.button)

    _by_label(at.text_area, t("revise_request", lang)).input("לשנות את שם פריט 1")
    next(b for b in at.button if b.label == t("agent_send", lang)).click().run()
    assert not at.exception, at.exception
    project_dir = next(f.parent for f in (tmp_path / "projects").rglob("project.json"))
    packages = list((project_dir / "agent_exchange" / "outbox").iterdir())
    assert len(packages) == 1
    assert (packages[0] / "request.txt").read_text(encoding="utf-8").startswith("לשנות את שם פריט 1")
    assert json.loads((doc_dir / "spec.json").read_text(encoding="utf-8")) == original

    revised = json.loads(json.dumps(original))
    revised["legend"][0]["en"] = "REVISED ITEM"
    inbox = project_dir / "agent_exchange" / "inbox"
    (inbox / "reply.json").write_text(json.dumps({
        "spec": revised,
        "changes": [{"en": "Legend 1 renamed", "he": "שם פריט 1 שונה"}],
        "assumptions": [], "questions": [],
    }, ensure_ascii=False), encoding="utf-8")
    next(b for b in at.button if b.label == t("agent_recv", lang)).click().run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == t("agent_apply", lang)).click().run()
    assert not at.exception, at.exception
    saved = json.loads((doc_dir / "spec.json").read_text(encoding="utf-8"))
    assert saved["legend"][0]["en"] == "REVISED ITEM"
    assert len(list((doc_dir / "spec_history").glob("spec_*.json"))) == 1
    assert any(s.value == t("agent_applied", lang) for s in at.success)
    assert (doc_dir / "20117-W-CAMEL-01_RevA.png").exists()

    next(b for b in at.button if b.label.endswith(t("revise_undo", lang))).click().run()
    assert not at.exception, at.exception
    assert json.loads((doc_dir / "spec.json").read_text(encoding="utf-8")) == original
    assert not list((doc_dir / "spec_history").glob("spec_*.json"))
    assert any(s.value == t("revise_undone", lang) for s in at.success)


def test_mail_page_opens(tmp_path):
    from sketch2cad.config import Settings
    from sketch2cad.i18n import t
    Settings(ui_language="he", default_projects_root=str(tmp_path / "projects")).save()
    at = AppTest.from_file(APP, default_timeout=30).run()
    next(b for b in at.button if b.label.endswith(t("nav_mail", "he"))).click().run()
    assert not at.exception
    assert any(h.value.endswith(t("nav_mail", "he")) for h in at.header)
