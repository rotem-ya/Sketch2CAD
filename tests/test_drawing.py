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
                assert entity.dxf.lineweight == -1, (block.name, entity.dxftype(), entity.dxf.lineweight)


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


def test_dense_labels_do_not_overlap(tmp_path):
    from sketch2cad.drawing.place import overlapping_text

    res = render(dense_plan(), tmp_path, "dense", formats=("dxf", "png"))
    doc = ezdxf.readfile(res["dxf"])
    hits = overlapping_text(doc.modelspace())
    assert hits == [], hits
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
    for fragment in ("PLAN", "1:400", "SECTION A-A", "1:50", "DETAIL 1", "1:25", "חתך"):
        assert fragment in joined
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
    texts = [e.dxf.text for e in doc.layouts.get("SHEET").query("TEXT")]
    assert "NTS" in texts and "SCRAP" in texts
    scales = _viewport_scales(doc, "mm")
    assert 100 in [round(s) for s in scales]
    assert any(s != 100 for s in scales)


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
    assert parse_reply('{"scale": 20, "elements": []}')["scale"] == 20
