"""Clash and defect checks: IfcClash results written as BCF 2.1, and the sample IFC."""
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from sketch2cad.drawing import render
from sketch2cad.io import clash
from sketch2cad.io.spec2ifc import spec_to_ifc, validate_ifc
from sketch2cad.spec import normalize_spec

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "clash" / "crossing-walls.ifc"
WHEN = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def _crossing():
    return {
        "units": "mm",
        "scale": 50,
        "sheet": {"fields": {"title_en": "Crossing walls"}},
        "elements": [
            {"id": "w1", "type": "wall", "points": [[0, 0], [4000, 0]],
             "height": 3000, "thickness": 200, "z": -100},
            {"id": "w2", "type": "wall", "points": [[2000, -2000], [2000, 2000]],
             "height": 3000, "thickness": 200, "z": 0},
            {"id": "f1", "type": "floor",
             "points": [[-500, -2500], [4500, -2500], [4500, 2500], [-500, 2500]],
             "thickness": 200, "z": 0},
            {"id": "wL", "type": "wall", "points": [[6000, 0], [8000, 0], [8000, 2000]],
             "height": 3000, "thickness": 200, "z": 0},
        ],
    }


def _clean():
    return {
        "units": "mm",
        "elements": [
            {"id": "w", "type": "wall", "points": [[0, 0], [4000, 0]],
             "height": 3000, "thickness": 200, "z": 0},
            {"id": "f", "type": "floor", "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
             "thickness": 200, "z": 0},
        ],
    }


def _loose_opening(tmp_path):
    return spec_to_ifc({
        "units": "mm",
        "elements": [
            {"id": "w", "type": "wall", "points": [[0, 0], [3000, 0]], "height": 3000, "thickness": 200},
            {"id": "loose", "type": "opening", "at": [0, 0], "kind": "gate"},
        ],
    }, tmp_path / "loose.ifc", warnings=[])


def _names(topic):
    return topic["title"]


def test_sample_ifc_is_an_ifc4_model_with_a_crossing():
    assert SAMPLE.is_file()
    report = validate_ifc(SAMPLE)
    assert report["schema"] == "IFC4"
    assert report["counts"]["IfcWall"] == 4
    assert report["counts"]["IfcSlab"] == 1
    model = ifcopenshell.open(str(SAMPLE))
    assert sorted(wall.Name for wall in model.by_type("IfcWall")) == ["w1", "w2", "wL/1", "wL/2"]


def test_clean_model_has_no_defects(tmp_path):
    path = spec_to_ifc(_clean(), tmp_path / "clean.ifc")
    assert clash.find_defects(path) == []


def test_loose_opening_is_a_defect_and_a_removed_body_is_too(tmp_path):
    path = _loose_opening(tmp_path)
    codes = {item["code"] for item in clash.find_defects(path)}
    assert "opening_without_host" in codes

    model = ifcopenshell.open(str(path))
    wall = model.by_type("IfcWall")[0]
    wall.Representation = None
    for rel in list(wall.IsDefinedBy or []):
        definition = rel.RelatingPropertyDefinition
        if definition is not None and definition.is_a("IfcElementQuantity"):
            for quantity in definition.Quantities:
                if quantity.is_a("IfcQuantityLength") and quantity.Name == "Length":
                    quantity.LengthValue = 0
    bare = tmp_path / "bare.ifc"
    model.write(str(bare))
    found = {item["code"] for item in clash.find_defects(bare)}
    assert {"no_body", "non_positive_quantity"} <= found


def test_bcf_zip_uses_forward_slashes_and_a_topic_folder(tmp_path):
    path = _loose_opening(tmp_path)
    out = tmp_path / "issues.bcf"
    report = clash.check_model(path, out, clashes=False, when=WHEN)
    assert report["warnings"] == []
    assert out.is_file()
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert "bcf.version" in names
    assert all("\\" not in name for name in names)
    assert any(name.endswith("/") for name in names)
    topics = clash.read_bcf(out)
    assert topics
    assert all(topic["topic_type"] == "Defect" for topic in topics)
    assert all(topic["status"] == "Open" for topic in topics)
    assert all(topic["author"] == "Sketch2CAD" for topic in topics)
    assert all(len(guid) == 22 for topic in topics for guid in topic["guids"])
    assert topics[0]["ifc_name"] == "loose.ifc"


def test_render_does_not_write_a_bcf(tmp_path):
    spec, _ = normalize_spec(_clean())
    res = render(spec, tmp_path, "sheet", formats=("dxf", "ifc"))
    assert res["ifc"].exists()
    assert list(tmp_path.glob("*.bcf")) == []


def test_missing_ifcclash_still_writes_the_defects(tmp_path, monkeypatch):
    monkeypatch.setattr(clash, "clash_available", lambda: False)

    def fail(*_args, **_kwargs):
        raise AssertionError("IfcClash was called")

    monkeypatch.setattr(clash, "find_clashes", fail)
    path = _loose_opening(tmp_path)
    report = clash.check_model(path, tmp_path / "issues.bcf", when=WHEN)
    assert report["clashes"] == []
    assert report["defects"]
    assert any("IfcClash" in msg for msg in report["warnings"])
    assert clash.read_bcf(report["bcf"])


@pytest.mark.skipif(not clash.clash_available(), reason="IfcClash is not installed")
def test_sample_clash_bcf_names_the_crossing_and_not_the_corner(tmp_path):
    report = clash.check_model(SAMPLE, tmp_path / "crossing.bcf", when=WHEN)
    assert report["warnings"] == []
    assert report["defects"] == []
    titles = [_names(topic) for topic in clash.read_bcf(report["bcf"])]
    assert any("w1" in title and "w2" in title for title in titles)
    assert any("w1" in title and "f1" in title for title in titles)
    assert not any("wL" in title for title in titles)
    clash_topics = [topic for topic in clash.read_bcf(report["bcf"]) if topic["topic_type"] == "Clash"]
    assert clash_topics
    assert all(topic["status"] == "Open" for topic in clash_topics)
    assert "0." in clash_topics[0]["description"] and " m " in clash_topics[0]["description"]
    assert clash_topics[0]["camera"] is not None
    assert clash_topics[0]["segment"] is not None
    assert len(clash_topics[0]["segment"]) == 2


@pytest.mark.skipif(not clash.clash_available(), reason="IfcClash is not installed")
def test_a_wall_sitting_on_a_slab_is_not_a_clash(tmp_path):
    path = spec_to_ifc(_clean(), tmp_path / "clean.ifc")
    report = clash.check_model(path, tmp_path / "clean.bcf", when=WHEN)
    assert report["clashes"] == []
    assert clash.read_bcf(report["bcf"]) == []


@pytest.mark.skipif(not clash.clash_available(), reason="IfcClash is not installed")
def test_same_element_segments_are_not_reported(tmp_path):
    path = spec_to_ifc({
        "units": "mm",
        "elements": [
            {"id": "w", "type": "wall", "points": [[0, 0], [4000, 0], [4000, 2000]],
             "height": 3000, "thickness": 200},
        ],
    }, tmp_path / "corner.ifc")
    model = ifcopenshell.open(str(path))
    walls = list(model.by_type("IfcWall"))
    assert len(walls) == 2
    products = {wall.GlobalId: wall for wall in walls}
    issue = clash._clash_issue({
        "a_global_id": walls[0].GlobalId,
        "b_global_id": walls[1].GlobalId,
        "a_name": walls[0].Name,
        "b_name": walls[1].Name,
        "a_ifc_class": "IfcWall",
        "b_ifc_class": "IfcWall",
        "type": "pierce",
        "p1": [4, 0, 0],
        "p2": [4, 0, 1],
        "distance": 0.2,
    }, products, set())
    assert issue is None
    report = clash.check_model(path, tmp_path / "corner.bcf", when=WHEN)
    assert report["clashes"] == []


@pytest.mark.skipif(not clash.clash_available(), reason="IfcClash is not installed")
def test_a_second_model_can_be_the_other_group(tmp_path):
    host = spec_to_ifc(_clean(), tmp_path / "host.ifc")
    other = spec_to_ifc({
        "units": "mm",
        "elements": [
            {"id": "slab", "type": "floor", "points": [[1000, -500], [3000, -500], [3000, 500], [1000, 500]],
             "thickness": 400, "z": 200},
        ],
    }, tmp_path / "other.ifc")
    sets = clash.default_clash_sets(host, other=other)
    assert any(item["name"] == "Model against other" for item in sets)
    report = clash.check_model(host, tmp_path / "pair.bcf", other=other, when=WHEN)
    titles = [topic["title"] for topic in clash.read_bcf(report["bcf"])]
    assert any("slab" in title for title in titles)
