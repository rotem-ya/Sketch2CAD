import json
import sys
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from bidi.algorithm import get_display

from sketch2cad import templates
from sketch2cad.drawing import preview, render
from sketch2cad.drawing.text import has_hebrew, to_visual

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ITEMS = {   # a tiny catalog subset (FORMATS.md section 2 keys)
    "avk_0661_dn150": {"name": {"en": "Gate valve AVK 06/61 DN150 PN16", "he": "מגוף טריז AVK 06/61"},
                       "flange": {"D": 285, "PCD": 240, "holes": 8, "hole_d": 23, "t": 24},
                       "dims": {"L": 210, "H": 448, "Dt": 212}},
    "elbow_6in_lr90": {"dn": 150, "dims": {"A": 229, "OD": 168.3}},
    "steel_pipe_6in_sch40": {"dn": 150, "dims": {"OD": 168.3}},
    "flange_6in_pn16": {"flange": {"D": 285, "PCD": 240, "t": 24}},
    "pexgol_160_c10": {"dims": {"OD": 160}},
    "golan_pex5081_160": {"dims": {"L": 250}},
    "manhole_conc_125": {"dims": {"Di": 1250, "Do": 1550}},
    "grate_e600": {"dims": {"clear": 600}},
    "trench_drain_generic": {"dims": {"width": 320}},
    "pipe_bt_400": {"dn": 400, "dims": {"OD": 480}},
    "pipe_pvc_200": {"dn": 200, "dims": {"OD": 200}},
}


class FakeCatalog:
    def get(self, item_id):
        return ITEMS.get(item_id)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


def load_example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def all_texts(doc) -> list[str]:
    """Model-space TEXT/MTEXT content."""
    msp = doc.modelspace()
    return [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in msp.query("TEXT MTEXT")]


def assert_submission(doc) -> None:
    """Every entity is BYLAYER, and layers 0 and DEFPOINTS hold no geometry."""
    for block in doc.blocks:
        for entity in block:
            if not entity.dxf.hasattr("layer"):
                continue
            assert entity.dxf.layer.upper() not in {"0", "DEFPOINTS"}, (
                block.name, entity.dxftype(), entity.dxf.layer)
            if entity.dxf.hasattr("color"):
                assert entity.dxf.color == 256, (block.name, entity.dxftype(), entity.dxf.color)
            if entity.dxf.hasattr("linetype") and entity.dxf.linetype:
                assert entity.dxf.linetype.upper() == "BYLAYER", (
                    block.name, entity.dxftype(), entity.dxf.linetype)
            if entity.dxf.hasattr("lineweight"):
                # BYLAYER, or an explicit weight such as a 0.50 mm ground line.
                assert entity.dxf.lineweight == -1 or entity.dxf.lineweight >= 5, (
                    block.name, entity.dxftype(), entity.dxf.lineweight)


def dimension_texts(doc) -> list[str]:
    """The rendered text of every DIMENSION (what AutoCAD shows)."""
    out = []
    for dim in doc.modelspace().query("DIMENSION"):
        out += [e.text for e in dim.get_geometry_block() if e.dxftype() == "MTEXT"]
    return out


DOC = {"code": "20117-W-CAMEL-02", "rev": "B", "title": {"en": "Camel", "he": "גמל"},
       "title_overrides": {"line3": "RFI-0021 - AS-MADE"}}
PRJ = {"code": "20117", "name": {"en": "Hatzerim 20117", "he": "חצרים"}, "client": "USACE / CDM Smith"}


# ------------------------------------------------------------------ examples end to end
@pytest.mark.parametrize("catalog", [FakeCatalog(), None], ids=["fake-catalog", "no-catalog"])
@pytest.mark.parametrize("name, units, hebrew, dims", [
    ("camel_simple", 4, "מגוף טריז", ["229", "210", "~183"]),
    ("bldgP_trench_proposal", 6, "שוחת ניקוז", ["62.2 (APPROX.)", "40", "13.9"]),
])
def test_render_examples(tmp_path, catalog, name, units, hebrew, dims):
    res = render(load_example(name), tmp_path, name, project=PRJ, document=DOC, catalog=catalog,
                 formats=("dxf", "png", "pdf"))
    for fmt in ("dxf", "png", "pdf"):
        assert res[fmt].exists() and res[fmt].stat().st_size > 1000
    with pymupdf.open(res["pdf"]) as pdf:                                # true A3 page -> prints to scale
        page = pdf[0].rect
        assert (page.width * 25.4 / 72, page.height * 25.4 / 72) == pytest.approx((420, 297), abs=0.5)
    assert isinstance(res["warnings"], list)

    doc = ezdxf.readfile(res["dxf"])
    assert doc.header["$INSUNITS"] == units
    for layer in ("EXISTING", "NEW", "PROPOSED", "DIM", "TEXT", "LEGEND", "FRAME", "HATCH", "CENTER"):
        assert layer in doc.layers
    assert doc.styles.get("ARIAL").dxf.font == "arial.ttf"

    texts = all_texts(doc)
    hebrew_texts = [t for t in texts if has_hebrew(t)]
    assert any(hebrew in t for t in hebrew_texts)                       # logical order, never pre-reversed
    assert not any(get_display(hebrew) in t for t in hebrew_texts)
    assert all(t.startswith("\\pxqr;") for t in hebrew_texts)           # right-aligned MTEXT paragraphs
    assert any("LEGEND / מקרא" in t for t in texts)

    shown = dimension_texts(doc)
    for value in dims:                                                  # dimlfac 1: no x100 values
        assert value in shown
    assert not any(s.startswith(("22900", "6220")) for s in shown)
    assert_submission(doc)


def test_diameter_code_kept_in_dxf(tmp_path):
    res = render(load_example("bldgP_trench_proposal"), tmp_path, "b", catalog=FakeCatalog(), formats=("dxf",))
    texts = all_texts(ezdxf.readfile(res["dxf"]))
    assert any("%%c400" in t and has_hebrew(t) for t in texts)
    assert not any("Ø" in t for t in texts)


def test_catalog_sizes_used_and_missing_items_reported(tmp_path):
    spec = {"units": "mm", "scale": 10, "elements": [
        {"id": "v", "type": "gate_valve", "status": "new", "at": [0, 0], "item": "avk_0661_dn150"},
        {"id": "p", "type": "pipe", "status": "existing", "points": [[0, 500], [1000, 500]], "item": "nope"}]}
    res = render(spec, tmp_path, "c", catalog=FakeCatalog(), formats=("dxf",))
    doc = ezdxf.readfile(res["dxf"])
    assert any(name.startswith("S2C_GV_ELEVATION_210_448_212_285_24") for name in (b.name for b in doc.blocks))
    assert any("'nope'" in w for w in res["warnings"])
    pipe_lines = doc.modelspace().query("LWPOLYLINE[layer=='EXISTING']")
    ys = sorted(p.get_points()[0][1] for p in pipe_lines)
    assert ys == pytest.approx([450, 550])                              # default OD 100 mm


ALL_TYPES = [
    {"type": "pipe", "points": [[0, 0], [1500, 0], [1500, 800]], "item": "steel_pipe_6in_sch40", "centerline": True,
     "label": {"en": "PIPE", "he": "צינור"}},
    {"type": "bend", "center": [1500, -600], "radius": 229, "start_angle": 0, "end_angle": 90, "od": 168.3},
    {"type": "flange", "at": [300, 0], "rotation": 0, "item": "flange_6in_pn16"},
    {"type": "gate_valve", "at": [700, 0], "rotation": 0, "item": "avk_0661_dn150"},
    {"type": "gate_valve", "at": [700, -900], "rotation": 0, "view": "plan", "item": "avk_0661_dn150"},
    {"type": "connector", "at": [-200, 0], "rotation": 180, "length": 250, "item": "golan_pex5081_160"},
    {"type": "manhole", "at": [2600, 0], "cover": "grate", "item": "manhole_conc_125", "cover_item": "grate_e600"},
    {"type": "manhole", "at": [2600, -1800], "cover": "closed", "item": "manhole_conc_125"},
    {"type": "trench_drain", "points": [[0, -1500], [1200, -1500]], "item": "trench_drain_generic"},
    {"type": "rect", "corners": [[-900, -1300], [-300, -700]], "hatch": "sand"},
    {"type": "rect", "corners": [[-900, -2300], [-300, -1700]], "hatch": "concrete", "style": "dashed"},
    {"type": "polyline", "points": [[-1000, 300], [3400, 300]], "style": "ground"},
    {"type": "polyline", "points": [[-1000, -2500], [3400, -2500]], "style": "berm"},
    {"type": "text", "at": [0, 900], "text": {"en": "TEXT", "he": "טקסט"}, "height": 3.5},
    {"type": "text", "at": [200, -2100], "text": {"en": "LEADER", "he": "חץ"}, "leader_to": [600, -1500]},
    {"type": "flow_arrow", "at": [1000, 300], "angle": 0},
    {"type": "north_arrow", "at": [3200, 900]},
    {"type": "weld", "at": [1500, 400], "axis": "v", "od": 168.3},
    {"type": "break", "at": [1500, 800], "axis": "v", "od": 168.3},
    {"type": "circle", "at": [2000, -900], "radius": 50},
]


def test_circle_passes_validation_and_renders(tmp_path):
    """A documented circle (at, radius) is kept by normalize_spec and drawn, with no drop warning."""
    from sketch2cad.spec import normalize_spec
    element = {"id": "circle-25", "type": "circle", "status": "existing", "at": [365, 1150], "radius": 22}
    spec, warnings = normalize_spec({"units": "mm", "scale": 15, "elements": [dict(element)]})
    assert spec["elements"] == [element]
    assert not any("Dropped" in warning for warning in warnings)
    res = render(spec, tmp_path, "circle", formats=("dxf",))
    assert not any("circle" in warning or "Dropped" in warning for warning in res["warnings"])
    drawn = list(ezdxf.readfile(res["dxf"]).modelspace().query("CIRCLE"))
    assert len(drawn) == 1
    assert (drawn[0].dxf.center.x, drawn[0].dxf.center.y) == pytest.approx((365, 1150))
    assert drawn[0].dxf.radius == pytest.approx(22)
    assert drawn[0].dxf.layer == "EXISTING"


def test_every_element_type_draws(tmp_path):
    elements = [{"id": f"e{i}", "status": ("existing", "new", "proposed")[i % 3], **el}
                for i, el in enumerate(ALL_TYPES)]
    res = render({"units": "mm", "scale": 20, "elements": elements}, tmp_path, "all", catalog=FakeCatalog(),
                 formats=("dxf", "png"))
    assert not [w for w in res["warnings"] if "skipped" in w and "DWG" not in w]
    msp = ezdxf.readfile(res["dxf"]).modelspace()
    assert len(msp.query("HATCH")) == 2 and msp.query("INSERT") and msp.query("ARC") and msp.query("SOLID")


def test_bad_elements_are_skipped_with_warning(tmp_path):
    spec = {"units": "mm", "scale": 10, "elements": [
        {"id": "x", "type": "spaceship", "at": [0, 0]},
        {"id": "y", "type": "pipe", "status": "new"},
        {"id": "z", "type": "weld", "status": "new", "at": [0, 0], "od": 100}]}
    res = render(spec, tmp_path, "bad", catalog=FakeCatalog(), formats=("dxf",))
    joined = " ".join(res["warnings"])
    assert "'x'" in joined and "'y'" in joined
    assert ezdxf.readfile(res["dxf"]).modelspace().query("LINE[layer=='NEW']")


def test_below_grade_dashed_and_proposed_red(tmp_path):
    spec = {"units": "mm", "scale": 10, "elements": [
        {"id": "a", "type": "pipe", "status": "proposed", "below_grade": True, "od": 100,
         "points": [[0, 0], [1000, 0]]}]}
    doc = ezdxf.readfile(render(spec, tmp_path, "d", catalog=FakeCatalog(), formats=("dxf",))["dxf"])
    lines = doc.modelspace().query("LWPOLYLINE[layer=='PROPOSED_DASH']")
    assert len(lines) == 2
    assert all(p.dxf.linetype == "BYLAYER" and p.dxf.color == 256 for p in lines)
    assert doc.layers.get("PROPOSED_DASH").dxf.linetype == "DASHED"
    assert doc.layers.get("PROPOSED").color == 1
    assert doc.layers.get("PROPOSED_DASH").color == 1
    assert doc.layers.get("EXISTING").color == 8
    assert_submission(doc)


def test_language_modes(tmp_path):
    spec = load_example("camel_simple")
    spec["language"] = "en"
    texts = all_texts(ezdxf.readfile(render(spec, tmp_path, "en", catalog=FakeCatalog(), formats=("dxf",))["dxf"]))
    assert not any(has_hebrew(t) for t in texts)
    spec["language"] = "he"
    texts = all_texts(ezdxf.readfile(render(spec, tmp_path, "he", catalog=FakeCatalog(), formats=("dxf",))["dxf"]))
    assert "DESCRIPTION" not in texts and any("תיאור" in t for t in texts)


def test_auto_fit_warns_when_too_big(tmp_path):
    spec = {"units": "m", "scale": 100, "elements": [
        {"id": "b", "type": "rect", "status": "existing", "corners": [[0, 0], [200, 100]]}]}
    res = render(spec, tmp_path, "big", catalog=FakeCatalog(), formats=("dxf",))
    assert any("does not fit" in w and "consider 1:750" in w for w in res["warnings"])


def test_legend_area_template_places_table_in_column(tmp_path):
    spec = load_example("bldgP_trench_proposal")
    spec["sheet"]["template"] = "a3_legend"
    spec["scale"] = 750
    doc = ezdxf.readfile(render(spec, tmp_path, "la", catalog=FakeCatalog(), formats=("dxf",))["dxf"])
    frame = next(p for p in doc.modelspace().query("LWPOLYLINE[layer=='FRAME']") if p.dxf.const_width > 0)
    x0 = min(x for x, *_ in frame.get_points())
    title = next(e for e in doc.modelspace().query("MTEXT") if "LEGEND / מקרא" in e.text)
    assert title.dxf.insert.x > x0 + 289 * 0.75                         # inside the right-hand column


# ------------------------------------------------------------------ templates
def test_list_templates_has_the_three():
    ids = [t["id"] for t in templates.list_templates()]
    assert {"a3_simple", "a3_legend", "usace_a1"} <= set(ids)
    t = templates.load_template("usace_a1")
    assert t["paper"] == {"w": 841, "h": 594} and t["frame"] == {"w": 821, "h": 574}


def test_user_template_overrides_repo(tmp_path):
    user_dir = Path(tmp_path / "home" / "templates")
    user_dir.mkdir(parents=True)
    data = (templates.REPO_DIR / "a3_simple.yaml").read_text(encoding="utf-8")
    (user_dir / "mine.yaml").write_text(data.replace('en: "A3 simple"', 'en: "My A3"'), encoding="utf-8")
    names = {t["id"]: t["name"]["en"] for t in templates.list_templates()}
    assert names["a3_simple"] == "My A3"


def test_titleblock_values_and_overrides(tmp_path):
    spec = {"units": "mm", "scale": 15, "sheet": {"template": "usace_a1", "fields": {"title_en": "FROM SPEC"}},
            "elements": [{"id": "r", "type": "rect", "status": "new", "corners": [[0, 0], [1000, 500]]}]}
    doc = {**DOC, "title_overrides": {"title_en": "OVERRIDDEN TITLE", "contract": "W912-C-0117"}}
    res = render(spec, tmp_path, "tb", project=PRJ, document=doc, catalog=FakeCatalog(), formats=("dxf",))
    texts = all_texts(ezdxf.readfile(res["dxf"]))
    joined = "\n".join(texts)
    assert "OVERRIDDEN TITLE" in joined and "FROM SPEC" not in joined
    assert "W912-C-0117" in texts and "20117-W-CAMEL-02" in texts and "1:15" in texts
    assert any("גמל" in t for t in texts) and any("חצרים" in t for t in texts)
    assert "{" not in joined                                            # missing placeholders -> ""


def test_build_values_precedence():
    spec = {"scale": 2.5, "sheet": {"fields": {"line3": "spec", "title_he": "מהמפרט"}}}
    values = templates.build_values(spec, PRJ, {**DOC, "title_overrides": {"line3": "doc"}})
    assert values["scale"] == "2.5" and values["line3"] == "doc" and values["title_he"] == "מהמפרט"
    assert values["project_name_en"] == "Hatzerim 20117" and values["code"] == "20117-W-CAMEL-02"


# ------------------------------------------------------------------ DWG and preview
def test_dwg_skipped_when_converter_missing(tmp_path, monkeypatch):
    from sketch2cad.io import oda

    def no_converter(*args, **kwargs):
        raise oda.ConverterNotFound("ODA File Converter not found")

    monkeypatch.setattr(oda, "convert", no_converter)
    res = render(load_example("camel_simple"), tmp_path, "w", catalog=FakeCatalog(), formats=("dxf", "dwg"))
    assert "dwg" not in res and res["dxf"].exists()
    assert any(w.startswith("DWG skipped") for w in res["warnings"])


def test_dwg_skipped_when_module_missing(tmp_path, monkeypatch):
    import sketch2cad.io
    monkeypatch.setitem(sys.modules, "sketch2cad.io.oda", None)
    monkeypatch.delattr(sketch2cad.io, "oda", raising=False)     # an earlier import binds the attribute
    res = render(load_example("camel_simple"), tmp_path, "w", catalog=FakeCatalog(), formats=("dwg",))
    assert "dwg" not in res and res["dxf"].exists()
    assert any(w.startswith("DWG skipped") for w in res["warnings"])


def test_to_visual_replaces_diameter_before_bidi():
    s = "\\pxqr;צינור %%c200 חדש\\Pשורה שנייה"
    assert to_visual(s) == "\\pxqr;" + get_display("צינור Ø200 חדש") + "\\P" + get_display("שורה שנייה")
    assert to_visual("%%c400 BT") == "Ø400 BT"


def test_preview_text_is_drawn_as_paths_not_matplotlib_text(tmp_path):
    """The ezdxf add-on draws glyph outlines itself (no matplotlib text shaping), so visual order is right."""
    res = render(load_example("camel_simple"), tmp_path, "p", catalog=FakeCatalog(), formats=("dxf",))
    doc = preview.preview_doc(res["dxf"])
    assert not any(has_hebrew(e.text) and "מגוף טריז" in e.text for e in doc.modelspace().query("MTEXT"))
    fig = preview.render_figure(doc, (-3000, -2000, 3000, 2000), (420, 297))
    try:
        assert len(fig.axes[0].texts) == 0 and len(fig.axes[0].patches) > 0
    finally:
        preview.plt.close(fig)


# ------------------------------------------------------------------ labels and multi-view sheets
ISO = {2.5, 3.5, 5.0, 7.0}


def dense_plan() -> dict:
    """A 1:400 plan where a trench and a pipe 1 m apart would otherwise stack their labels."""
    return {
        "version": 1, "units": "m", "scale": 400, "paper": "A3", "language": "both", "view": "PLAN",
        "sheet": {"template": "a3_simple", "fields": {
            "title_en": "DRAIN PLAN", "title_he": "תוכנית ניקוז", "line3": "SYNTHETIC"}},
        "elements": [
            {"id": "bldg", "type": "rect", "status": "existing", "corners": [[0, 0], [94.3, 52]]},
            {"id": "trench", "type": "trench_drain", "status": "new", "width": 0.32,
             "points": [[20, 10], [55, 10]], "label": {"en": "TRENCH DRAIN", "he": "תעלת ניקוז"}},
            {"id": "pipe", "type": "pipe", "status": "new", "od": 0.2,
             "points": [[20, 11], [55, 11]], "label": {"en": "BT PIPE", "he": "צינור בטון"}},
            {"id": "mh1", "type": "circle", "status": "existing", "at": [20, 10], "radius": 0.6,
             "label": {"en": "MH-1 INV 225.20", "he": "שוחה 1"}},
            {"id": "mh2", "type": "circle", "status": "existing", "at": [55, 11], "radius": 0.6,
             "label": {"en": "MH-2 INV 224.94", "he": "שוחה 2"}},
            {"id": "note", "type": "text", "at": [70, 40], "height": 2.5,
             "text": {"en": "INV SEE PROFILE", "he": "רום בחתך"}},
        ],
        "dimensions": [
            {"type": "chain", "points": [[0, 0], [40, 0], [94.3, 0]], "axis": "h", "base": -4, "decimals": 1},
        ],
    }


def _fingerprint(doc) -> list:
    rows = []
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind == "TEXT":
            rows.append((kind, entity.dxf.text, round(entity.dxf.insert.x, 4), round(entity.dxf.insert.y, 4)))
        elif kind == "MTEXT":
            rows.append((kind, entity.text, round(entity.dxf.insert.x, 4), round(entity.dxf.insert.y, 4)))
        elif kind == "LINE":
            rows.append((kind, round(entity.dxf.start.x, 4), round(entity.dxf.start.y, 4),
                         round(entity.dxf.end.x, 4), round(entity.dxf.end.y, 4)))
        elif kind == "DIMENSION":
            rows.append((kind, round(entity.dxf.defpoint.x, 4), round(entity.dxf.defpoint.y, 4),
                         entity.dxf.text))
    return rows


def _inserts_of(doc, fragment: str) -> list[tuple[float, float]]:
    found = []
    for entity in doc.modelspace().query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        if fragment in text:
            found.append((entity.dxf.insert.x, entity.dxf.insert.y))
    return found


def _text_centers(doc) -> list[tuple[str, float, float]]:
    """Box centre of each model-space text entity outside the title block."""
    from ezdxf import bbox as ezbbox

    found = []
    for entity in doc.modelspace().query("TEXT MTEXT"):
        if entity.dxf.layer == "FRAME":
            continue
        ext = ezbbox.extents([entity], fast=True)
        if not ext.has_data:
            continue
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        found.append((text, (ext.extmin.x + ext.extmax.x) / 2, (ext.extmin.y + ext.extmax.y) / 2))
    return found


def _paired_moves(before, after) -> tuple[list[str], list[float]]:
    """Match each unnudged text to the nearest same string. Missing strings were omitted."""
    used = set()
    missing, moves = [], []
    for text, x, y in before:
        best_i = best_d = None
        for i, (text2, x2, y2) in enumerate(after):
            if i in used or text2 != text:
                continue
            distance = ((x2 - x) ** 2 + (y2 - y) ** 2) ** 0.5
            if best_d is None or distance < best_d:
                best_i, best_d = i, distance
        if best_i is None:
            missing.append(text)
        else:
            used.add(best_i)
            moves.append(best_d)
    return missing, moves


def _label_stacks(layout, join: float):
    """One box per label. Lines closer than `join` (model units) belong to the same label."""
    from sketch2cad.drawing.place import _entity_box, _overlaps

    boxes = []
    for entity in layout.query("TEXT MTEXT"):
        if entity.dxf.layer != "TEXT":
            continue
        box = _entity_box(entity)
        if box:
            boxes.append(box)
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _overlaps(boxes[i], boxes[j], join):
                parent[find(j)] = find(i)
    groups: dict[int, list] = {}
    for i, box in enumerate(boxes):
        groups.setdefault(find(i), []).append(box)
    unions = []
    for members in groups.values():
        unions.append((min(b[0] for b in members), min(b[1] for b in members),
                       max(b[2] for b in members), max(b[3] for b in members)))
    return unions


def test_dense_labels_do_not_overlap(tmp_path):
    from sketch2cad.drawing.place import _overlaps, leader_crosses_text, overlapping_text

    res = render(dense_plan(), tmp_path, "dense", formats=("dxf", "png"))
    doc = ezdxf.readfile(res["dxf"])
    msp = doc.modelspace()
    hits = overlapping_text(msp)
    assert hits == [], hits
    assert leader_crosses_text(msp) == []
    k = 400 * 0.001
    stacks = _label_stacks(msp, 1.35 * k)
    gap = 1.5 * k
    close = [(i, j) for i in range(len(stacks)) for j in range(i + 1, len(stacks))
             if _overlaps(stacks[i], stacks[j], gap - 1e-6)]
    assert close == [], close
    assert_submission(doc)
    assert res["png"].stat().st_size > 1000


def test_label_placement_is_deterministic(tmp_path):
    first = _fingerprint(ezdxf.readfile(render(dense_plan(), tmp_path, "a", formats=("dxf",))["dxf"]))
    second = _fingerprint(ezdxf.readfile(render(dense_plan(), tmp_path, "b", formats=("dxf",))["dxf"]))
    assert first == second
    assert len(first) > 5


def test_label_at_is_a_hint_and_pinned_stays(tmp_path):
    spec = {"units": "mm", "scale": 10, "language": "en", "elements": [
        {"id": "a", "type": "circle", "at": [0, 0], "radius": 4,
         "label": {"en": "ALPHA LABEL"}, "label_at": [80, 80]},
        {"id": "b", "type": "circle", "at": [0, 200], "radius": 4,
         "label": {"en": "BETA LABEL"}, "label_at": [80, 80]},
        {"id": "c", "type": "circle", "at": [2000, 0], "radius": 4,
         "label": {"en": "CLEAR"}, "label_at": [2100, 40]},
        {"id": "d", "type": "circle", "at": [4000, 0], "radius": 4,
         "label": {"en": "PINNED"}, "label_at": [80, 80], "label_pinned": True},
    ]}
    doc = ezdxf.readfile(render(spec, tmp_path, "hint", formats=("dxf",))["dxf"])
    pinned = _inserts_of(doc, "PINNED")
    clear = _inserts_of(doc, "CLEAR")
    alpha = _inserts_of(doc, "ALPHA")
    beta = _inserts_of(doc, "BETA")
    assert pinned == [(80, 80)]
    assert clear == [(2100, 40)]
    # the pinned label owns (80, 80); both hints that asked for it have to move
    assert alpha and beta
    assert all(point != (80, 80) for point in alpha + beta)
    from sketch2cad.drawing.place import overlapping_text
    assert overlapping_text(doc.modelspace()) == []


def test_text_heights_follow_iso_3098(tmp_path):
    doc = ezdxf.readfile(render(dense_plan(), tmp_path, "iso", formats=("dxf",))["dxf"])
    k = 400 * 0.001
    for entity in doc.modelspace().query("TEXT MTEXT"):
        if entity.dxf.layer == "FRAME":
            continue
        height = entity.dxf.char_height if entity.dxftype() == "MTEXT" else entity.dxf.height
        assert round(height / k, 3) in ISO
    for dim in doc.modelspace().query("DIMENSION"):
        for entity in dim.virtual_entities():
            if entity.dxftype() == "MTEXT":
                assert round(entity.dxf.char_height / k, 3) in ISO


def multiview_spec() -> dict:
    return {
        "version": 1, "units": "m", "scale": 400, "paper": "A3", "language": "both", "view": "PLAN",
        "sheet": {"template": "a3_simple", "fields": {"title_en": "MULTI VIEW", "title_he": "רב מבט"}},
        "elements": [
            {"id": "bldg", "type": "rect", "status": "existing", "corners": [[0, 0], [94.3, 52]]},
            {"id": "trench", "type": "trench_drain", "status": "new", "width": 0.3,
             "points": [[10, 20], [40, 20]], "label": {"en": "TRENCH", "he": "תעלה"}},
        ],
        "dimensions": [{"type": "chain", "points": [[0, 0], [94.3, 0]], "axis": "h", "base": -3, "decimals": 1}],
        "views": [
            {"id": "section-a", "title": {"en": "SECTION A-A", "he": "חתך א-א"}, "scale": 50,
             "elements": [
                 {"id": "ground", "type": "polyline", "status": "existing", "style": "ground",
                  "points": [[0, 0], [2.4, 0]]},
                 {"id": "wall", "type": "rect", "status": "new", "corners": [[0.4, 0], [1.6, 1.1]],
                  "label": {"en": "WALL", "he": "קיר"}},
             ],
             "dimensions": [{"type": "chain", "points": [[0.4, 0], [1.6, 0]], "axis": "h",
                             "base": -0.3, "decimals": 2}]},
            {"id": "detail-1", "title": {"en": "DETAIL 1", "he": "פרט 1"}, "scale": 25,
             "elements": [
                 {"id": "box", "type": "rect", "status": "new", "corners": [[-0.5, -0.5], [0.5, 0.5]]},
                 {"id": "grate", "type": "circle", "status": "new", "at": [0, 0], "radius": 0.3,
                  "label": {"en": "GRATE", "he": "רשת"}},
             ]},
        ],
    }


def _viewport_scales(doc, units: str) -> list[float]:
    u = 0.001 if units == "m" else 1.0
    layout = doc.layouts.get("SHEET")
    scales = []
    for vp in layout.query("VIEWPORT"):
        if vp.dxf.status <= 1 or vp.dxf.height <= 0:
            continue
        scales.append((vp.dxf.view_height / vp.dxf.height) / u)
    return sorted(round(scale, 4) for scale in scales)


def test_multiview_sheet_plan_section_detail(tmp_path):
    res = render(multiview_spec(), tmp_path, "sheet", formats=("dxf", "png", "pdf"))
    doc = ezdxf.readfile(res["dxf"])
    assert res["layout"] == "SHEET"
    assert _viewport_scales(doc, "m") == [25.0, 50.0, 400.0]
    psp = doc.layouts.get("SHEET")
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in psp.query("TEXT MTEXT")]
    joined = "\n".join(texts)
    for fragment in ("PLAN / תוכנית", "SCALE 1:400", "SECTION A-A / חתך א-א", "SCALE 1:50",
                     "DETAIL 1 / פרט 1", "SCALE 1:25", "AS SHOWN / כמצוין"):
        assert fragment in joined, fragment
    frame_left, frame_bottom, frame_right, frame_top = 10, 10, 410, 287
    titles = {}
    for entity in psp.query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        titles[text] = (entity.dxf.insert.x, entity.dxf.insert.y)

    def at(fragment):
        return next(point for text, point in titles.items() if fragment in text)

    assert at("SECTION A-A")[1] == pytest.approx(at("DETAIL 1")[1], abs=0.3)
    assert at("SCALE 1:50")[1] < at("SECTION A-A")[1]
    assert abs(at("SCALE 1:50")[0] - at("SECTION A-A")[0]) < 2
    from ezdxf import bbox as ezbbox
    for entity in psp.query("MTEXT"):
        if "תוכנית" not in (entity.text or ""):
            continue
        box = ezbbox.extents([entity], fast=True)
        assert box.extmax.y <= frame_top + 0.5
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1:
            continue
        left = vp.dxf.center.x - vp.dxf.width / 2
        right = vp.dxf.center.x + vp.dxf.width / 2
        bottom = vp.dxf.center.y - vp.dxf.height / 2
        top = vp.dxf.center.y + vp.dxf.height / 2
        assert left >= frame_left + 10 - 0.6
        assert right <= frame_right - 10 + 0.6
        assert bottom >= frame_bottom + 10 - 0.6
        assert top <= frame_top - 10 + 0.6
    for entity in psp.query("TEXT MTEXT"):
        if entity.dxf.layer == "FRAME":
            continue
        height = entity.dxf.char_height if entity.dxftype() == "MTEXT" else entity.dxf.height
        assert round(height, 3) in ISO
    assert_submission(doc)
    # the preview actually drew the viewports, not a blank page
    from PIL import Image
    import numpy as np
    ink = np.asarray(Image.open(res["png"]).convert("L"))
    assert (ink < 250).mean() > 0.002
    with pymupdf.open(res["pdf"]) as pdf:
        page = pdf[0].rect
        assert (page.width * 25.4 / 72, page.height * 25.4 / 72) == pytest.approx((420, 297), abs=0.5)


def test_nts_view_is_labelled_and_fitted(tmp_path):
    spec = {"units": "mm", "scale": 100, "paper": "A3", "language": "en", "view": "PLAN",
            "elements": [{"id": "r", "type": "rect", "status": "existing", "corners": [[0, 0], [5000, 3000]]}],
            "views": [{"id": "scrap", "title": "SCRAP", "scale": "NTS", "fit_mm": 60,
                       "elements": [{"id": "c", "type": "circle", "status": "new", "at": [0, 0], "radius": 40},
                                    {"id": "e", "type": "rect", "status": "new",
                                     "corners": [[0, 0], [80, 40]]}]}]}
    doc = ezdxf.readfile(render(spec, tmp_path, "nts", formats=("dxf",))["dxf"])
    texts = [e.dxf.text if e.dxftype() == "TEXT" else e.text for e in doc.layouts.get("SHEET").query("TEXT MTEXT")]
    assert "SCALE NTS" in texts and "SCRAP" in texts
    scales = _viewport_scales(doc, "mm")
    assert 100 in [round(s) for s in scales]
    assert any(s != 100 for s in scales)
    nts = next(vp for vp in doc.layouts.get("SHEET").query("VIEWPORT")
               if vp.dxf.status > 1 and abs((vp.dxf.view_height / vp.dxf.height) - 100) > 1)
    assert max(nts.dxf.width, nts.dxf.height) == pytest.approx(64, abs=2)


def test_views_round_trip_and_old_specs_stay_compatible():
    from sketch2cad.spec import normalize_spec, parse_reply

    bare = {"units": "mm", "scale": 15, "elements": [
        {"id": "circle-25", "type": "circle", "status": "existing", "at": [365, 1150], "radius": 22}]}
    spec, warnings = normalize_spec(bare)
    assert "views" not in spec and spec["elements"] == bare["elements"]
    assert not any("Dropped" in warning for warning in warnings)

    bad_view = {"views": [{"id": "s", "scale": 50, "elements": [{"type": "spaceship"}, {"type": "circle",
                "at": [0, 0], "radius": 1}]}]}
    spec, warnings = normalize_spec(bad_view)
    assert spec["views"][0]["id"] == "s" and spec["views"][0]["elements"][0]["type"] == "circle"
    assert any("spaceship" in warning for warning in warnings)

    with pytest.raises(ValueError):
        parse_reply('{"views": [{"id": "a", "scale": "huge"}]}')
    parsed = parse_reply('```json\n{"spec": {"views": [{"id": "a", "scale": "nts", "elements": []}]}}\n```')
    assert parsed["spec"]["views"][0]["scale"] == "nts"
    parsed = parse_reply('{"views": [{"id": "a", "scale": "auto", "elements": []}]}')
    assert parsed["views"][0]["scale"] == "auto"
    spec, warnings = normalize_spec({"views": [{"id": "a", "scale": "Auto", "elements": []}]})
    assert spec["views"][0]["scale"] == "auto"
    assert not any("Dropped view" in warning for warning in warnings)
    assert parse_reply('{"scale": 20, "elements": []}')["scale"] == 20


def _frame_limits(doc):
    frame = next(p for p in doc.modelspace().query("LWPOLYLINE[layer=='FRAME']") if p.dxf.const_width > 0)
    xs = [p[0] for p in frame.get_points()]
    ys = [p[1] for p in frame.get_points()]
    return min(xs), min(ys), max(xs), max(ys)


def _outside_frame(doc, tol_mm: float = 1.0):
    from ezdxf import bbox as ezbbox
    x0, y0, x1, y1 = _frame_limits(doc)
    outside = []
    for entity in doc.modelspace():
        if entity.dxftype() == "LWPOLYLINE" and entity.dxf.layer == "FRAME" and entity.dxf.const_width > 0:
            continue
        ext = ezbbox.extents([entity], fast=True)
        if not ext.has_data:
            continue
        if (ext.extmin.x < x0 - tol_mm or ext.extmin.y < y0 - tol_mm
                or ext.extmax.x > x1 + tol_mm or ext.extmax.y > y1 + tol_mm):
            outside.append(entity.dxftype())
    return outside


def test_full_sheet_stays_inside_the_frame(tmp_path):
    from sketch2cad.drawing.place import linework_hits_text

    spec = {
        "version": 1, "units": "m", "scale": 400, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple", "fields": {"title_en": "FULL", "title_he": "מלא"}},
        "notes": {"en": ["Keep the trench clear.", "Inverts as shown."],
                  "he": ["לשמור את התעלה פנויה.", "רומים כמצוין בחתך."]},
        "elements": [
            {"id": "bldg", "type": "rect", "status": "existing", "corners": [[0, 0], [140, 36]]},
            {"id": "pipe", "type": "pipe", "status": "new", "od": 0.2, "points": [[10, 18], [80, 18]],
             "label": {"en": "BT PIPE", "he": "צינור"}},
            {"id": "outlet", "type": "text", "at": [40, 24],
             "text": {"en": "PVC Ø200 OUTLET", "he": "יציאה"}},
            {"id": "end", "type": "circle", "status": "new", "at": [140, 18], "radius": 0.4,
             "label": {"en": "END STRUCTURE", "he": "מבנה קצה"}},
        ],
        "dimensions": [{"type": "chain", "points": [[0, 0], [140, 0]], "axis": "h", "base": -3, "decimals": 1}],
    }
    res = render(spec, tmp_path, "full", formats=("dxf",))
    assert not any("not drawn off the sheet" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    assert _outside_frame(doc, tol_mm=0.6) == []
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in doc.modelspace().query("TEXT MTEXT")]
    assert any("הערות" in t or "פנויה" in t for t in texts)
    assert any("PVC" in t for t in texts)
    assert linework_hits_text(doc.modelspace()) == []


def test_text_is_measured_with_the_bundled_face():
    from ezdxf.fonts import fonts as ezfonts
    from sketch2cad.drawing import text as drawing_text

    manager = ezfonts.font_manager
    bundled = drawing_text._BUNDLED_FONT.resolve()
    for name in ("arial.ttf", "txt", manager.fallback_font_name()):
        entry = manager._font_cache.get(name, manager.fallback_font_name())
        assert Path(entry.file_path).resolve() == bundled


def test_bundled_face_contains_hebrew():
    from fontTools.ttLib import TTFont
    from sketch2cad.drawing.text import _BUNDLED_FONT

    face = TTFont(_BUNDLED_FONT)
    version = " ".join(
        rec.toUnicode() for rec in face["name"].names if rec.nameID == 5
    )
    assert "2.1.5" in version
    cmap = face.getBestCmap()
    missing = [ch for ch in "אבגדהוזחטיכלמנסעפצקרשת" if ord(ch) not in cmap]
    assert missing == []


def test_measurement_ignores_system_fonts(monkeypatch):
    """Empty font search paths still measure with the bundled face, including a missing style."""
    import ezdxf
    from ezdxf.fonts import font_manager as fm_mod
    from ezdxf.fonts import fonts as ezfonts
    from sketch2cad.drawing import text as drawing_text
    from sketch2cad.drawing.place import _entity_box, _overlaps
    from sketch2cad.templates import draw_titleblock, load_template

    sample = "תוכנית ניקוז כללית"
    before = drawing_text.text_width(sample, 3.5)
    manager = ezfonts.font_manager
    try:
        manager.clear()
        monkeypatch.setattr(fm_mod, "WIN_FONT_DIRS", [])
        monkeypatch.setattr(fm_mod, "LINUX_FONT_DIRS", [])
        monkeypatch.setattr(fm_mod, "MACOS_FONT_DIRS", [])
        monkeypatch.setattr(fm_mod, "FONT_DIRECTORIES", {"Linux": [], "Windows": [], "Darwin": []})
        manager.build(support_dirs=False)
        assert manager.has_font("DejaVuSans.ttf") is False
        assert manager.has_font("ArialUni.ttf") is False
        drawing_text.font.cache_clear()
        drawing_text._pin_measurement_font()
        drawing_text.font.cache_clear()
        assert drawing_text.text_width(sample, 3.5) == before
        bundled = drawing_text._BUNDLED_FONT.resolve()
        for name in ("txt", "arial.ttf", "ArialUni.ttf", "DejaVuSans.ttf"):
            entry = manager._font_cache.get(name, manager.fallback_font_name())
            assert Path(entry.file_path).resolve() == bundled
        doc = ezdxf.new()
        assert "ARIAL" not in {style.dxf.name for style in doc.styles}
        narrow = load_template("a3_simple")
        narrow["fields"] = [{**field, "h": 3.5, "w": 80} if field["key"] == "title" else field
                            for field in narrow["fields"]]
        draw_titleblock(doc.modelspace(), narrow, {
            "title_en": "DRAINAGE GENERAL ARRANGEMENT", "title_he": sample,
            "scale": "400", "view": "PLAN"}, 1, "mm", language="both")
        en_box = he_box = None
        for entity in doc.modelspace().query("TEXT MTEXT"):
            text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
            if "DRAINAGE" in text:
                en_box = _entity_box(entity)
            if "ניקוז" in text:
                he_box = _entity_box(entity)
        assert en_box and he_box
        assert not _overlaps(en_box, he_box, 1.0)
    finally:
        ezfonts.load()
        drawing_text._pin_measurement_font()
        drawing_text.font.cache_clear()


def test_measurement_ignores_a_mapped_system_txt_ttf(tmp_path):
    """A system ``txt_____.ttf`` (the Windows stand-in for ``txt``) is not the file bbox opens."""
    import shutil

    from ezdxf.fonts import fonts as ezfonts
    from ezdxf.fonts.font_face import FontFace
    from ezdxf.fonts.font_manager import CacheEntry, FontCache
    from sketch2cad.drawing import text as drawing_text
    from sketch2cad.drawing.place import _entity_box, _overlaps
    from sketch2cad.templates import draw_titleblock, load_template

    from matplotlib import font_manager

    sample = "תוכנית ניקוז כללית"
    bundled = drawing_text._BUNDLED_FONT.resolve()
    bundled_width = ezfonts.make_font(drawing_text.FONT, 3.5).text_width(sample)
    # matplotlib ships DejaVu on Windows and Linux. It is not the bundled face.
    different = Path(font_manager.findfont("DejaVu Sans", fallback_to_default=False))
    assert different.is_file()
    assert different.read_bytes() != bundled.read_bytes()
    folder = tmp_path / "Windows" / "Fonts"
    folder.mkdir(parents=True)
    fake = folder / "txt_____.ttf"
    shutil.copyfile(different, fake)
    manager = ezfonts.font_manager
    key = FontCache.key("txt_____.ttf")
    previous = manager._font_cache._cache.get(key)
    try:
        manager.scan_folder(folder)
        # Windows maps the missing style "txt" onto this filename and matches the family "Txt".
        manager._font_cache._cache[key] = CacheEntry(
            fake, FontFace(filename="txt_____.ttf", family="Txt", style="Regular", weight=400, width=5))
        assert ezfonts.map_shx_to_ttf("txt") == "txt_____.ttf"
        assert ezfonts.map_shx_to_ttf("TXT.SHX") == "txt_____.ttf"
        raw = manager._font_cache._cache[key]
        assert Path(raw.file_path).resolve() == fake.resolve()
        manager._match_cache.clear()
        manager._loaded_ttf_fonts.clear()
        ezfonts.TrueTypeFont._glyph_caches.clear()
        for name in ("txt", "TXT", "txt_____.ttf", "TXT.SHX"):
            assert ezfonts.make_font(name, 3.5).text_width(sample) == bundled_width
        opened = Path(manager.get_ttf_font("txt_____.ttf").reader.file.name).resolve()
        assert opened == bundled
        doc = ezdxf.new()
        narrow = load_template("a3_simple")
        narrow["fields"] = [{**field, "h": 3.5, "w": 80} if field["key"] == "title" else field
                            for field in narrow["fields"]]
        draw_titleblock(doc.modelspace(), narrow, {
            "title_en": "DRAINAGE GENERAL ARRANGEMENT", "title_he": sample,
            "scale": "400", "view": "PLAN"}, 1, "mm", language="both")
        en_box = he_box = None
        for entity in doc.modelspace().query("TEXT MTEXT"):
            text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
            if "DRAINAGE" in text:
                en_box = _entity_box(entity)
            if "ניקוז" in text:
                he_box = _entity_box(entity)
        assert en_box and he_box
        assert not _overlaps(en_box, he_box, 1.0)
    finally:
        if previous is None:
            manager._font_cache._cache.pop(key, None)
        else:
            manager._font_cache._cache[key] = previous
        manager._match_cache.clear()
        manager._loaded_ttf_fonts.clear()
        ezfonts.TrueTypeFont._glyph_caches.clear()


def test_title_height_rounds_up_and_is_not_shrunk():
    from sketch2cad.drawing.standards import snap_text_height
    from sketch2cad.templates import draw_titleblock, load_template

    assert snap_text_height(3.29) == 3.5          # nearest, already up
    assert snap_text_height(4.1) == 3.5           # nearest would shrink
    assert snap_text_height(4.1, up=True) == 5.0
    assert snap_text_height(3.29, up=True) == 3.5
    assert snap_text_height(8, up=True) == 8
    template = load_template("a3_simple")
    template["fields"] = [{**field, "h": 4.1} if field["key"] == "title" else field
                          for field in template["fields"]]
    doc = ezdxf.new()
    msp = doc.modelspace()
    values = {"title_en": "LONG DRAINAGE GENERAL ARRANGEMENT", "title_he": "תוכנית ניקוז כללית ארוכה מאוד",
              "scale": "400", "view": "PLAN"}
    draw_titleblock(msp, template, values, 1, "mm", language="both")
    heights = []
    for entity in msp.query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        if "LONG DRAINAGE" in text or "ניקוז" in text:
            heights.append(entity.dxf.char_height if entity.dxftype() == "MTEXT" else entity.dxf.height)
    assert heights and min(heights) == pytest.approx(5.0)
    # A long pair in a narrow field must shrink or stack. The two titles never overlap.
    from sketch2cad.drawing.place import _entity_box, _overlaps
    narrow = load_template("a3_simple")
    narrow["fields"] = [{**field, "h": 3.5, "w": 80} if field["key"] == "title" else field
                        for field in narrow["fields"]]
    doc2 = ezdxf.new()
    draw_titleblock(doc2.modelspace(), narrow, {
        "title_en": "DRAINAGE GENERAL ARRANGEMENT", "title_he": "תוכנית ניקוז כללית",
        "scale": "400", "view": "PLAN"}, 1, "mm", language="both")
    en_box = he_box = None
    for entity in doc2.modelspace().query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        height = entity.dxf.char_height if entity.dxftype() == "MTEXT" else entity.dxf.height
        if "DRAINAGE" in text:
            en_box = _entity_box(entity)
            assert height <= 3.5 + 1e-6
        if "ניקוז" in text:
            he_box = _entity_box(entity)
            assert height <= 3.5 + 1e-6
    assert en_box and he_box
    assert not _overlaps(en_box, he_box, 1.0)


def test_oversized_view_is_not_placed_off_the_sheet(tmp_path):
    spec = {"units": "m", "scale": 50, "paper": "A3", "language": "en", "view": "plan",
            "elements": [{"id": "r", "type": "rect", "status": "existing", "corners": [[0, 0], [40, 20]]}],
            "views": [{"id": "huge", "title": "SECTION HUGE", "scale": 10,
                       "elements": [{"id": "big", "type": "rect", "status": "new",
                                     "corners": [[0, 0], [30, 20]]}]}]}
    res = render(spec, tmp_path, "huge", formats=("dxf",))
    for key in ("huge", "primary"):
        errs = [w for w in res["warnings"] if w.startswith("ERROR:") and f"view {key!r}" in w]
        assert len(errs) == 1, (key, res["warnings"])
        assert "not shown on this sheet" in errs[0] and "larger sheet" in errs[0] and "A2/A1" in errs[0]
    assert not any("would be 1:" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    psp = doc.layouts.get("SHEET")
    captions = []
    for entity in psp.query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        if "NOT SHOWN" not in text:
            continue
        captions.append(text)
        point = entity.dxf.insert
        assert 10 <= point.x and point.x + 2 <= 410
        assert 10 <= point.y and point.y + entity.dxf.height <= 287.5
    assert "HUGE NOT SHOWN – NEEDS A2 SHEET" in captions
    assert "PRIMARY NOT SHOWN – NEEDS A2 SHEET" in captions
    from sketch2cad.qa import review
    omitted = [f for f in review(spec).findings if f.code == "omitted_view"]
    assert {f.element for f in omitted} >= {"huge", "primary"}
    assert all(f.severity == "error" and f.he for f in omitted)
    from sketch2cad.drawing.layout import STANDARD_SCALES
    assert all(scale in STANDARD_SCALES for scale in _viewport_scales(doc, "m"))
    psp = doc.layouts.get("SHEET")
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1:
            continue
        assert 10 <= vp.dxf.center.x - vp.dxf.width / 2
        assert vp.dxf.center.x + vp.dxf.width / 2 <= 420
        assert 10 <= vp.dxf.center.y - vp.dxf.height / 2
        assert vp.dxf.center.y + vp.dxf.height / 2 <= 297


def _paper_box(entity):
    from ezdxf import bbox as ezbbox

    ext = ezbbox.extents([entity], fast=True)
    if not ext.has_data:
        return None
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def _boxes_overlap(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_omitted_caption_uses_the_strip_between_views():
    """A one-line caption wider than the largest gap sits in the free strip, not on a view."""
    from ezdxf.math import Vec2
    from sketch2cad.drawing.text import put_text
    from sketch2cad.drawing.views import _write_omitted_caption
    from sketch2cad.templates import load_template

    template = load_template("a3_simple")
    doc = ezdxf.new()
    psp = doc.layouts.new("SHEET")
    frame = 10.0
    # Frame coordinates: an 8 mm strip at y 133.7, and a 72.02 mm column that is the largest gap.
    section = (10.0, 27.0, 317.98, 133.7)
    plan = (10.0, 141.7, 317.98, 267.0)
    area = {"x": 10.0, "y": 27.0, "w": 380.0, "h": 240.0}
    for rect, status in ((section, 2), (plan, 3)):
        cx = frame + (rect[0] + rect[2]) / 2
        cy = frame + (rect[1] + rect[3]) / 2
        psp.add_viewport(center=(cx, cy), size=(rect[2] - rect[0], rect[3] - rect[1]),
                         view_center_point=(0, 0), view_height=100, status=status)
    put_text(psp, "חתך א-א", (frame + 30, frame + 100), 3.5, align="left")
    put_text(psp, "TRENCH DRAIN – MUFLE WING", (frame + 30, frame + 60), 2.5, align="left")
    occupied = [section, plan]
    _write_omitted_caption(psp, {"key": "detail-1"}, area, occupied, Vec2(frame, frame), template)
    captions = [entity for entity in psp.query("TEXT MTEXT")
                if "NOT SHOWN" in (entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text)]
    assert len(captions) == 1
    caption = _paper_box(captions[0])
    mid_y = (caption[1] + caption[3]) / 2 - frame
    assert 133.7 < mid_y < 141.7, caption
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1:
            continue
        view = (vp.dxf.center.x - vp.dxf.width / 2, vp.dxf.center.y - vp.dxf.height / 2,
                vp.dxf.center.x + vp.dxf.width / 2, vp.dxf.center.y + vp.dxf.height / 2)
        assert not _boxes_overlap(caption, view)
    for entity in psp.query("TEXT MTEXT"):
        if entity is captions[0]:
            continue
        other = _paper_box(entity)
        assert other and not _boxes_overlap(caption, other)


def test_omitted_caption_wraps_into_the_gap_when_the_strip_is_blocked():
    """When the strip is full, two lines fit the 72 mm gap and still miss every view and text."""
    from ezdxf.math import Vec2
    from sketch2cad.drawing.text import put_text
    from sketch2cad.drawing.views import _write_omitted_caption
    from sketch2cad.templates import load_template

    template = load_template("a3_simple")
    doc = ezdxf.new()
    psp = doc.layouts.new("SHEET")
    frame = 10.0
    section = (10.0, 27.0, 317.98, 133.7)
    plan = (10.0, 141.7, 317.98, 267.0)
    area = {"x": 10.0, "y": 27.0, "w": 380.0, "h": 240.0}
    for rect, status in ((section, 2), (plan, 3)):
        cx = frame + (rect[0] + rect[2]) / 2
        cy = frame + (rect[1] + rect[3]) / 2
        psp.add_viewport(center=(cx, cy), size=(rect[2] - rect[0], rect[3] - rect[1]),
                         view_center_point=(0, 0), view_height=100, status=status)
    put_text(psp, "חתך א-א", (frame + 30, frame + 100), 3.5, align="left")
    put_text(psp, "TRENCH DRAIN – MUFLE WING", (frame + 30, frame + 60), 2.5, align="left")
    put_text(psp, "BLOCK " * 24, (frame + 12, frame + 134.2), 2.5, align="left")
    _write_omitted_caption(psp, {"key": "detail-1"}, area, [section, plan], Vec2(frame, frame), template)
    captions = [entity for entity in psp.query("TEXT MTEXT")
                if "NOT SHOWN" in (entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text)]
    assert len(captions) == 1
    text = captions[0].text if captions[0].dxftype() == "MTEXT" else captions[0].dxf.text
    assert "\\P" in text
    caption = _paper_box(captions[0])
    assert caption[0] - frame >= 317.98 - 0.5
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1:
            continue
        view = (vp.dxf.center.x - vp.dxf.width / 2, vp.dxf.center.y - vp.dxf.height / 2,
                vp.dxf.center.x + vp.dxf.width / 2, vp.dxf.center.y + vp.dxf.height / 2)
        assert not _boxes_overlap(caption, view), (caption, view)
    for entity in psp.query("TEXT MTEXT"):
        if entity is captions[0]:
            continue
        other = _paper_box(entity)
        assert other and not _boxes_overlap(caption, other), (text, caption, other)


def test_tight_sheet_omitted_caption_misses_views_and_text(tmp_path):
    """Plan and section leave a short strip. The omitted detail is named in that strip."""
    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "origin_on_sheet": [10, 181],
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [3000, 700]]}],
        "views": [
            {"id": "section-a", "title": {"en": "SECTION A-A", "he": "חתך א-א"}, "scale": 10,
             "origin_on_sheet": [10, 98],
             "elements": [{"id": "wall", "type": "rect", "status": "new",
                           "corners": [[0, 0], [3000, 600]],
                           "label": "TRENCH DRAIN – MUFLE WING", "label_at": [1500, 300],
                           "label_pinned": True}]},
            {"id": "detail-1", "title": "DETAIL 1", "scale": 5,
             "elements": [{"id": "big", "type": "rect", "status": "new",
                           "corners": [[0, 0], [5000, 4000]]}]},
        ],
    }
    res = render(spec, tmp_path, "tight_caption", formats=("dxf",))
    detail_errors = [w for w in res["warnings"] if w.startswith("ERROR:") and "view 'detail-1'" in w]
    assert len(detail_errors) == 1, res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    psp = doc.layouts.get("SHEET")
    captions = [entity for entity in psp.query("TEXT MTEXT")
                if "DETAIL-1 NOT SHOWN" in (entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text)]
    assert len(captions) == 1
    caption = _paper_box(captions[0])
    viewports = []
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1:
            continue
        view = (vp.dxf.center.x - vp.dxf.width / 2, vp.dxf.center.y - vp.dxf.height / 2,
                vp.dxf.center.x + vp.dxf.width / 2, vp.dxf.center.y + vp.dxf.height / 2)
        viewports.append(view)
        assert not _boxes_overlap(caption, view), (caption, view)
    lower = min(view[3] for view in viewports)
    upper = max(view[1] for view in viewports)
    assert lower <= caption[1] and caption[3] <= upper, (caption, viewports)
    for entity in psp.query("TEXT MTEXT"):
        if entity is captions[0]:
            continue
        other = _paper_box(entity)
        assert other and not _boxes_overlap(caption, other)
    assert any("TRENCH DRAIN" in (entity.dxf.text if entity.dxftype() == "TEXT" else entity.text)
               for entity in doc.modelspace().query("TEXT MTEXT"))


def dense_drainage_plan() -> dict:
    path = Path(__file__).parent / "fixtures" / "dense_drainage_plan.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_dense_drainage_fixture(tmp_path):
    from sketch2cad.drawing.place import (
        dimension_text_gap_mm, leader_crosses_text, leader_crossings, overlapping_text,
    )
    from sketch2cad.drawing.place import _entity_box, _overlaps

    spec = dense_drainage_plan()
    still = ezdxf.readfile(render(spec, tmp_path, "dense_still", formats=("dxf",), place_labels=False)["dxf"])
    res = render(spec, tmp_path, "dense_fix", formats=("dxf", "png"))
    assert not any("not drawn off the sheet" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    msp = doc.modelspace()
    assert overlapping_text(msp) == []
    assert leader_crosses_text(msp) == []
    assert leader_crossings(msp) == []
    assert _outside_frame(doc, tol_mm=0.6) == []
    gaps = dimension_text_gap_mm(msp, 400)
    assert gaps and max(gaps) <= 3.0
    assert _inserts_of(doc, "INV. 225.00") == [(17500, 54500)]
    assert _inserts_of(doc, "INV. 224.80") == [(76800, -2500)]
    titles = {}
    for entity in msp.query("TEXT MTEXT"):
        if entity.dxf.layer != "FRAME":
            continue
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        if "DRAINAGE" in text or "ניקוז כללית" in text:
            titles[text] = _entity_box(entity)
    assert len(titles) >= 2
    boxes = list(titles.values())
    assert not _overlaps(boxes[0], boxes[1], 400)          # 1 mm of paper between the two titles
    missing, moves = _paired_moves(_text_centers(still), _text_centers(doc))
    assert missing == [], missing
    assert moves and max(moves) <= 30 * 400               # 30 mm on paper, from the unnudged label
    assert res["png"].stat().st_size > 1000


def test_dimension_text_only_slides_or_flips(tmp_path):
    from sketch2cad.drawing.place import dimension_text_gap_mm, overlapping_text

    spec = {
        "units": "mm", "scale": 1, "language": "en", "paper": "A3",
        "elements": [{"id": "inv", "type": "text", "at": [40, 2], "height": 2.5,
                      "text": {"en": "INV. 225.00"}}],
        "dimensions": [{"type": "chain", "points": [[0, 0], [120, 0]], "axis": "h", "base": 0}],
    }
    doc = ezdxf.readfile(render(spec, tmp_path, "dimslide", formats=("dxf",))["dxf"])
    msp = doc.modelspace()
    assert _inserts_of(doc, "INV. 225.00") == [(40, 2)]
    assert overlapping_text(msp) == []
    gaps = dimension_text_gap_mm(msp, 1)
    assert gaps and max(gaps) <= 3.0


def test_scale_advice_uses_a_smaller_scale(tmp_path):
    spec = {
        "units": "mm", "scale": 100, "paper": "A3", "language": "en", "view": "plan",
        "elements": [{"id": "r", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [30000, 22000]]}],
        "views": [{"id": "side", "title": "SIDE", "scale": 100,
                   "elements": [{"id": "s", "type": "rect", "status": "new",
                                 "corners": [[0, 0], [15000, 3000]]}]}],
    }
    res = render(spec, tmp_path, "advice", formats=("dxf",))
    warning = next(w for w in res["warnings"] if "side" in w and "Consider 1:" in w)
    assert "free" in warning, warning
    suggested = int(warning.split("Consider 1:")[1].split()[0])
    assert suggested in {50, 100, 125, 200, 250, 500, 1000}, warning
    assert suggested >= 100, warning


def test_dense_drainage_multiview_fits(tmp_path):
    spec = dense_drainage_plan()
    spec["views"] = [
        {"id": "section-a", "title": {"en": "SECTION A-A", "he": "חתך א-א"}, "scale": 50,
         "elements": [
             {"id": "ground", "type": "polyline", "status": "existing",
              "points": [[0, 0], [2000, 0]]},
             {"id": "wall", "type": "rect", "status": "new", "corners": [[400, 0], [1600, 800]]},
         ]},
        {"id": "detail-1", "title": {"en": "DETAIL 1", "he": "פרט 1"}, "scale": 25,
         "elements": [
             {"id": "box", "type": "rect", "status": "new", "corners": [[0, 0], [800, 500]]},
         ]},
    ]
    res = render(spec, tmp_path, "dense_sheet", formats=("dxf", "png"))
    assert not any("not shown on this sheet" in w or "not placed off the sheet" in w
                   for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    assert _viewport_scales(doc, "mm") == [25.0, 50.0, 400.0]
    psp = doc.layouts.get("SHEET")
    titles = []
    for entity in psp.query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        if any(part in text for part in ("PLAN /", "SECTION A-A", "DETAIL 1")):
            titles.append(text)
            assert "\\P" not in text and "\n" not in text
    assert any("PLAN / תוכנית" in text for text in titles)
    viewports = [vp for vp in psp.query("VIEWPORT") if vp.dxf.status > 1]
    assert len(viewports) == 3
    for vp in viewports:
        assert 10 <= vp.dxf.center.x - vp.dxf.width / 2
        assert vp.dxf.center.x + vp.dxf.width / 2 <= 410
        assert 10 <= vp.dxf.center.y - vp.dxf.height / 2
        assert vp.dxf.center.y + vp.dxf.height / 2 <= 287


def test_explicit_text_height_is_kept(tmp_path):
    from sketch2cad.drawing.standards import apply_text_style

    snapped = apply_text_style({"text_height": 2.0, "dim_text_height": 2.0}, explicit={"text_height"})
    assert snapped["text_height"] == 2.0
    assert snapped["dim_text_height"] == 2.5
    spec = {
        "units": "mm", "scale": 10, "language": "en",
        "elements": [
            {"id": "inv", "type": "text", "at": [10, 10], "height": 2.0, "text": "INV. 225.00"},
            {"id": "note", "type": "text", "at": [10, 40], "text": "NOTE"},
            {"id": "tag", "type": "circle", "at": [80, 10], "radius": 2,
             "label": "TAG", "label_height": 2.0},
        ],
    }
    doc = ezdxf.readfile(render(spec, tmp_path, "heights", formats=("dxf",))["dxf"])
    k = 10
    heights = {}
    for entity in doc.modelspace().query("TEXT MTEXT"):
        text = entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text
        height = entity.dxf.char_height if entity.dxftype() == "MTEXT" else entity.dxf.height
        if "INV" in text:
            heights["inv"] = height
        elif "NOTE" in text:
            heights["note"] = height
        elif "TAG" in text:
            heights["tag"] = height
    assert heights["inv"] == pytest.approx(2.0 * k)
    assert heights["note"] == pytest.approx(2.5 * k)
    assert heights["tag"] == pytest.approx(2.0 * k)


def test_blocked_label_stays_and_is_drawn(tmp_path):
    lines = [{"id": f"h{i}", "type": "polyline", "status": "existing",
              "points": [[0, y], [90, y]]} for i, y in enumerate(range(0, 91, 2))]
    spec = {"units": "mm", "scale": 1, "language": "en", "elements": [
        {"id": "mark", "type": "circle", "at": [0, 0], "radius": 1,
         "label": "CN1A", "label_at": [40, 40]},
        *lines,
    ]}
    res = render(spec, tmp_path, "blocked", formats=("dxf",))
    doc = ezdxf.readfile(res["dxf"])
    assert _inserts_of(doc, "CN1A") == [(40, 40)]
    assert any(w.startswith("WARNING:") and "mark" in w for w in res["warnings"]), res["warnings"]
    assert not any(w.startswith("ERROR:") and "mark" in w for w in res["warnings"])


def test_authored_leader_is_kept(tmp_path):
    """A leader the spec asked for is drawn, even when it is longer than the 25 mm search cap."""
    spec = {"units": "mm", "scale": 1, "language": "en", "elements": [
        {"id": "p", "type": "circle", "at": [0, 0], "radius": 1,
         "label": "MP2", "label_at": [80, 0], "leader": True},
    ]}
    res = render(spec, tmp_path, "leader", formats=("dxf",))
    doc = ezdxf.readfile(res["dxf"])
    assert _inserts_of(doc, "MP2") == [(80, 0)]
    lengths = []
    for entity in doc.modelspace().query("LINE"):
        if entity.dxf.layer != "TEXT":
            continue
        start, end = entity.dxf.start, entity.dxf.end
        lengths.append(((end.x - start.x) ** 2 + (end.y - start.y) ** 2) ** 0.5)
    assert lengths and max(lengths) > 25


def test_nts_fit_warning_quotes_space_and_no_scale(tmp_path):
    spec = {"units": "mm", "scale": 100, "paper": "A3", "language": "en", "view": "plan",
            "elements": [{"id": "r", "type": "rect", "status": "existing",
                          "corners": [[0, 0], [4000, 3000]]}],
            "views": [{"id": "scrap", "title": "SCRAP", "scale": "NTS", "fit_mm": 500,
                       "elements": [{"id": "c", "type": "circle", "status": "new",
                                     "at": [0, 0], "radius": 40}]}]}
    res = render(spec, tmp_path, "ntsbig", formats=("dxf",))
    # The requested 500 mm does not fit, and the view is fitted into the free rectangle.
    assert not any("Consider 1:" in w or "not shown on this sheet" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    viewports = [vp for vp in doc.layouts.get("SHEET").query("VIEWPORT") if vp.dxf.status > 1]
    assert len(viewports) == 2


def test_auto_scale_views_stay_inside_and_off_the_title_block(tmp_path):
    from ezdxf import bbox as ezbbox

    spec = dense_drainage_plan()
    spec["views"] = [
        {"id": "section-a", "title": {"en": "SECTION A-A", "he": "חתך א-א"}, "scale": "auto",
         "elements": [
             {"id": "ground", "type": "polyline", "status": "existing", "points": [[0, 0], [2000, 0]]},
             {"id": "wall", "type": "rect", "status": "new", "corners": [[400, 0], [1600, 800]],
              "label": {"en": "WALL", "he": "קיר"}},
         ],
         "dimensions": [{"type": "chain", "points": [[400, 0], [1600, 0]], "axis": "h", "base": -400}]},
        {"id": "detail-1", "title": {"en": "DETAIL 1", "he": "פרט 1"}, "scale": "auto",
         "elements": [
             {"id": "box", "type": "rect", "status": "new", "corners": [[0, 0], [800, 500]],
              "label": {"en": "GRATE", "he": "רשת"}},
         ]},
    ]
    res = render(spec, tmp_path, "auto", formats=("dxf",))
    assert not any("not shown on this sheet" in w or "not placed off the sheet" in w
                   or "nothing to draw" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    series = {50, 100, 125, 200, 250, 500, 1000, 400}
    scales = _viewport_scales(doc, "mm")
    assert scales and all(scale in series for scale in scales), scales
    assert 400 in scales
    psp = doc.layouts.get("SHEET")
    title_block = (10, 10, 410, 34)
    windows = []
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1 or vp.dxf.height <= 0:
            continue
        left = vp.dxf.center.x - vp.dxf.width / 2
        right = vp.dxf.center.x + vp.dxf.width / 2
        bottom = vp.dxf.center.y - vp.dxf.height / 2
        top = vp.dxf.center.y + vp.dxf.height / 2
        assert right <= title_block[0] or left >= title_block[2] or top <= title_block[1] or bottom >= title_block[3]
        center = vp.dxf.view_center_point
        cx, cy = center[0], center[1]
        half_h = vp.dxf.view_height / 2
        half_w = half_h * (vp.dxf.width / vp.dxf.height)
        windows.append((cx - half_w, cy - half_h, cx + half_w, cy + half_h))
    assert len(windows) == 3
    outside = []
    for entity in doc.modelspace():
        ext = ezbbox.extents([entity], fast=True)
        if not ext.has_data:
            continue
        box = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
        if not any(box[0] >= w[0] - 1 and box[1] >= w[1] - 1 and box[2] <= w[2] + 1 and box[3] <= w[3] + 1
                   for w in windows):
            outside.append(entity.dxftype())
    assert outside == [], outside[:8]
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in doc.modelspace().query("TEXT MTEXT")]
    assert any("WALL" in text for text in texts) and any("GRATE" in text for text in texts)


def _overlap_count(doc) -> int:
    from sketch2cad.drawing.place import overlapping_text
    return len(overlapping_text(doc.modelspace()))


def _qa_text_codes(spec, place_labels: bool) -> list[str]:
    from sketch2cad.qa import _geometry, build_scene, resolve_qa
    scene = build_scene(spec, place_labels=place_labels)
    return [finding.code for finding in _geometry(scene, resolve_qa(spec))
            if finding.code in ("text_overlap", "text_over_line")]


def _leader_to(doc, target, tol: float = 1.5) -> bool:
    for entity in doc.modelspace().query("LINE"):
        if entity.dxf.layer != "TEXT":
            continue
        for point in (entity.dxf.start, entity.dxf.end):
            if abs(point.x - target[0]) <= tol and abs(point.y - target[1]) <= tol:
                return True
    return False


def test_placement_adds_no_overlaps_on_the_dense_fixture(tmp_path):
    spec = dense_drainage_plan()
    off = ezdxf.readfile(render(spec, tmp_path, "off", formats=("dxf",), place_labels=False)["dxf"])
    on = ezdxf.readfile(render(spec, tmp_path, "on", formats=("dxf",))["dxf"])
    assert _overlap_count(on) <= _overlap_count(off)
    off_codes = _qa_text_codes(spec, False)
    on_codes = _qa_text_codes(spec, True)
    assert on_codes.count("text_overlap") <= off_codes.count("text_overlap")
    assert on_codes.count("text_over_line") <= off_codes.count("text_over_line")
    assert "td_ne" in {box_eid for box_eid in _qa_eids(spec)}


def _qa_eids(spec) -> set[str]:
    from sketch2cad.qa import build_scene
    return {box.eid for box in build_scene(spec).boxes}


def test_side_column_notes_do_not_cover_leader_labels(tmp_path):
    """Notes take the side column. Leader labels near that edge keep their leaders and add no overlaps."""
    spec = {
        "units": "mm", "scale": 1, "paper": "A3", "language": "en", "view": "detail",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": ["Keep the trench clear.", "Inverts as shown.", "See the detail."]},
        "elements": [
            {"id": "body", "type": "rect", "status": "existing", "corners": [[0, 0], [220, 200]]},
            {"id": "d_td", "type": "circle", "at": [190, 170], "radius": 3,
             "label": "D_TD", "label_at": [230, 180], "leader": True},
            {"id": "d_conn", "type": "circle", "at": [190, 140], "radius": 3,
             "label": "D_CONN", "label_at": [230, 145], "leader": True},
        ],
    }
    off = ezdxf.readfile(render(spec, tmp_path, "side_off", formats=("dxf",), place_labels=False)["dxf"])
    res = render(spec, tmp_path, "side_on", formats=("dxf",))
    on = ezdxf.readfile(res["dxf"])
    assert _overlap_count(on) <= _overlap_count(off)
    assert _leader_to(on, (190, 170)) and _leader_to(on, (190, 140))
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in on.modelspace().query("TEXT MTEXT")]
    assert any("D_TD" in text for text in texts) and any("trench" in text for text in texts)
    off_codes = _qa_text_codes(spec, False)
    on_codes = _qa_text_codes(spec, True)
    assert on_codes.count("text_overlap") <= off_codes.count("text_overlap")
    assert on_codes.count("text_over_line") <= off_codes.count("text_over_line")


def test_sheet_scale_auto_renders(tmp_path):
    spec = {"units": "mm", "scale": "auto", "paper": "A3", "language": "en", "view": "plan",
            "sheet": {"template": "a3_simple", "fields": {"title_en": "AUTO"}},
            "elements": [{"id": "r", "type": "rect", "status": "existing",
                          "corners": [[0, 0], [5000, 3000]]}]}
    res = render(spec, tmp_path, "sheet_auto", formats=("dxf",))
    assert not any("could not convert" in w or "not a positive number" in w for w in res["warnings"])
    doc = ezdxf.readfile(res["dxf"])
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in doc.modelspace().query("TEXT MTEXT")]
    # 5000 x 3000 mm on the A3 drawing area fits at 1:15 and not at 1:10.
    assert any("1:15" in text for text in texts), texts
    assert not any("1:1000" in text or "1:50" in text for text in texts), texts


def _sheet_texts(doc) -> list[str]:
    return [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in doc.modelspace().query("TEXT MTEXT")]


def test_notes_are_never_dropped(tmp_path):
    """A notes block that does not fit at 2.5 mm is redrawn smaller, and every sentence is on the sheet."""
    en = [f"English note {i} about the trench cover." for i in range(1, 13)]
    he = [f"הערה {i} על הכיסוי." for i in range(1, 13)]
    spec = {
        "units": "mm", "scale": 1, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": en, "he": he},
        "elements": [{"id": "pad", "type": "rect", "status": "existing", "corners": [[0, 0], [300, 150]]}],
    }
    res = render(spec, tmp_path, "notes", formats=("dxf",))
    assert not any("not drawn" in w for w in res["warnings"]), res["warnings"]
    blob = "\n".join(_sheet_texts(ezdxf.readfile(res["dxf"]))).replace("\\P", " ")
    for line in en + he:
        assert line in blob, line
    heights = [e.dxf.char_height for e in ezdxf.readfile(res["dxf"]).modelspace().query("MTEXT")
               if "NOTES" in e.text or "הערות" in e.text]
    assert heights and max(heights) <= 2.0 + 1e-6


def test_two_column_notes_when_a_single_column_does_not_fit(tmp_path):
    en = [f"English column note {i} stays readable." for i in range(1, 16)]
    he = [f"טור עברי {i} נשאר קריא." for i in range(1, 16)]
    spec = {
        "units": "mm", "scale": 1, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": en, "he": he},
        "elements": [{"id": "pad", "type": "rect", "status": "existing", "corners": [[0, 0], [300, 180]]}],
    }
    res = render(spec, tmp_path, "cols", formats=("dxf",))
    assert not any("not drawn" in w for w in res["warnings"]), res["warnings"]
    notes = [e for e in ezdxf.readfile(res["dxf"]).modelspace().query("MTEXT")
             if "NOTES" in e.text or "הערות" in e.text]
    assert len(notes) == 2
    tops = sorted(e.dxf.insert.y for e in notes)
    assert tops[1] - tops[0] < 5.0


def test_pinned_label_does_not_sit_on_dimension_text(tmp_path):
    from sketch2cad.drawing.place import overlapping_text

    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [{
            "id": "mp1", "type": "pipe", "status": "new", "od": 400,
            "points": [[0, 0], [6000, 0]],
            "label": "MAIN DRAIN", "label_at": [3000, 280], "label_pinned": True,
        }],
        "dimensions": [{"type": "chain", "axis": "h", "points": [[0, 0], [6000, 0]], "base": 200}],
    }
    res = render(spec, tmp_path, "dimlab", formats=("dxf",))
    doc = ezdxf.readfile(res["dxf"])
    assert overlapping_text(doc.modelspace()) == []
    label = next(e for e in doc.modelspace().query("TEXT MTEXT") if "MAIN DRAIN" in (
        e.text if e.dxftype() == "MTEXT" else e.dxf.text))
    point = label.dxf.insert
    assert point.x == pytest.approx(3000, abs=1)
    assert point.y == pytest.approx(280, abs=1)


def test_callout_below_the_detail_stays_inside_the_frame(tmp_path):
    spec = {
        "units": "mm", "scale": 1, "paper": "A3", "language": "en", "view": "detail",
        "sheet": {"template": "a3_simple"},
        "elements": [
            {"id": "body", "type": "rect", "status": "existing", "corners": [[0, 0], [220, 100]]},
            {"id": "d_inv_t", "type": "text", "at": [110, -108], "height": 2.5, "text": "INV 225.00"},
        ],
    }
    doc = ezdxf.readfile(render(spec, tmp_path, "inv", formats=("dxf",))["dxf"])
    fx0, fy0, _fx1, _fy1 = _frame_limits(doc)
    text = next(e for e in doc.modelspace().query("TEXT MTEXT") if "INV 225" in (
        e.text if e.dxftype() == "MTEXT" else e.dxf.text))
    from ezdxf import bbox as ezbbox
    box = ezbbox.extents([text], fast=True)
    assert box.extmin.y - fy0 > 8.0


def test_ground_and_explicit_lineweight_are_kept(tmp_path):
    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "en", "view": "section",
        "sheet": {"template": "a3_simple"},
        "elements": [
            {"id": "sec_grade", "type": "polyline", "status": "existing", "style": "ground",
             "points": [[0, 0], [4000, 0]]},
            {"id": "heavy", "type": "polyline", "status": "new", "lineweight": 0.70,
             "points": [[0, 800], [4000, 800]]},
        ],
    }
    doc = ezdxf.readfile(render(spec, tmp_path, "grade", formats=("dxf",))["dxf"])
    weights = {}
    for poly in doc.modelspace().query("LWPOLYLINE"):
        ys = [p[1] for p in poly.get_points()]
        if not ys:
            continue
        weights[round(sum(ys) / len(ys), 3)] = poly.dxf.lineweight
    assert weights[0] == 50
    assert weights[800] == 70


def _denominators(text: str) -> list[int]:
    found = []
    for part in text.split("1:"):
        digits = []
        for ch in part:
            if ch.isdigit():
                digits.append(ch)
            else:
                break
        if digits:
            found.append(int("".join(digits)))
    return found


def test_auto_scale_is_the_largest_that_fits_the_drawn_sheet(tmp_path):
    def title_scale(name, spec):
        doc = ezdxf.readfile(render(spec, tmp_path, name, formats=("dxf",))["dxf"])
        return " ".join(_sheet_texts(doc))

    detail = {
        "units": "m", "scale": "auto", "paper": "A3", "language": "en", "view": "detail",
        "sheet": {"template": "a3_simple", "fields": {"title_en": "DETAIL"}},
        "elements": [{"id": "fit", "type": "rect", "status": "existing", "corners": [[0, 0], [4, 2]]}],
    }
    shown = title_scale("detail_auto", detail)
    assert "1:15" in shown
    # The dimension line is part of the drawn sheet, so a base far from the detail coarsens the scale.
    far = {**detail, "dimensions": [
        {"type": "chain", "axis": "h", "points": [[0, 0], [4, 0]], "base": 500}]}
    far_denoms = _denominators(title_scale("detail_far", far))
    assert far_denoms and min(far_denoms) > 15
    site = {**detail, "view": "plan", "elements": [
        {"id": "site", "type": "rect", "status": "existing", "corners": [[0, 0], [200, 150]]}]}
    site_text = title_scale("site_auto", site)
    assert "1:750" in site_text
    assert "1:15" not in site_text


def test_sheet_auto_fits_notes_labels_and_dimension_text(tmp_path):
    """Geometry that fits at 1:50 does not, once the notes block and a label are on the sheet."""
    rect = {"id": "pad", "type": "rect", "status": "existing", "corners": [[0, 0], [18500, 11500]]}
    bare = {
        "units": "mm", "scale": "auto", "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [rect],
    }
    notes = {
        "en": [f"English note {i} about the cover." for i in range(1, 9)],
        "he": [f"הערה {i} על הכיסוי." for i in range(1, 9)],
    }
    full = {
        **bare,
        "notes": notes,
        "elements": [
            rect,
            {"id": "call", "type": "text", "at": [9000, 12000], "height": 2.5, "text": "MAIN PLAN"},
        ],
    }
    bare_denoms = _denominators(" ".join(_sheet_texts(
        ezdxf.readfile(render(bare, tmp_path, "bare", formats=("dxf",))["dxf"]))))
    res = render(full, tmp_path, "noted", formats=("dxf",))
    assert not any("does not fit" in w for w in res["warnings"]), res["warnings"]
    full_denoms = _denominators(" ".join(_sheet_texts(ezdxf.readfile(res["dxf"]))))
    assert bare_denoms and full_denoms
    assert min(bare_denoms) == 50
    assert min(full_denoms) > min(bare_denoms)
    blob = "\n".join(_sheet_texts(ezdxf.readfile(res["dxf"]))).replace("\\P", " ")
    assert "MAIN PLAN" in blob and "English note 1" in blob and "הערה 1" in blob


def _sheet_windows(doc):
    """(scale denominator, model window) for every real viewport on the sheet."""
    psp = doc.layouts.get("SHEET")
    found = []
    for vp in psp.query("VIEWPORT"):
        if vp.dxf.status <= 1 or vp.dxf.height <= 0:
            continue
        center = vp.dxf.view_center_point
        half_h = vp.dxf.view_height / 2
        half_w = half_h * (vp.dxf.width / vp.dxf.height)
        window = (center[0] - half_w, center[1] - half_h, center[0] + half_w, center[1] + half_h)
        found.append(((vp.dxf.view_height / vp.dxf.height) / 0.001, window))
    return found


def _outside_viewports(doc, windows) -> list[str]:
    from ezdxf import bbox as ezbbox

    outside = []
    for entity in doc.modelspace():
        ext = ezbbox.extents([entity], fast=True)
        if not ext.has_data:
            continue
        box = (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
        if not any(box[0] >= w[0] - 1 and box[1] >= w[1] - 1 and box[2] <= w[2] + 1 and box[3] <= w[3] + 1
                   for _scale, w in windows):
            outside.append(entity.dxftype())
    return outside


def _side_views():
    """A short section and a few-metre detail. The far callout and fit_mm must not set the scale."""
    return [
        {"id": "section-a", "title": {"en": "SECTION A-A", "he": "חתך א-א"}, "scale": "auto",
         "elements": [{"id": "ground", "type": "polyline", "status": "existing",
                       "points": [[0, 0], [2, 0]]}]},
        {"id": "detail-1", "title": {"en": "DETAIL 1", "he": "פרט 1"}, "scale": "auto", "fit_mm": 400,
         "elements": [
             {"id": "box", "type": "rect", "status": "new", "corners": [[0, 0], [2.4, 1.2]]},
             {"id": "far", "type": "text", "at": [120, 40], "text": "SITE NOTE"},
         ]},
    ]


def _assert_standard_scales(doc, units: str) -> list[float]:
    from sketch2cad.drawing.layout import STANDARD_SCALES

    scales = _viewport_scales(doc, units)
    assert scales and all(scale in STANDARD_SCALES for scale in scales), scales
    return scales


def _sheet_blob(doc) -> str:
    layouts = [doc.modelspace(), doc.layouts.get("SHEET")]
    chunks = []
    for layout in layouts:
        for entity in layout.query("TEXT MTEXT"):
            chunks.append(entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text)
    return "\n".join(chunks).replace("\\P", " ")


def test_auto_multiview_places_every_view(tmp_path):
    """The plan is scaled into the space the side views' readable floors leave, and every view is placed."""
    from ezdxf import bbox as ezbbox
    from sketch2cad.drawing.place import overlapping_text

    spec = {
        "units": "m", "scale": "auto", "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": ["Keep the trench clear."], "he": ["השאר את התעלה נקייה."]},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [90, 50]]}],
        "views": _side_views(),
    }
    res = render(spec, tmp_path, "mv_auto", formats=("dxf",))
    assert not any("not placed" in w or "not shown" in w or "does not fit" in w
                   or "would be 1:" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    windows = _sheet_windows(doc)
    assert len(windows) >= 3, windows
    primary = next(scale for scale, window in windows
                   if window[0] <= 45 <= window[2] and window[1] <= 25 <= window[3])
    sides = [scale for scale, window in windows
             if not (window[0] <= 45 <= window[2] and window[1] <= 25 <= window[3])]
    assert primary <= 500, windows
    assert sides and max(sides) <= 100 and min(sides) <= 50, windows
    assert overlapping_text(doc.modelspace()) == []
    assert _outside_viewports(doc, windows) == []
    assert _assert_standard_scales(doc, "m")
    blob = _sheet_blob(doc)
    assert "Keep the trench clear." in blob and "השאר את התעלה נקייה." in blob
    assert "no paper position" not in " ".join(res["warnings"])
    texts = [e.text if e.dxftype() == "MTEXT" else e.dxf.text for e in doc.modelspace().query("TEXT MTEXT")]
    assert not any("SITE NOTE" in text for text in texts)
    psp = doc.layouts.get("SHEET")
    for entity in psp.query("MTEXT"):
        if "תוכנית" not in (entity.text or ""):
            continue
        box = ezbbox.extents([entity], fast=True)
        assert box.extmax.y <= 287 + 0.5


def test_fixed_plan_auto_views_are_placed(tmp_path):
    """A numeric plan leaves room, and both auto views are placed at a readable scale."""
    from sketch2cad.drawing.place import overlapping_text

    spec = {
        "units": "m", "scale": 500, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": ["Keep the trench clear."], "he": ["השאר את התעלה נקייה."]},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [80, 40]]}],
        "views": _side_views(),
    }
    res = render(spec, tmp_path, "mv_500", formats=("dxf",))
    assert not any("not placed" in w or "does not fit" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    windows = _sheet_windows(doc)
    assert len(windows) >= 3, windows
    primary = next(scale for scale, window in windows
                   if window[0] <= 40 <= window[2] and window[1] <= 20 <= window[3])
    sides = [scale for scale, window in windows
             if not (window[0] <= 40 <= window[2] and window[1] <= 20 <= window[3])]
    assert primary == pytest.approx(500, abs=1), windows
    assert sides and max(sides) <= 100 and min(sides) <= 50, windows
    assert _assert_standard_scales(doc, "m")
    blob = _sheet_blob(doc)
    assert "Keep the trench clear." in blob and "השאר את התעלה נקייה." in blob
    assert overlapping_text(doc.modelspace()) == []
    assert _outside_viewports(doc, windows) == []


def test_multiview_keeps_notes_and_fixed_scales(tmp_path):
    """A fixed 1:50 and 1:25 stay exactly those scales, and the notes stay on the sheet."""
    from sketch2cad.qa import review

    spec = {
        "units": "m", "scale": 400, "paper": "A3", "language": "both", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "notes": {"en": ["Keep the trench clear."], "he": ["השאר את התעלה נקייה."]},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [40, 20]]}],
        "views": [
            {"id": "section-a", "title": "SECTION A-A", "scale": 50,
             "elements": [{"id": "ground", "type": "polyline", "status": "existing",
                           "points": [[0, 0], [2, 0]]}]},
            {"id": "detail-1", "title": "DETAIL 1", "scale": 25,
             "elements": [{"id": "box", "type": "rect", "status": "new",
                           "corners": [[0, 0], [1, 0.6]]}]},
        ],
    }
    res = render(spec, tmp_path, "mv_fixed", formats=("dxf",))
    assert not any("Omitted" in w or "no paper position" in w for w in res["warnings"]), res["warnings"]
    doc = ezdxf.readfile(res["dxf"])
    assert _viewport_scales(doc, "m") == [25.0, 50.0, 400.0]
    blob = _sheet_blob(doc)
    assert "Keep the trench clear." in blob and "השאר את התעלה נקייה." in blob
    findings = review(spec).findings
    assert not any(finding.code == "omitted_strings" for finding in findings)
    assert not any(finding.code == "omitted_view" for finding in findings)


def test_auto_plan_placed_at_its_floor_does_not_warn(tmp_path):
    """The probe may want 1:1000, but a plan that is placed at 1:500 must not say so."""
    spec = {
        "units": "m", "scale": "auto", "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [100, 60]]}],
        "views": [
            {"id": "section-a", "title": "SECTION A-A", "scale": "auto",
             "elements": [{"id": "ground", "type": "polyline", "status": "existing",
                           "points": [[0, 0], [8, 0]]}]},
            {"id": "detail-1", "title": "DETAIL 1", "scale": "auto",
             "elements": [{"id": "box", "type": "rect", "status": "new",
                           "corners": [[0, 0], [4, 3]]}]},
        ],
    }
    res = render(spec, tmp_path, "floor", formats=("dxf",))
    assert not any("would be 1:" in w for w in res["warnings"]), res["warnings"]
    scales = _viewport_scales(ezdxf.readfile(res["dxf"]), "m")
    assert 500.0 in scales, scales


def test_unplaced_plan_does_not_claim_the_probe_scale(tmp_path):
    """A plan that is not placed is named once. It is not also 'would be 1:2000'."""
    spec = {
        "units": "m", "scale": "auto", "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [200, 150]]}],
        "views": [
            {"id": "section-a", "title": "SECTION A-A", "scale": "auto",
             "elements": [{"id": "ground", "type": "polyline", "status": "existing",
                           "points": [[0, 0], [8, 0]]}]},
            {"id": "detail-1", "title": "DETAIL 1", "scale": "auto",
             "elements": [{"id": "box", "type": "rect", "status": "new",
                           "corners": [[0, 0], [4, 3]]}]},
        ],
    }
    res = render(spec, tmp_path, "probe", formats=("dxf",))
    primary = [w for w in res["warnings"] if w.startswith("ERROR:") and "view 'primary'" in w]
    assert len(primary) == 1, res["warnings"]
    assert "not shown on this sheet" in primary[0]
    assert not any("would be 1:" in w for w in res["warnings"]), res["warnings"]
    blob = _sheet_blob(ezdxf.readfile(res["dxf"]))
    assert "PRIMARY NOT SHOWN – NEEDS A2 SHEET" in blob


def test_placed_plan_coarser_than_its_floor_warns_once(tmp_path):
    spec = {
        "units": "m", "scale": 2000, "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [{"id": "site", "type": "rect", "status": "existing",
                      "corners": [[0, 0], [10, 6]]}],
        "views": [{"id": "detail-1", "title": "DETAIL 1", "scale": 50,
                   "elements": [{"id": "box", "type": "rect", "status": "new",
                                 "corners": [[0, 0], [1, 0.6]]}]}],
    }
    res = render(spec, tmp_path, "coarse", formats=("dxf",))
    would = [w for w in res["warnings"] if "would be 1:" in w]
    assert len(would) == 1, res["warnings"]
    assert "1:2000" in would[0] and "1:500" in would[0]
    assert not any("view 'primary' is at" in w for w in res["warnings"])
    assert not any("not shown" in w for w in res["warnings"]), res["warnings"]
    assert 2000.0 in _viewport_scales(ezdxf.readfile(res["dxf"]), "m")


def test_nudged_dimension_text_is_stored_above_the_line(tmp_path):
    from ezdxf import bbox as ezbbox
    from sketch2cad.drawing.place import overlapping_text

    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple"},
        "elements": [{
            "id": "mp1", "type": "pipe", "status": "new", "od": 400,
            "points": [[0, 0], [6000, 0]],
            "label": "MAIN DRAIN", "label_at": [3000, 220], "label_pinned": True,
        }],
        "dimensions": [{"type": "chain", "axis": "h", "points": [[0, 0], [6000, 0]], "base": 200}],
    }
    doc = ezdxf.readfile(render(spec, tmp_path, "dimflag", formats=("dxf",))["dxf"])
    assert overlapping_text(doc.modelspace()) == []
    dim = next(iter(doc.modelspace().query("DIMENSION")))
    assert int(dim.dxf.dimtype) & 128
    block = dim.get_geometry_block()
    text = next(entity for entity in block if entity.dxftype() in ("TEXT", "MTEXT"))
    box = ezbbox.extents([text], fast=True)
    center_x = (box.extmin.x + box.extmax.x) / 2
    center_y = (box.extmin.y + box.extmax.y) / 2
    assert dim.dxf.text_midpoint.x == pytest.approx(center_x, abs=1)
    assert dim.dxf.text_midpoint.y == pytest.approx(center_y, abs=1)
    assert center_y > 200
