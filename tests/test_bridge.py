"""Work-package bridge: export, import, and no network. The model API is not on this path."""
import hashlib
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from sketch2cad import bridge
from sketch2cad.procutil import run as hidden_run
from sketch2cad.bridge import BridgeError, export_package, import_reply, main
from sketch2cad.documents import add_source, create_document
from sketch2cad.projects import create_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
ELBOW = {
    "version": 1,
    "units": "mm",
    "scale": 20,
    "paper": "A3",
    "language": "both",
    "elements": [{
        "id": "bend-01", "type": "bend", "item": "elbow_6in_lr90",
        "center": [0, 0], "radius": 229, "start_angle": 0, "end_angle": 90,
    }],
}
PIPE = {
    "version": 1, "units": "mm", "scale": 20, "paper": "A3", "language": "both",
    "elements": [{"id": "p1", "type": "pipe", "points": [[0, 0], [1000, 0]], "od": 110}],
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-FROMENV")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("network connection")
    monkeypatch.setattr(socket, "create_connection", blocked)


def _drawing(tmp_path, spec=None):
    project = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    doc = create_document(project, title_he="גמל", title_en="Camel", discipline="W", subject="CAMEL")
    if spec is not None:
        (doc.path / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    return project, doc


def _png(path, color):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path)
    return path


def _package_bytes(folder: Path) -> bytes:
    return b"".join(path.read_bytes() for path in folder.rglob("*") if path.is_file())


# ---------------------------------------------------------------- export
def test_export_manifest_matches_files_and_has_no_secrets(tmp_path):
    _project, doc = _drawing(tmp_path, ELBOW)
    _png(tmp_path / "sketch.png", "white")
    add_source(doc, tmp_path / "sketch.png")
    add_source(doc, name="leak.pdf", data=b"%PDF-1.4\nsk-ant-LEAKED\n")
    add_source(doc, name="review.eml", data=b"From: reviewer\nHello\n")
    (doc.path / "settings.json").write_text('{"anthropic_api_key": "sk-ant-LOCAL"}', encoding="utf-8")
    (doc.path.parent.parent / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-FROMFILE\n", encoding="utf-8")
    marked = _png(doc.path / "markups" / "markup_20260101.png", "red")
    _png(doc.path / "sheet.png", "blue")
    doc.outputs["preview"] = "sheet.png"
    doc.save()

    out = tmp_path / "package"
    export_package(doc.path, out, request="Keep every dimension that was not named.")
    blob = _package_bytes(out)
    assert b"sk-ant-" not in blob and b"anthropic_api_key" not in blob.lower()
    assert b"LEAKED" not in blob and b"FROMENV" not in blob and b"FROMFILE" not in blob

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    disk = sorted(path.relative_to(out).as_posix() for path in out.rglob("*")
                  if path.is_file() and path.name != "manifest.json")
    assert disk == sorted(item["name"] for item in manifest["files"])
    for item in manifest["files"]:
        data = (out / item["name"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert item["bytes"] == len(data)
    assert (out / "spec.json").read_bytes() == (doc.path / "spec.json").read_bytes()
    assert (out / "preview.png").read_bytes() == marked.read_bytes()
    assert manifest["preview_marked"] is True
    assert manifest["units"] == "mm" and manifest["language"] == "both"
    assert manifest["items_used"] == ["elbow_6in_lr90"]
    assert manifest["return_envelope"]["spec"] == {} and "changes" in manifest["return_envelope"]
    names = {item["name"] for item in manifest["files"]}
    assert "sources/leak.pdf" not in names and "sources/review.eml" not in names
    assert "sources/sketch.png" in names
    reasons = {item["reason"] for item in manifest["excluded"]}
    assert reasons >= {"secret marker", "not a sketch, image or PDF"}
    catalog = (out / "catalog.txt").read_text(encoding="utf-8")
    assert "elbow_6in_lr90 | elbow |" in catalog
    assert "trench_drain_generic" not in catalog and "avk_0661_dn150" not in catalog
    spec_format = (out / "spec_format.md").read_text(encoding="utf-8")
    assert spec_format.startswith("## 1.") and "## 2." not in spec_format
    instructions = (out / "AGENT.md").read_text(encoding="utf-8")
    assert "Change only what the request and the markings ask for." in instructions
    assert "Preserve units, scale and paper" in instructions
    assert "פפיס" in instructions and "Papis" in instructions
    assert (out / "request.txt").read_text(encoding="utf-8").startswith("Keep every dimension")
    source = Path(bridge.__file__).read_text(encoding="utf-8")
    assert "import anthropic" not in source and "anthropic." not in source
    for word in ("urllib", "requests", "http.client", "socket"):
        assert word not in source


def test_export_skips_a_source_that_escapes_the_drawing(tmp_path):
    _project, doc = _drawing(tmp_path, ELBOW)
    secret = doc.path.parent.parent / "secret.txt"
    secret.write_text("sk-ant-OUTSIDE", encoding="utf-8")
    doc.sources.append("../../secret.txt")
    doc.save()
    out = tmp_path / "package"
    export_package(doc.path, out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert b"OUTSIDE" not in _package_bytes(out)
    assert any(item["reason"] == "outside the drawing folder" for item in manifest["excluded"])


def test_export_copies_extra_pdf_and_dwg_into_sources(tmp_path):
    _project, doc = _drawing(tmp_path, ELBOW)
    pdf = tmp_path / "section.pdf"
    pdf.write_bytes(b"%PDF-1.4\nsection\n")
    dwg = tmp_path / "ref.dwg"
    dwg.write_bytes(b"AC1018 reference")
    skipped = tmp_path / "note.txt"
    skipped.write_text("not a drawing", encoding="utf-8")
    leaked = tmp_path / "leak.pdf"
    leaked.write_bytes(b"%PDF-1.4\nsk-ant-LEAKED\n")
    out = tmp_path / "pkg"
    export_package(doc.path, out, extra_sources=[pdf, dwg, skipped, leaked])
    assert (out / "sources" / "section.pdf").read_bytes().startswith(b"%PDF")
    assert (out / "sources" / "ref.dwg").read_bytes().startswith(b"AC1018")
    blob = _package_bytes(out)
    assert b"LEAKED" not in blob and b"not a drawing" not in blob
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    roles = {item["name"]: item["role"] for item in manifest["files"]}
    assert roles["sources/section.pdf"] == "source"
    assert roles["sources/ref.dwg"] == "source"
    reasons = {item["reason"] for item in manifest["excluded"]}
    assert "not a PDF, DWG or image" in reasons
    assert "secret marker" in reasons


def test_export_refuses_a_secret_in_the_request(tmp_path):
    _project, doc = _drawing(tmp_path, ELBOW)
    out = tmp_path / "secret-package"
    with pytest.raises(BridgeError, match="secret"):
        export_package(doc.path, out, request="the key is sk-ant-NOPE")
    assert not out.exists()


def test_several_drawings_need_a_code(tmp_path):
    project = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    first = create_document(project, title_en="One", discipline="W", subject="ONE")
    create_document(project, title_en="Two", discipline="W", subject="TWO")
    with pytest.raises(BridgeError, match="--drawing"):
        export_package(project.path, tmp_path / "out")
    export_package(project.path, tmp_path / "out", drawing=first.code)
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["document_code"] == first.code
    catalog = (tmp_path / "out" / "catalog.txt").read_text(encoding="utf-8")
    assert "elbow_6in_lr90" in catalog and "trench_drain_generic" in catalog


def test_module_cli_export(tmp_path):
    _project, doc = _drawing(tmp_path, PIPE)
    out = tmp_path / "cli-package"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["SKETCH2CAD_HOME"] = str(tmp_path / "home")
    proc = hidden_run(
        [sys.executable, "-m", "sketch2cad.bridge", "export", str(doc.path), "--out", str(out),
         "--request", "leave the pipe where it is"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert (out / "manifest.json").is_file() and (out / "request.txt").is_file()
    assert "sk-ant-" not in _package_bytes(out).decode("utf-8", "ignore")


# ---------------------------------------------------------------- import
def test_import_example_renders_without_touching_the_spec(tmp_path):
    _project, doc = _drawing(tmp_path, PIPE)
    spec_path = doc.path / "spec.json"
    original = spec_path.read_bytes()
    document_json = (doc.path / "document.json").read_bytes()
    example = json.loads((EXAMPLES / "camel_simple.json").read_text(encoding="utf-8"))
    reply = tmp_path / "reply.json"
    envelope = {"spec": example, "assumptions": [{"en": "Site cover stays 1.20 m", "he": "כיסוי 1.20 מ' נשאר"}],
                "questions": [], "changes": [{"en": "Loaded the camel example", "he": "נטענה דוגמת הגמל"}]}
    reply.write_text("```json\n" + json.dumps(envelope, ensure_ascii=False) + "\n```\n", encoding="utf-8")
    trial = tmp_path / "trial"
    assert main(["import", str(reply), "--drawing", str(doc.path), "--trial", str(trial)]) == 0
    report = json.loads((trial / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["applied"] is False
    assert spec_path.read_bytes() == original
    assert (doc.path / "document.json").read_bytes() == document_json
    assert not list(doc.path.glob("*.dxf")) and not (doc.path / "spec_history").exists()
    for fmt in ("dxf", "pdf", "png"):
        assert (trial / report["outputs"][fmt]).stat().st_size > 1000
    assert not any("Dropped" in warning and "circle" in warning for warning in report["warnings"])
    assert report["element_count"] == len(example["elements"])
    assert report["changes"][0]["he"] == "נטענה דוגמת הגמל"
    assert "פפיס" in report["precision_source"]["he"]
    assert "Change only what" in report["precision_rules"][0]
    assert isinstance(report["checks"], list)
    assert all({"severity", "code", "en", "he"} <= finding.keys() for finding in report["checks"])


def test_apply_backs_up_then_replaces_spec(tmp_path):
    _project, doc = _drawing(tmp_path, PIPE)
    spec_path = doc.path / "spec.json"
    original = spec_path.read_bytes()
    revised = json.loads(json.dumps(PIPE))
    revised["notes"] = {"en": ["Added note"], "he": ["הערה"]}
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({
        "spec": revised, "changes": [{"en": "Added a note", "he": "נוספה הערה"}],
        "assumptions": [], "questions": [],
    }), encoding="utf-8")
    untouched = import_reply(reply, doc.path, trial=tmp_path / "trial-no", apply=False)
    assert untouched["ok"] and not untouched["applied"] and spec_path.read_bytes() == original
    applied = import_reply(reply, doc.path, trial=tmp_path / "trial-yes", apply=True)
    assert applied["ok"] and applied["applied"]
    saved = json.loads(spec_path.read_text(encoding="utf-8"))
    assert saved["notes"]["he"] == ["הערה"] and saved["elements"][0]["id"] == "p1"
    backups = list((doc.path / "spec_history").glob("spec_*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert applied["backup"] == f"spec_history/{backups[0].name}"
    assert not list(doc.path.glob("*.dxf"))


def test_bad_reply_does_not_touch_the_spec(tmp_path):
    _project, doc = _drawing(tmp_path, PIPE)
    spec_path = doc.path / "spec.json"
    original = spec_path.read_bytes()
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    illegal = tmp_path / "illegal.json"
    illegal.write_text(json.dumps({"spec": {"elements": [{"type": "nope"}]}}), encoding="utf-8")
    not_object = tmp_path / "text.json"
    not_object.write_text(json.dumps({"spec": "nope"}), encoding="utf-8")
    for path in (broken, illegal, not_object):
        report = import_reply(path, doc.path, trial=tmp_path / path.stem, apply=True)
        assert report["ok"] is False and report["applied"] is False and report["errors"]
        assert spec_path.read_bytes() == original
        assert not (doc.path / "spec_history").exists()
        assert not list(doc.path.glob("*.dxf"))
    assert main(["import", str(broken), "--drawing", str(doc.path), "--trial", str(tmp_path / "cli"),
                 "--apply"]) == 1
    assert spec_path.read_bytes() == original


def test_secret_reply_is_refused_without_copying_it(tmp_path):
    _project, doc = _drawing(tmp_path, PIPE)
    original = (doc.path / "spec.json").read_bytes()
    reply = tmp_path / "reply.json"
    reply.write_text('{"spec": {"elements": []}, "note": "sk-ant-SECRET"}', encoding="utf-8")
    report = import_reply(reply, doc.path, trial=tmp_path / "trial", apply=True)
    assert report["ok"] is False and report["applied"] is False
    assert "secret" in report["errors"][0]
    assert b"sk-ant-SECRET" not in (tmp_path / "trial" / "report.json").read_bytes()
    assert (doc.path / "spec.json").read_bytes() == original


def test_outbox_and_inbox_do_not_apply_without_a_flag(tmp_path):
    project, doc = _drawing(tmp_path, PIPE)
    spec_path = doc.path / "spec.json"
    original = spec_path.read_bytes()
    package = bridge.export_to_outbox(doc.path, request="keep the pipe")
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["exchange"] == {"outbox": "agent_exchange/outbox", "inbox": "agent_exchange/inbox"}
    assert package.parent == project.path / "agent_exchange" / "outbox"
    assert (package / "request.txt").read_text(encoding="utf-8").startswith("keep the pipe")
    assert (project.path / "agent_exchange" / "inbox").is_dir()
    with pytest.raises(BridgeError, match="no reply"):
        bridge.latest_inbox_reply(project.path)
    inbox = project.path / "agent_exchange" / "inbox"
    older = inbox / "older.json"
    newer = inbox / "newer.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text(json.dumps({"spec": PIPE, "changes": [{"en": "none", "he": "אין"}],
                                 "assumptions": [], "questions": []}), encoding="utf-8")
    os.utime(older, (1_000, 1_000))
    os.utime(newer, (2_000, 2_000))
    assert bridge.latest_inbox_reply(doc.path) == newer
    report = import_reply(bridge.latest_inbox_reply(project.path), doc.path, trial=tmp_path / "trial", apply=False)
    assert report["ok"] is True and report["applied"] is False
    assert spec_path.read_bytes() == original
    assert not (doc.path / "spec_history").exists()


def test_import_skips_dwg_when_no_converter(tmp_path, monkeypatch):
    from sketch2cad.io.oda import ConverterNotFound

    def missing(*_args, **_kwargs):
        raise ConverterNotFound("No DWG converter found.")

    monkeypatch.setattr("sketch2cad.io.oda.convert", missing)
    _project, doc = _drawing(tmp_path, PIPE)
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({"spec": PIPE, "changes": [], "assumptions": [], "questions": []}), encoding="utf-8")
    original = (doc.path / "spec.json").read_bytes()
    report = import_reply(reply, doc.path, trial=tmp_path / "trial", apply=False)
    assert report["ok"] is True and report["applied"] is False
    assert report["dwg"]["produced"] is False
    assert report["dwg"]["reason"].startswith("DWG skipped")
    assert (tmp_path / "trial" / "trial.dxf").is_file()
    assert not (tmp_path / "trial" / "trial.dwg").exists()
    assert (doc.path / "spec.json").read_bytes() == original


def test_cli_to_outbox_and_from_inbox(tmp_path):
    project, doc = _drawing(tmp_path, PIPE)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["SKETCH2CAD_HOME"] = str(tmp_path / "home")
    proc = hidden_run(
        [sys.executable, "-m", "sketch2cad.bridge", "export", str(doc.path), "--to-outbox", "--request", "note"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    package = Path(proc.stdout.strip())
    assert (package / "manifest.json").is_file() and (package / "request.txt").is_file()
    inbox = project.path / "agent_exchange" / "inbox"
    (inbox / "reply.json").write_text(json.dumps({"spec": PIPE}), encoding="utf-8")
    original = (doc.path / "spec.json").read_bytes()
    proc = hidden_run(
        [sys.executable, "-m", "sketch2cad.bridge", "import", "--from-inbox", "--drawing", str(doc.path),
         "--trial", str(tmp_path / "trial")],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "applied" not in proc.stdout
    assert (doc.path / "spec.json").read_bytes() == original
    assert not (doc.path / "spec_history").exists()


def test_bridge_calls_do_not_import_anthropic(tmp_path):
    sys.modules.pop("anthropic", None)
    _project, doc = _drawing(tmp_path, PIPE)
    export_package(doc.path, tmp_path / "package")
    reply = tmp_path / "broken.json"
    reply.write_text("{", encoding="utf-8")
    import_reply(reply, doc.path, trial=tmp_path / "trial", apply=True)
    assert "anthropic" not in sys.modules
