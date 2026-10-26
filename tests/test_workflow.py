"""Agent panel state, from the exchange folders and the drawing's event file."""
import json
import re

from sketch2cad.bridge import export_to_outbox
from sketch2cad.documents import create_document
from sketch2cad.i18n import STRINGS
from sketch2cad.projects import create_project
from sketch2cad.workflow import compose_request, record_applied, record_restore, workflow_view

PIPE = {
    "version": 1, "units": "mm", "scale": 20, "paper": "A3", "language": "both",
    "elements": [{"id": "p1", "type": "pipe", "points": [[0, 0], [1000, 0]], "od": 110}],
}
# The status card names the local runner. It still must not talk about a model service or a key.
BANNED = re.compile(r"\b(AI|API|anthropic)\b|מפתח")


def _drawing(tmp_path):
    project = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    doc = create_document(project, title_he="גמל", title_en="Camel", discipline="W", subject="CAMEL")
    (doc.path / "spec.json").write_text(json.dumps(PIPE), encoding="utf-8")
    return project, doc


def _view(project, doc):
    return workflow_view(project.path, doc.path, doc.code)


def test_compose_appends_checked_items():
    assert compose_request("  להזיז את השוחה  ", []) == "להזיז את השוחה"
    text = compose_request("להזיז את השוחה", ["שמור על קנה המידה", "הוסף חתך"])
    assert text.startswith("להזיז את השוחה\n\n- שמור על קנה המידה\n- הוסף חתך")
    assert compose_request("", ["הוסף הגדלה"]) == "- הוסף הגדלה"


def test_panel_states_follow_the_exchange_folders(tmp_path):
    project, doc = _drawing(tmp_path)
    assert _view(project, doc)["state"] == "none"

    export_to_outbox(doc.path, request="להוסיף חתך")
    sent = _view(project, doc)
    assert sent["state"] == "awaiting"
    assert sent["reply_waiting"] is False
    assert "להוסיף חתך" in sent["last_sent"]["request"]
    assert sent["last_applied"] is None

    inbox = project.path / "agent_exchange" / "inbox"
    reply = inbox / "reply.json"
    reply.write_text("{}\n", encoding="utf-8")
    waiting = _view(project, doc)
    assert waiting["state"] == "reply"
    assert waiting["reply_waiting"] is True
    assert waiting["reply_name"] == "reply.json"

    record_applied(doc.path, reply=reply, backup="spec_history/spec_old.json")
    applied = _view(project, doc)
    assert applied["state"] == "applied"
    assert applied["reply_waiting"] is False
    assert applied["last_applied"]["backup"].endswith("spec_old.json")

    record_restore(doc.path, "spec_old.json")
    assert _view(project, doc)["state"] == "reply"
    assert _view(project, doc)["last_applied"] is None


def test_ui_strings_use_plain_words():
    assert BANNED.search("תמונה נשמרת כסקיצה") is None
    for key, entry in STRINGS.items():
        assert {"en", "he"} <= set(entry), key
        for lang, text in entry.items():
            assert text.strip(), key
            assert BANNED.search(text) is None, f"{key}:{lang}:{text}"
            assert "full AutoCAD" not in text and "AutoCAD מלא" not in text, f"{key}:{lang}"


def test_notice_localises_converter_and_bridge_messages():
    from sketch2cad.i18n import notice, t
    raw = "DWG skipped: No DWG converter found. Install ODA File Converter or install full AutoCAD."
    assert notice(raw, "he") == "לא נמצא ממיר DWG — הגדר את ODA File Converter בהגדרות."
    assert notice(raw, "en") == t("no_converter", "en")
    assert "DWG skipped" not in notice(raw, "he")
    assert notice("ODA File Converter not found", "he") == t("no_converter", "he")
    assert notice("DWG was not written", "he") == t("agent_dwg_none", "he")
    assert notice("no reply in /tmp/inbox", "he") == t("agent_no_reply", "he")
    assert notice("refusing to export: spec.json contains a secret marker", "he") == t("agent_secret", "he")
    assert notice("render failed: bad spec", "en") == t("agent_render_failed", "en")
    assert notice("Conversion of a.dwg timed out after 180 s", "he") == t("agent_dwg_timeout", "he")
    assert notice("ODA conversion of a.dxf to DWG failed", "he") == t("agent_dwg_failed", "he")
