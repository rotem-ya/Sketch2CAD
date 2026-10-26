"""Drawing QA: every check has a case that flags and a case that does not."""
import json
from pathlib import Path

import ezdxf
import pytest

from sketch2cad import checks
from sketch2cad.catalog import load_catalog
from sketch2cad.projects import Project, create_project
from sketch2cad.qa import QA_DEFAULTS, review, submission_from_doc, write_overlay
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
    assert not _has(_spec([_pipe(slope=0.01, inv=[500, 400])]), "slope_mismatch")
    assert _has(_spec([_pipe(slope=0.01, inv=[500, 450])]), "slope_mismatch")
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
    assert not _has(_spec([_pipe(slope=1.5, dn=150)]), "min_slope")  # 1.5 is read as 1.5%
    assert not _has(_spec([_pipe(slope=0.01, dn=200)]), "min_slope")
    assert _has(_spec([_pipe(slope=0.005, dn=250)]), "min_slope")
    assert not _has(_spec([_pipe(dn=150)]), "min_slope")  # no slope declared: nothing to reject


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
    assert raw["elements"][0]["id"] == raw["elements"][1]["id"] == "a"

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


def test_findings_carry_both_languages():
    spec = _spec([
        {"id": "t1", "type": "text", "at": [500, 1500], "height": 1.2, "text": "see #missing"},
        {"id": "t2", "type": "text", "at": [510, 1500], "height": 1.2, "text": "X"},
    ])
    found = checks.run_checks(spec, None)
    assert {f.code for f in found} >= {"text_overlap", "text_too_small", "unknown_ref"}
    for finding in found:
        assert finding.en and finding.he and finding.severity in ("error", "warning", "info")
