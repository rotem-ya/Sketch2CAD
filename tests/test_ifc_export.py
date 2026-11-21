"""IFC export: reopen the file with IfcOpenShell and check entities, units and geometry."""
import numpy as np
import pytest

ezdxf = pytest.importorskip("ezdxf")
ifcopenshell = pytest.importorskip("ifcopenshell")
import ifcopenshell.util.element

from sketch2cad.drawing import render
from sketch2cad.io.ifc2dxf import ifc_to_dxf
from sketch2cad.io.spec2ifc import spec_to_ifc, validate_ifc
from sketch2cad.spec import normalize_spec


def _building():
    return {
        "units": "mm",
        "scale": 50,
        "sheet": {"fields": {"title_en": "Pump house"}},
        "elements": [
            {"id": "w1", "type": "wall", "status": "new", "points": [[0, 0], [5000, 0]],
             "height": 3000, "thickness": 200},
            {"id": "d1", "type": "opening", "host": "w1", "kind": "door", "at": [1500, 0],
             "width": 900, "height": 2100, "sill": 0},
            {"id": "win", "type": "opening", "host": "w1", "kind": "window", "at": [3500, 0],
             "width": 1200, "height": 1200, "sill": 900},
            {"id": "f1", "type": "floor", "points": [[0, 0], [5000, 0], [5000, 4000], [0, 4000]],
             "thickness": 200, "z": 0},
        ],
        "dimensions": [
            {"type": "chain", "axis": "h", "base": -600, "points": [[0, 0], [1500, 0], [5000, 0]],
             "texts": ["", "3500"]},
        ],
    }


def _quantities(product):
    found = {}
    for rel in product.IsDefinedBy or []:
        definition = rel.RelatingPropertyDefinition
        if not definition.is_a("IfcElementQuantity"):
            continue
        for quantity in definition.Quantities:
            if quantity.is_a("IfcQuantityLength"):
                found[quantity.Name] = quantity.LengthValue
            elif quantity.is_a("IfcQuantityArea"):
                found[quantity.Name] = quantity.AreaValue
            elif quantity.is_a("IfcQuantityVolume"):
                found[quantity.Name] = quantity.VolumeValue
    return found


def _bbox(model, product):
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    shape = ifcopenshell.geom.create_shape(settings, product)
    verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
    return verts.min(axis=0), verts.max(axis=0)


def test_export_reopens_with_units_and_counts(tmp_path):
    spec, warnings = normalize_spec(_building())
    assert not warnings
    path = spec_to_ifc(spec, tmp_path / "house.ifc", warnings=warnings)
    assert warnings == []
    report = validate_ifc(path)
    assert report["schema"] == "IFC4"
    assert report["length_unit"] == {"prefix": "MILLI", "name": "METRE"}
    assert report["unit_scale_m"] == pytest.approx(0.001)
    counts = report["counts"]
    assert counts["IfcProject"] == 1 and counts["IfcBuildingStorey"] == 1
    assert counts["IfcWall"] == 1
    assert counts["IfcOpeningElement"] == 2
    assert counts["IfcDoor"] == 1 and counts["IfcWindow"] == 1
    assert counts["IfcRelVoidsElement"] == 2 and counts["IfcRelFillsElement"] == 2
    assert counts["IfcSlab"] == 1
    assert counts["IfcAnnotation"] == 2
    assert counts["IfcElementQuantity"] >= 5

    model = ifcopenshell.open(str(path))
    assert model.by_type("IfcProject")[0].Name == "Pump house"
    wall = model.by_type("IfcWall")[0]
    q = _quantities(wall)
    assert q["Length"] == pytest.approx(5000)
    assert q["Width"] == pytest.approx(200)
    assert q["Height"] == pytest.approx(3000)
    assert q["GrossSideArea"] == pytest.approx(15)          # m2, not mm2
    opening_area = 0.9 * 2.1 + 1.2 * 1.2
    assert q["NetSideArea"] == pytest.approx(15 - opening_area)
    assert q["GrossVolume"] == pytest.approx(3)             # m3
    assert q["NetVolume"] == pytest.approx(3 - opening_area * 0.2)

    low, high = _bbox(model, wall)
    assert high[0] - low[0] == pytest.approx(5, abs=0.01)
    assert high[1] - low[1] == pytest.approx(0.2, abs=0.01)
    assert low[2] == pytest.approx(0, abs=0.01) and high[2] == pytest.approx(3, abs=0.01)

    door_opening = next(el for el in model.by_type("IfcOpeningElement") if el.Name == "d1")
    low, high = _bbox(model, door_opening)
    assert low[0] == pytest.approx(1.05, abs=0.02) and high[0] == pytest.approx(1.95, abs=0.02)
    assert high[2] - low[2] == pytest.approx(2.1, abs=0.02)
    win_opening = next(el for el in model.by_type("IfcOpeningElement") if el.Name == "win")
    low, high = _bbox(model, win_opening)
    assert low[2] == pytest.approx(0.9, abs=0.02) and high[2] == pytest.approx(2.1, abs=0.02)

    window = model.by_type("IfcWindow")[0]
    assert window.OverallWidth == pytest.approx(1200)
    assert window.OverallHeight == pytest.approx(1200)
    low, high = _bbox(model, model.by_type("IfcSlab")[0])
    assert model.by_type("IfcSlab")[0].PredefinedType == "FLOOR"
    assert high[2] == pytest.approx(0, abs=0.01) and low[2] == pytest.approx(-0.2, abs=0.01)
    slab_q = _quantities(model.by_type("IfcSlab")[0])
    assert slab_q["GrossArea"] == pytest.approx(20) and slab_q["Depth"] == pytest.approx(200)

    dims = {ann.Name: ifcopenshell.util.element.get_psets(ann)["Sketch2CAD_Dimension"]
            for ann in model.by_type("IfcAnnotation")}
    assert dims["DIM-1/1"]["MeasuredLength"] == pytest.approx(1500)
    assert dims["DIM-1/1"]["Axis"] == "h"
    assert dims["DIM-1/2"]["MeasuredLength"] == pytest.approx(3500)
    assert dims["DIM-1/2"]["Text"] == "3500"
    assert all(ann.ObjectType == "DIMENSION" for ann in model.by_type("IfcAnnotation"))

    msp = ezdxf.readfile(ifc_to_dxf(path, tmp_path / "plan.dxf")).modelspace()
    walls = [e for e in msp.query("LINE") if e.dxf.layer.endswith("IfcWall")]
    xs = [round(c) for e in walls for c in (e.dxf.start.x, e.dxf.end.x)]
    assert xs and min(xs) == 0 and max(xs) == 5000


def test_metre_spec_uses_metre_length(tmp_path):
    spec = {
        "units": "m",
        "elements": [
            {"id": "w", "type": "wall", "points": [[0, 0], [5, 0]], "height": 3, "thickness": 0.2},
            {"id": "s", "type": "floor", "corners": [[0, 0], [5, 4]], "thickness": 0.2},
        ],
        "dimensions": [{"type": "chain", "axis": "v", "base": 6, "points": [[0, 0], [0, 4]]}],
    }
    path = spec_to_ifc(spec, tmp_path / "m.ifc")
    report = validate_ifc(path)
    assert report["length_unit"]["prefix"] in (None, "None", "NONE")
    assert report["length_unit"]["name"] == "METRE"
    assert report["unit_scale_m"] == pytest.approx(1)
    assert report["counts"]["IfcWall"] == 1 and report["counts"]["IfcSlab"] == 1
    assert report["counts"]["IfcAnnotation"] == 1
    model = ifcopenshell.open(str(path))
    assert _quantities(model.by_type("IfcWall")[0])["Length"] == pytest.approx(5)
    low, high = _bbox(model, model.by_type("IfcWall")[0])
    assert high[0] - low[0] == pytest.approx(5, abs=0.01)


def test_polyline_wall_is_one_entity_per_segment(tmp_path):
    spec = {"units": "mm", "elements": [
        {"id": "w", "type": "wall", "points": [[0, 0], [4000, 0], [4000, 2000]], "thickness": 200, "height": 2700},
        {"id": "o", "type": "opening", "host": "w", "at": [4000, 1000], "width": 800, "height": 2000},
    ]}
    path = spec_to_ifc(spec, tmp_path / "l.ifc")
    report = validate_ifc(path)
    assert report["counts"]["IfcWall"] == 2
    assert report["counts"]["IfcOpeningElement"] == 1
    assert report["counts"]["IfcRelVoidsElement"] == 1
    model = ifcopenshell.open(str(path))
    names = sorted(wall.Name for wall in model.by_type("IfcWall"))
    assert names == ["w/1", "w/2"]
    host = next(wall for wall in model.by_type("IfcWall") if wall.Name == "w/2")
    assert _quantities(host)["NetSideArea"] == pytest.approx(2.0 * 2.7 - 0.8 * 2.0)


def test_bad_elements_are_warnings(tmp_path):
    warnings = []
    path = spec_to_ifc({
        "units": "mm",
        "elements": [
            {"id": "short", "type": "wall", "points": [[0, 0]]},
            {"id": "loose", "type": "opening", "at": [0, 0], "kind": "gate"},
            {"id": "blob", "type": "floor", "points": [[0, 0], [1, 0]]},
            {"id": "pipe", "type": "pipe", "points": [[0, 0], [1000, 0]]},
        ],
        "dimensions": [{"type": "chain", "points": [[0, 0]]}],
    }, tmp_path / "bad.ifc", warnings=warnings)
    report = validate_ifc(path)
    assert report["counts"]["IfcWall"] == 0
    assert report["counts"]["IfcOpeningElement"] == 1          # kept, but not a void
    assert report["counts"]["IfcRelVoidsElement"] == 0
    assert report["counts"]["IfcDoor"] == 0
    assert report["counts"]["IfcSlab"] == 0
    assert any("short" in msg for msg in warnings)
    assert any("loose" in msg and "kind" in msg for msg in warnings)
    assert any("blob" in msg for msg in warnings)
    assert report["counts"]["IfcProject"] == 1


def test_render_writes_ifc_beside_dxf(tmp_path):
    spec, _ = normalize_spec(_building())
    res = render(spec, tmp_path, "sheet", formats=("dxf", "ifc"))
    assert res["dxf"].exists() and res["ifc"].exists()
    assert not any(msg.startswith("IFC skipped") for msg in res["warnings"])
    assert validate_ifc(res["ifc"])["counts"]["IfcWall"] == 1
    msp = ezdxf.readfile(res["dxf"]).modelspace()
    faces = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "NEW"]
    ys = sorted({round(p[1], 3) for e in faces for p in e.get_points()})
    assert -100 in ys and 100 in ys                          # 200 mm wall, centred


def test_second_storey_keeps_its_elevation(tmp_path):
    spec = {"units": "mm", "elements": [
        {"id": "low", "type": "wall", "storey": "Ground", "points": [[0, 0], [2000, 0]], "height": 3000},
        {"id": "up", "type": "wall", "storey": "First", "z": 3000, "points": [[0, 0], [2000, 0]], "height": 3000},
    ]}
    path = spec_to_ifc(spec, tmp_path / "levels.ifc")
    model = ifcopenshell.open(str(path))
    elevations = {s.Name: s.Elevation for s in model.by_type("IfcBuildingStorey")}
    assert elevations["Ground"] == pytest.approx(0)
    assert elevations["First"] == pytest.approx(3000)
    upper = next(w for w in model.by_type("IfcWall") if w.Name == "up")
    low, high = _bbox(model, upper)
    assert low[2] == pytest.approx(3, abs=0.02) and high[2] == pytest.approx(6, abs=0.02)


def test_opening_without_host_on_unknown_storey_falls_back(tmp_path):
    """A missing host plus a storey no wall/floor names must not KeyError on 'Ground Floor'."""
    spec = {"units": "mm", "scale": 50, "elements": [
        {"id": "w1", "type": "wall", "storey": "Level 2", "z": 3000, "points": [[0, 0], [4000, 0]],
         "height": 3000, "thickness": 200},
        {"id": "d9", "type": "opening", "host": "nope", "kind": "door", "at": [500, 0], "width": 900,
         "height": 2100},
    ]}
    warnings: list[str] = []
    path = spec_to_ifc(spec, tmp_path / "x.ifc", warnings=warnings)
    assert any("no host wall" in w for w in warnings)
    assert any("does not exist" in w for w in warnings)
    model = ifcopenshell.open(str(path))
    assert len(model.by_type("IfcOpeningElement")) == 1


def test_dimension_keeps_storey_elevation(tmp_path):
    """A dimension on a raised storey stays at that storey's height in world coordinates."""
    spec = {"units": "mm", "scale": 50, "elements": [
        {"id": "w1", "type": "wall", "storey": "Level 2", "z": 3000, "points": [[0, 0], [4000, 0]],
         "height": 3000, "thickness": 200},
    ], "dimensions": [
        {"type": "chain", "axis": "h", "base": -500, "storey": "Level 2", "points": [[0, 0], [4000, 0]]},
    ]}
    path = spec_to_ifc(spec, tmp_path / "x.ifc", warnings=[])
    model = ifcopenshell.open(str(path))
    ann = model.by_type("IfcAnnotation")[0]
    low, high = _bbox_points(ann)
    assert low[2] == pytest.approx(3.0) and high[2] == pytest.approx(3.0)


def _bbox_points(product):
    pts = []
    for rep in product.Representation.Representations:
        for item in rep.Items:
            pts += [p.Coordinates for p in item.Points]
    arr = np.array(pts, dtype=float) / 1000.0
    return arr.min(axis=0), arr.max(axis=0)


def test_plan_opening_follows_host_wall(tmp_path):
    """The DXF plan rectangle uses the host wall's direction, thickness and `along` position."""
    spec = {"units": "mm", "scale": 50, "elements": [
        {"id": "w1", "type": "wall", "points": [[0, 0], [0, 5000]], "height": 3000, "thickness": 300},
        {"id": "d1", "type": "opening", "host": "w1", "kind": "door", "along": 2000, "width": 900,
         "height": 2100},
    ]}
    spec, _ = normalize_spec(spec)
    res = render(spec, tmp_path, "host", formats=("dxf",))
    msp = ezdxf.readfile(str(res["dxf"] if isinstance(res, dict) and "dxf" in res else next(tmp_path.glob("*.dxf")))).modelspace()
    boxes = [e for e in msp.query("LWPOLYLINE") if e.is_closed and len(e) == 4]
    spans = []
    for e in boxes:
        xs = [p[0] for p in e.get_points()]
        ys = [p[1] for p in e.get_points()]
        spans.append((round(min(xs)), round(max(xs)), round(min(ys)), round(max(ys))))
    assert (-150, 150, 1550, 2450) in spans, spans
