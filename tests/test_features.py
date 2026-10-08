"""Catalog, checks, BOM, typical details, submittal package, print set and the AI draft (fake client)."""
import json
from types import SimpleNamespace

import pymupdf
import pytest

from sketch2cad import bom, checks, typical
from sketch2cad.ai import sketch
from sketch2cad.catalog import Catalog, load_catalog, save_item
from sketch2cad.documents import create_document
from sketch2cad.package import build_package
from sketch2cad.printset import build_print_set
from sketch2cad.projects import create_project


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


@pytest.fixture
def cat():
    return load_catalog()


def codes(findings):
    return [f.code for f in findings]


# ---------------------------------------------------------------- catalog
def test_catalog_seed_and_sources(cat):
    avk = cat.get("avk_0661_dn150")
    assert avk["dims"]["L"] == 210 and avk["flange"]["PCD"] == 240
    assert avk["name"]["he"].startswith("מגוף")
    assert Catalog.missing_source(avk) == ["t"]                       # t comes from the Plasson data
    assert Catalog.missing_source(cat.get("bolt_m20_ss316")) == ["source"]
    assert {i["id"] for i in cat.items("pipe")} >= {"pexgol_160_c10", "pipe_bt_400"}


def test_project_catalog_overrides(tmp_path):
    p = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    save_item({"id": "golan_pex5081_160", "category": "connector", "name": {"en": "Golan", "he": "גולן"},
               "dims": {"L": 240}, "source": {"submittal": "57 00 00-8.1", "fields": ["L"]}}, p)
    c = load_catalog(p)
    assert c.get("golan_pex5081_160")["dims"]["L"] == 240
    assert Catalog.missing_source(c.get("golan_pex5081_160")) == []


# ---------------------------------------------------------------- checks
def test_cover_check(cat):
    ok = typical.generate("riser_connection", catalog=cat)
    assert "cover" not in codes(checks.run_checks(ok, cat))
    shallow = typical.generate("riser_connection", {"cover": 900}, catalog=cat)
    found = [f for f in checks.run_checks(shallow, cat) if f.code == "cover"]
    assert found and found[0].severity == "error" and "900" in found[0].en


def test_flange_and_bolt_checks(cat):
    spec = typical.generate("riser_connection", catalog=cat)
    assert "flange_drilling" in codes(checks.run_checks(spec, cat))   # Golan ASA150 vs PN16
    spec["legend"] = [l for l in spec["legend"] if l.get("item") != "bolt_m20_ss316"]
    assert "bolts_missing" in codes(checks.run_checks(spec, cat))
    c = Catalog({i["id"]: i for i in cat.items()})
    c.add({"id": "bolt_cs", "category": "bolt", "material": "Carbon steel 8.8"})
    spec["legend"].append({"no": 9, "en": "BOLTS", "he": "ברגים", "item": "bolt_cs"})
    assert "bolts_material" in codes(checks.run_checks(spec, c))


def test_discharge_distance(cat):
    spec = typical.generate("trench_to_manhole", {"run_length": 10.0}, catalog=cat)
    assert "discharge_distance" not in codes(checks.run_checks(spec, cat))
    spec = typical.generate("trench_to_manhole", {"run_length": 30.0, "max_discharge_distance": 20.0}, catalog=cat)
    assert "discharge_distance" in codes(checks.run_checks(spec, cat))
    spec["elements"] = [e for e in spec["elements"] if e["type"] not in ("manhole", "pipe")]
    assert "no_outlet" in codes(checks.run_checks(spec, cat))


def test_unknown_item(cat):
    spec = {"units": "mm", "elements": [{"id": "x", "type": "pipe", "item": "nope", "points": [[0, 0], [1, 0]]}]}
    assert checks.run_checks(spec, cat)[0].code == "unknown_item"


# ---------------------------------------------------------------- BOM
def test_bom_quantities(cat, tmp_path):
    spec = typical.generate("valve_in_line", {"pipe_length": 1000}, catalog=cat)
    rows = {r["item"]: r for r in bom.build_bom(spec, cat)}
    assert rows["steel_pipe_6in_sch40"]["qty"] == 2.0 and rows["steel_pipe_6in_sch40"]["unit"] == "m"
    assert rows["flange_6in_pn16"]["qty"] == 2 and rows["avk_0661_dn150"]["qty"] == 1
    assert rows["avk_0661_dn150"]["source"] == "07 00 00-7.1"
    out = bom.bom_to_xlsx(list(rows.values()), tmp_path / "bom.xlsx", lang="he", title="20117")
    from openpyxl import load_workbook
    ws = load_workbook(out).active
    assert ws.sheet_view.rightToLeft and ws.cell(3, 1).value == "מס'"


def test_bom_metres_for_m_units(cat):
    spec = typical.generate("trench_to_manhole", {"run_length": 10.0}, catalog=cat)
    rows = {r["item"]: r for r in bom.build_bom(spec, cat)}
    assert rows["trench_drain_generic"]["unit"] == "m" and 9 < rows["trench_drain_generic"]["qty"] < 10


# ---------------------------------------------------------------- typicals
def test_typicals_generate_valid_specs(cat):
    names = [t.name for t in typical.list_typicals()]
    assert names == ["riser_connection", "trench_to_manhole", "valve_in_line"]
    for name in names:
        spec = typical.generate(name, catalog=cat)
        types = {e["type"] for e in spec["elements"]}
        assert types <= set(sketch.ELEMENT_TYPES)
        assert all(l["he"] and l["en"] for l in spec["legend"])
        assert len({e["id"] for e in spec["elements"]}) == len(spec["elements"])


# ---------------------------------------------------------------- package + print set
@pytest.fixture
def project_with_doc(tmp_path, cat):
    p = create_project(tmp_path / "projects", "20117", "חצרים", "Hatzerim", client="USACE")
    d = create_document(p, title_he="חיבור לגמל קיים", title_en="Connection to existing camel",
                        discipline="W", subject="CAMEL")
    spec = typical.generate("riser_connection", catalog=cat)
    (d.path / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    drawing = pymupdf.open()
    drawing.new_page(width=1191, height=842).insert_text((72, 72), "DRAWING SHEET")
    drawing.save(d.path / f"{d.code}.pdf")
    d.outputs["pdf"] = f"{d.code}.pdf"
    d.save()
    sheet = pymupdf.open()
    sheet.new_page().insert_text((72, 72), "AVK DATASHEET")
    sheet.save(p.path / "00_Sources" / "0661_standard_Israel.pdf")
    return p, d


@pytest.mark.parametrize("lang", ["en", "he"])
def test_package(project_with_doc, cat, tmp_path, lang):
    p, d = project_with_doc
    out = build_package(p, d, cat, tmp_path / f"pkg_{lang}.pdf", lang=lang)
    pdf = pymupdf.open(out)
    text = "".join(page.get_text() for page in pdf)
    assert "DRAWING SHEET" in text and "AVK DATASHEET" in text
    assert "07 00 00-7.1" in text and "20117-W-CAMEL-01" in text
    assert ("SUBMITTAL PACKAGE" if lang == "en" else "הגשה") in text


def test_print_set(project_with_doc, tmp_path):
    p, d = project_with_doc
    create_document(p, title_en="No pdf yet", title_he="ללא", discipline="W", subject="X")
    out = build_print_set(p, tmp_path / "set.pdf", lang="en")
    pdf = pymupdf.open(out)
    text = pdf[0].get_text()
    assert "20117-W-CAMEL-01" in text and "no PDF" in text
    assert "DRAWING SHEET" in pdf[1].get_text()
    assert [t[1].split(" ")[0] for t in pdf.get_toc()] == ["Sheet", "20117-W-CAMEL-01"]


# ---------------------------------------------------------------- AI draft (no network)
class FakeStream:
    def __init__(self, text, captured, kwargs):
        captured.update(kwargs)
        self.message = SimpleNamespace(stop_reason="end_turn", model=sketch.MODEL,
                                       content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def fake_client(text, captured):
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        stream=lambda **kw: FakeStream(text, captured, kw))))


def test_ai_draft_parses_and_normalizes(tmp_path, cat):
    img = tmp_path / "sketch.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pix.clear_with(255)
    pix.save(img)
    reply = {"spec": {"units": "mm", "elements": [
        {"type": "pipe", "points": [[0, 0], [1000, 0]], "item": "steel_pipe_6in_sch40"},
        {"type": "spaceship"}]},
        "assumptions": [{"en": "Valve height estimated", "he": "גובה המגוף משוער"}], "questions": []}
    captured = {}
    draft = sketch.draft_spec_from_images([img], "branch from the left leg", catalog=cat,
                                          client=fake_client("```json\n" + json.dumps(reply) + "\n```", captured))
    assert [e["type"] for e in draft.spec["elements"]] == ["pipe"]
    assert draft.spec["elements"][0]["id"] == "pipe_0"
    assert draft.warnings and "spaceship" in draft.warnings[0]
    assert draft.assumptions[0]["he"] == "גובה המגוף משוער"
    assert captured["model"] == "claude-opus-5-5"
    assert "avk_0661_dn150" in captured["system"] and "trench_drain" in captured["system"]
    blocks = captured["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and "left leg" in blocks[-1]["text"]
