"""Drawing QA: every check has a case that flags and a case that does not."""
import json
from pathlib import Path

import ezdxf
import pytest

from sketch2cad import checks
from sketch2cad.catalog import load_catalog
from sketch2cad.projects import Project, create_project
from sketch2cad.qa import (MAX_MARKER_MM, QA_DEFAULTS, SHEET_CODES, overlay_plan, review,
                           submission_from_doc, write_overlay)
from sketch2cad.spec import normalize_spec


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))


def _spec(elements=None, **extra):
    spec = {
        "units": "mm", "scale": 10, "paper": "A3", "language": "en", "view": "plan",
        "sheet": {"template": "a3_simple", "origin": [0, 0]},
        "elements": elements or [],
        # Wide line-weight window so the engine's own layers are not the subject of these cases.
        "qa": {"lineweight_min_mm": 0.05, "lineweight_max_mm": 2.0, "thick_thin_ratio": 1.0,
               "utility_submission": False},
    }
    spec.update(extra)
    return spec


def _codes(spec, catalog=None, project=None):
    return [f.code for f in checks.run_checks(spec, catalog, project)]


def _has(spec, code, catalog=None, project=None):
    return code in _codes(spec, catalog, project)


def _box(spec, eid):
    scene = review(spec).scene
    return next(b for b in scene.boxes if b.eid == eid)


# ---------------------------------------------------------------- text
def test_bilingual_label_is_two_lines_and_overlap_is_caught():
    apart = _spec([
        {"id": "t1", "type": "text", "at": [500, 1500], "height": 2.5, "text": {"en": "HELLO", "he": "שלום"}},
        {"id": "t2", "type": "text", "at": [500, 800], "height": 2.5, "text": {"en": "WORLD"}},
    ], language="both")
    both, one = _box(apart, "t1"), _box(apart, "t2")
    assert both.nlines == 2 and one.nlines == 1
    assert (both.y1 - both.y0) > (one.y1 - one.y0) * 1.5
    assert not _has(apart, "text_overlap")

    piled = _spec([
        {"id": "t1", "type": "text", "at": [500, 1500], "height": 2.5, "text": {"en": "HELLO"}},
        {"id": "t2", "type": "text", "at": [520, 1500], "height": 2.5, "text": {"en": "WORLD"}},
    ])
    assert _has(piled, "text_overlap")


def test_text_over_line_ignores_the_labels_own_leader():
    on_pipe = _spec([{
        "id": "p", "type": "pipe", "points": [[400, 1000], [3000, 1000]], "od": 40,
        "label": {"en": "PIPE LABEL"}, "label_at": [800, 1000],
    }])
    assert _has(on_pipe, "text_over_line")

    led = _spec([{
        "id": "p", "type": "pipe", "points": [[400, 1000], [3000, 1000]], "od": 40,
        "label": {"en": "PIPE LABEL"}, "label_at": [800, 1800], "leader": True,
    }])
    assert not _has(led, "text_over_line")


def test_outside_sheet_and_title_block():
    assert _has(_spec([{"id": "t", "type": "text", "at": [-2000, -2000], "height": 2.5, "text": "OUT"}]),
                "outside_sheet")
    inside = _spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 2.5, "text": "IN"}])
    assert not _has(inside, "outside_sheet") and not _has(inside, "in_titleblock")

    titled = _spec([{"id": "t", "type": "text", "at": [1000, 100], "height": 2.5, "text": "TITLE"}])
    assert _has(titled, "in_titleblock")


def test_text_height_minimum_series_and_consistency():
    tiny = _spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 1.2, "text": "tiny"}])
    assert _has(tiny, "text_too_small")
    ok = _spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 2.5, "text": "ok"}])
    assert not _has(ok, "text_too_small")

    mixed = _spec([
        {"id": "a", "type": "text", "at": [500, 1500], "height": 2.5, "text": "A"},
        {"id": "b", "type": "text", "at": [2500, 1500], "height": 5.0, "text": "B"},
    ])
    assert _has(mixed, "text_height_inconsistent")
    same = _spec([
        {"id": "a", "type": "text", "at": [500, 1500], "height": 2.5, "text": "A"},
        {"id": "b", "type": "text", "at": [2500, 1500], "height": 2.5, "text": "B"},
    ])
    assert not _has(same, "text_height_inconsistent")

    off = _spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 3.0, "text": "off"}])
    assert _has(off, "text_height_standard")
    allowed = _spec(
        [{"id": "t", "type": "text", "at": [500, 1500], "height": 2.5, "text": "on"}],
        qa={"lineweight_min_mm": 0.05, "lineweight_max_mm": 2, "thick_thin_ratio": 1,
            "utility_submission": False, "text_heights_mm": [2.2, 2.5, 3.0, 3.5, 5, 7],
            "annotation_heights_mm": [1.8, 2.0, 2.2, 3.0]},
    )
    assert not _has(allowed, "text_height_standard") and not _has(allowed, "text_too_small")


# ---------------------------------------------------------------- dimensions
def _dim(texts, view="plan", points=None):
    return _spec(dimensions=[{
        "type": "chain", "axis": "h", "base": 1800, "texts": texts,
        "points": points or [[500, 1500], [2500, 1500]],
    }], view=view)


def test_dimension_override_zero_and_duplicate():
    assert _has(_dim(["1500"]), "dim_override")
    assert not _has(_dim(["2000"]), "dim_override")
    assert not _has(_dim(["<>"]), "dim_override")
    assert not _has(_dim(["1500"], view="NTS"), "dim_override")
    assert not _has(_dim(["1500"], view="N.T.S."), "dim_override")
    assert _has(_dim(["1500"], view="section"), "dim_override")
    nts_flag = _dim(["1500"])
    nts_flag["nts"] = True
    assert not _has(nts_flag, "dim_override")
    nts_text = _dim(["1500"])
    nts_text["elements"] = [{"id": "cap", "type": "text", "at": [800, 2000], "height": 2.5, "text": "SECTION A - N.T.S."}]
    assert not _has(nts_text, "dim_override")
    nts_scale = _dim(["1500"])
    nts_scale["sheet"] = {"template": "a3_simple", "fields": {"scale": "N.T.S."}}
    assert not _has(nts_scale, "dim_override")
    nts_view = _dim(["1500"], view="section")
    nts_view["views"] = [{"name": "SECTION", "scale": "NTS"}]
    assert not _has(nts_view, "dim_override")

    assert _has(_dim([""], points=[[500, 1500], [500, 1500]]), "dim_zero")
    assert not _has(_dim(["2000"]), "dim_zero")

    twice = _spec(dimensions=[
        {"type": "chain", "axis": "h", "base": 1800, "points": [[500, 1500], [2500, 1500]]},
        {"type": "chain", "axis": "h", "base": 2100, "points": [[500, 1500], [2500, 1500]]},
    ])
    assert _has(twice, "dim_duplicate")
    different = _spec(dimensions=[
        {"type": "chain", "axis": "h", "base": 1800, "points": [[500, 1500], [2500, 1500]]},
        {"type": "chain", "axis": "v", "base": 4000, "points": [[3000, 1000], [3000, 2200]]},
    ])
    assert not _has(different, "dim_duplicate")


# ---------------------------------------------------------------- drainage and connectors
def _pipe(**extra):
    el = {"id": "p", "type": "pipe", "points": [[500, 1500], [10500, 1500]], "od": 160, "dn": 150}
    el.update(extra)
    return el


def test_slope_matches_invert_and_minimum_by_diameter():
    ends = [[500, 1500], [10500, 1500]]
    assert not _has(_spec([_pipe(slope=0.01, inv=[500, 400], inv_at=ends)]), "slope_mismatch")
    assert _has(_spec([_pipe(slope=0.01, inv=[500, 450], inv_at=ends)]), "slope_mismatch")
    whole = _pipe(slope=0.004, slope_unit="percent", dn=400, inv=[225.00, 224.84],
                  points=[[0, 0], [63160, 0]])
    assert not _has(_spec([whole]), "slope_mismatch")
    assert _has(_spec([whole]), "slope_unchecked")
    texts = _spec([
        _pipe(slope=0.01),
        {"id": "i1", "type": "text", "at": [500, 1900], "height": 2.5, "text": "INV 500", "pipe": "p"},
        {"id": "i2", "type": "text", "at": [10500, 1900], "height": 2.5, "text": "INV 450", "pipe": "p"},
    ])
    assert _has(texts, "slope_mismatch")
    texts_ok = _spec([
        _pipe(slope=0.01),
        {"id": "i1", "type": "text", "at": [500, 1900], "height": 2.5, "text": "INV 500", "pipe": "p"},
        {"id": "i2", "type": "text", "at": [10500, 1900], "height": 2.5, "text": "INV 400", "pipe": "p"},
    ])
    assert not _has(texts_ok, "slope_mismatch")

    assert _has(_spec([_pipe(slope=0.005, dn=150)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.015, dn=150)]), "min_slope")
    assert not _has(_spec([_pipe(slope=1.5, slope_unit="percent", dn=150)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.4, slope_unit="percent", dn=150)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.01, dn=200)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.005, dn=250)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.005, dn=300)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.004, dn=300)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.004, dn=400)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.003, dn=400)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.003, dn=500)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.002, dn=500)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.0025, dn=600)]), "min_slope")
    assert not _has(_spec([_pipe(slope=0.0025, dn=800)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.002, dn=800)]), "min_slope")
    bare = _spec([_pipe(dn=150)])
    assert not _has(bare, "min_slope")
    assert _has(bare, "slope_unchecked")


def test_sn_cover_and_connector_offset():
    shallow = _spec([_pipe(sn="SN8", cover=200)], view="elevation")
    assert _has(shallow, "sn_cover")
    assert not _has(_spec([_pipe(sn="SN8", cover=600)], view="elevation"), "sn_cover")
    assert not _has(_spec([_pipe(cover=200)], view="elevation"), "sn_cover")

    class _Project:
        qa = {"sn_cover_mm": {"SN8": 100}}

    assert not _has(shallow, "sn_cover", project=_Project())

    bad = _spec([{"id": "c", "type": "connector", "at": [2000, 1500], "offset": 1500,
                  "offset_from": [0, 1500], "offset_axis": "x"}])
    good = _spec([{"id": "c", "type": "connector", "at": [2000, 1500], "offset": 2000,
                   "offset_from": [0, 1500], "offset_axis": "x"}])
    assert _has(bad, "connector_offset") and not _has(good, "connector_offset")


# ---------------------------------------------------------------- hygiene and standards
def test_duplicate_ids_and_unknown_references():
    dup = _spec([
        {"id": "p", "type": "circle", "at": [500, 1500], "radius": 20},
        {"id": "p", "type": "circle", "at": [2000, 1500], "radius": 20},
    ])
    assert _has(dup, "duplicate_id")
    assert not _has(_spec([
        {"id": "a", "type": "circle", "at": [500, 1500], "radius": 20},
        {"id": "b", "type": "circle", "at": [2000, 1500], "radius": 20},
    ]), "duplicate_id")

    raw, warnings = normalize_spec({"units": "mm", "elements": [
        {"id": "a", "type": "circle", "at": [0, 0], "radius": 1},
        {"id": "a", "type": "circle", "at": [2, 0], "radius": 1},
    ]})
    assert any("duplicate" in w for w in warnings)
    assert raw["elements"][0]["id"] == "a"
    assert raw["elements"][1]["id"] == "a_1"

    assert _has(_spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 2.5, "text": "see #missing"}]),
                "unknown_ref")
    assert not _has(_spec([
        {"id": "p1", "type": "circle", "at": [500, 1500], "radius": 20},
        {"id": "t", "type": "text", "at": [2500, 1500], "height": 2.5, "text": "see #p1"},
    ]), "unknown_ref")


def test_unsourced_dims_still_reported():
    spec = _spec([{"id": "v", "type": "gate_valve", "item": "avk_0661_dn150", "at": [800, 1500]}])
    found = [f for f in checks.run_checks(spec, load_catalog()) if f.code == "unsourced_dims"]
    assert found and found[0].he and found[0].en


def test_lineweights_and_utility_submission():
    heavy = _spec(
        [{"id": "p", "type": "pipe", "points": [[500, 1500], [3000, 1500]], "od": 100}],
        qa={"lineweight_min_mm": 0.25, "lineweight_max_mm": 0.40, "thick_thin_ratio": 2,
            "utility_submission": False, "min_text_mm": 1.5,
            "text_heights_mm": [1.8, 2.0, 2.2, 2.5, 3.0, 3.5, 5, 7],
            "annotation_heights_mm": [1.8, 2.0, 2.2, 3.0]},
    )
    assert _has(heavy, "lineweight_ratio") and _has(heavy, "lineweight_range")
    calm = _spec([{"id": "p", "type": "pipe", "points": [[500, 1500], [3000, 1500]], "od": 100}])
    assert not _has(calm, "lineweight_ratio") and not _has(calm, "lineweight_range")
    buried = _spec(
        [{"id": "p", "type": "pipe", "points": [[500, 1500], [3000, 1500]], "od": 100, "below_grade": True}],
        qa={**calm["qa"], "utility_submission": True},
    )
    assert _has(buried, "not_bylayer")
    assert not _has(calm, "not_bylayer")

    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    doc.modelspace().add_circle((0, 0), 1, dxfattribs={"layer": "DEFPOINTS"})
    block = doc.blocks.new("BAD")
    block.add_line((0, 0), (1, 0), dxfattribs={"layer": "NEW"})
    doc.modelspace().add_line((0, 0), (2, 0), dxfattribs={"layer": "NEW", "color": 1, "lineweight": 25})
    flagged = {f.code for f in submission_from_doc(doc)}
    assert {"layer_0", "defpoints", "block_layer", "not_bylayer"} <= flagged
    for finding in submission_from_doc(doc):
        assert finding.severity == "warning" and finding.he and finding.en

    clean = ezdxf.new()
    ok = clean.blocks.new("OK")
    ok.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    assert not {f.code for f in submission_from_doc(clean)} & {"layer_0", "defpoints", "block_layer", "not_bylayer"}


def test_project_qa_is_saved_and_overrides_the_minimum(tmp_path):
    project = create_project(tmp_path, "20117", "חצרים", "Hatzerim")
    project.qa = {"min_text_mm": 1.0, "text_heights_mm": [1.2, 2.5, 3.5, 5, 7],
                  "annotation_heights_mm": [1.2, 1.8],
                  "lineweight_min_mm": 0.05, "lineweight_max_mm": 2, "thick_thin_ratio": 1,
                  "utility_submission": False}
    project.save()
    loaded = Project.load(project.path)
    assert loaded.qa["min_text_mm"] == 1.0
    tiny = _spec([{"id": "t", "type": "text", "at": [500, 1500], "height": 1.2, "text": "tiny"}], qa={})
    # spec.qa {} does not override; the project does. An explicit spec qa would.
    assert _has(tiny, "text_too_small")
    assert not _has(tiny, "text_too_small", project=loaded)
    assert QA_DEFAULTS["min_text_mm"] == 2.5
    assert QA_DEFAULTS["min_slopes"][0]["slope"] == 0.015


def test_overlay_circles_the_problem_and_the_import_report_points_at_it(tmp_path):
    spec = _spec([
        {"id": "t1", "type": "text", "at": [500, 1500], "height": 2.5, "text": "HELLO"},
        {"id": "t2", "type": "text", "at": [520, 1500], "height": 2.5, "text": "WORLD"},
    ])
    path = tmp_path / "qa_overlay.png"
    assert write_overlay(spec, path) == str(path)
    assert path.stat().st_size > 1000
    import matplotlib.pyplot as plt
    image = plt.imread(path)
    red = (image[:, :, 0] > 0.6) & (image[:, :, 1] < 0.35) & (image[:, :, 2] < 0.35)
    assert red.any()

    from sketch2cad.bridge import import_reply
    from sketch2cad.documents import create_document
    project = create_project(tmp_path / "proj", "20117", "חצרים", "Hatzerim")
    doc = create_document(project, title_he="בדיקה", title_en="QA", discipline="W", subject="QA")
    reply = tmp_path / "reply.json"
    reply.write_text(json.dumps({"spec": spec, "changes": [], "assumptions": [], "questions": []}),
                     encoding="utf-8")
    report = import_reply(reply, doc.path, trial=tmp_path / "trial", apply=False)
    assert report["ok"] and report["qa_overlay"] == "qa_overlay.png"
    assert report["outputs"]["qa_overlay"] == "qa_overlay.png"
    assert (tmp_path / "trial" / "qa_overlay.png").stat().st_size > 1000
    assert any(f["code"] == "text_overlap" and f["he"] and f["en"] for f in report["checks"])


def test_leader_label_box_matches_the_rendered_text():
    """A leader is drawn left-aligned from label_at even when label_align is right."""
    spec = _spec([
        {"id": "near", "type": "text", "at": [1500, 1800], "height": 2.5, "text": "NEAR"},
        {"id": "v", "type": "circle", "at": [2800, 1400], "radius": 40,
         "label": "VALVE", "label_at": [1750, 1800], "label_align": "right", "leader": True},
    ])
    assert not _has(spec, "text_overlap")
    assert not _has(spec, "outside_sheet")


def test_qa_sheet_uses_the_renderer_origin(tmp_path):
    spec = _spec([{"id": "t", "type": "text", "at": [800, 1600], "height": 2.5, "text": "MARK"}])
    from sketch2cad.drawing import render
    import ezdxf
    render(spec, tmp_path, "sheet", formats=("dxf",))
    doc = ezdxf.readfile(tmp_path / "sheet.dxf")
    scene = review(spec).scene
    found = [e for e in doc.modelspace().query("TEXT") if e.dxf.text == "MARK"]
    assert found and scene is not None
    insert = found[0].dxf.insert
    px = (insert.x - scene.origin[0]) / scene.k
    py = (insert.y - scene.origin[1]) / scene.k
    box = next(b for b in scene.boxes if b.eid == "t")
    assert box.x0 - 0.5 <= px <= box.x1 + 0.5
    assert box.y0 - 0.5 <= py <= box.y1 + 0.5


def test_metric_inverts_and_percent_slope_use_the_span_between_them():
    """INV. 225.00 / 224.84, 40 m apart on a longer mm pipe, slope 0.4%."""
    pipe = {"id": "p", "type": "pipe", "dn": 400, "label": "SLOPE 0.4%",
            "points": [[0, 0], [63160, 0]]}
    spec = _spec([
        pipe,
        {"id": "i1", "type": "text", "at": [10000, 400], "height": 2.5, "text": "INV. 225.00", "pipe": "p"},
        {"id": "i2", "type": "text", "at": [50000, 400], "height": 2.5, "text": "INV. 224.84", "pipe": "p"},
    ])
    assert not _has(spec, "slope_mismatch")
    assert not _has(spec, "min_slope")
    assert not _has(spec, "slope_unchecked")


def test_inverts_off_the_pipe_use_the_projected_run():
    """INV texts 2.36 m off a 1:400 pipe, 40 m apart along it, 11.6 m from the ends."""
    pipe = {"id": "p", "type": "pipe", "dn": 400, "label": "SLOPE 0.4%",
            "points": [[0, 0], [63160, 0]]}
    texts = [
        {"id": "i1", "type": "text", "at": [11580, 2360], "height": 2.5, "text": "INV. 225.00"},
        {"id": "i2", "type": "text", "at": [51580, 2360], "height": 2.5, "text": "INV. 224.84"},
    ]
    spec = _spec([pipe, *texts], scale=400)
    assert not _has(spec, "slope_mismatch")
    assert not _has(spec, "min_slope")
    assert not _has(spec, "slope_unchecked")
    low = _spec([pipe, texts[0], {**texts[1], "text": "INV. 224.70"}], scale=400)
    assert _has(low, "slope_mismatch")
    stationed = _spec([_pipe(slope=0.4, slope_unit="percent", dn=400, inv=[225.00, 224.84],
                             inv_stations=[11580, 51580], points=[[0, 0], [63160, 0]])], scale=400)
    assert not _has(stationed, "slope_mismatch")
    assert not _has(stationed, "slope_unchecked")


def test_large_target_keeps_a_full_outline():
    spec = _spec([{
        "id": "wide", "type": "text", "at": [400, 1600], "height": 3.0,
        "text": "SECTION TITLE RUNNING WELL PAST THE TWENTY EIGHT MILLIMETRE CAP",
    }])
    markers, _rows = overlay_plan(spec)
    mark = next(marker for marker in markers if marker.element == "wide")
    assert mark.thin
    assert mark.x1 - mark.x0 > MAX_MARKER_MM


def test_title_role_is_not_an_inconsistent_height():
    spec = _spec([
        {"id": "sec", "type": "text", "role": "title", "at": [800, 2200], "height": 3.5,
         "text": "SECTION A-A"},
        {"id": "note", "type": "text", "at": [800, 1800], "height": 2.5, "text": "NOTE"},
    ])
    assert not _has(spec, "text_height_inconsistent")
    block = _spec([{"id": "p", "type": "circle", "at": [1000, 1500], "radius": 20}])
    assert not any(f.code == "text_height_inconsistent" and f.element == "title"
                   for f in review(block).findings)


def test_overlay_marker_labels_do_not_overlap():
    """Each affected element gets its own capped marker, and the number badges do not collide.

    A sheet-wide finding (line weight, layer 0) stays in the table and is not drawn on the sheet.
    """
    spec = _spec([
        {"id": "t1", "type": "text", "at": [800, 1800], "height": 3.0, "text": "HELLO"},
        {"id": "t2", "type": "text", "at": [812, 1800], "height": 3.0, "text": "WORLD"},
        {"id": "t3", "type": "text", "at": [824, 1800], "height": 3.0, "text": "AGAIN"},
    ], qa={"utility_submission": False, "lineweight_max_mm": 0.30})
    markers, rows = overlay_plan(spec)
    assert markers and rows
    for marker in markers:
        badge = marker.label_rect()
        overlaps_box = not (badge[2] <= marker.x0 or marker.x1 <= badge[0]
                            or badge[3] <= marker.y0 or marker.y1 <= badge[1])
        assert not overlaps_box
        if not marker.thin:
            assert marker.x1 - marker.x0 <= MAX_MARKER_MM + 1e-6
            assert marker.y1 - marker.y0 <= MAX_MARKER_MM + 1e-6
    height_marks = [marker for marker in markers if marker.code == "text_height_standard" and marker.element in {"t1", "t2", "t3"}]
    height_rows = [row for row in rows if row.code == "text_height_standard" and "t1" in row.element]
    assert len(height_rows) == 1
    assert height_rows[0].element.startswith("×3:")
    assert all(name in height_rows[0].element for name in ("t1", "t2", "t3"))
    assert 2 <= len(height_marks) <= 3
    assert len({round(marker.x0, 1) for marker in height_marks}) >= 2
    assert len({marker.number for marker in height_marks}) == 1
    sheet = [row for row in rows if row.code == "lineweight_range"]
    assert sheet and not sheet[0].on_sheet
    assert not any(marker.code in SHEET_CODES for marker in markers)
    assert [row.number for row in rows] == list(range(1, len(rows) + 1))
    assert {marker.number for marker in markers} <= {row.number for row in rows}
    rects = [marker.label_rect() for marker in markers]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            separated = a[2] <= b[0] + 0.05 or b[2] <= a[0] + 0.05 or a[3] <= b[1] + 0.05 or b[3] <= a[1] + 0.05
            assert separated, (a, b)


def test_valve_typical_collapses_repeated_height_rows():
    """Legend cells at the table height are not a second height, and one role is one row.

    slope_unchecked stays in the table, at info, with no marker on the sheet.
    """
    from sketch2cad.typical import generate
    spec = generate("valve_in_line")
    result = review(spec)
    assert len(result.findings) <= 8
    assert not any(f.code == "text_height_inconsistent" for f in result.findings)
    markers, rows = overlay_plan(spec)
    assert len(rows) == len(result.findings)
    assert len(rows) <= 8
    assert not any(marker.code == "slope_unchecked" for marker in markers)
    info = [row for row in rows if row.code == "slope_unchecked"]
    assert info and all(row.severity == "info" and not row.on_sheet for row in info)
    dim = next(row for row in rows if row.code == "text_height_standard" and "dim:" in row.element)
    assert dim.element.startswith("×")
    assert "dim:0" in dim.element and "dim:1" in dim.element


def test_findings_carry_both_languages():
    spec = _spec([
        {"id": "t1", "type": "text", "at": [500, 1500], "height": 1.2, "text": "see #missing"},
        {"id": "t2", "type": "text", "at": [510, 1500], "height": 1.2, "text": "X"},
    ])
    found = checks.run_checks(spec, None)
    assert {f.code for f in found} >= {"text_overlap", "text_too_small", "unknown_ref"}
    for finding in found:
        assert finding.en and finding.he and finding.severity in ("error", "warning", "info")
