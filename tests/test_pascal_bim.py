"""Pascal scene prototype: editable walls, openings, floors and ceilings beside an IFC file."""
import json

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")

from sketch2cad.io.pascal_bim import (
    EDITOR_COMMAND, MCP_COMMAND, PASCAL_REPO, scene_from_spec, spec_from_scene, write_package,
)
from sketch2cad.io.spec2ifc import validate_ifc
from sketch2cad.spec import normalize_spec


def _as_built():
    return {
        "units": "mm",
        "scale": 50,
        "sheet": {"fields": {"title_en": "As built"}},
        "elements": [
            {"id": "w1", "type": "wall", "status": "existing", "points": [[0, 0], [5000, 0]],
             "height": 3000, "thickness": 200},
            {"id": "d1", "type": "opening", "host": "w1", "kind": "door", "at": [1500, 0],
             "width": 900, "height": 2100, "sill": 0},
            {"id": "win", "type": "opening", "host": "w1", "kind": "window", "at": [3500, 0],
             "width": 1200, "height": 1200, "sill": 900},
            {"id": "f1", "type": "floor", "corners": [[0, 0], [5000, 4000]], "thickness": 200, "z": 0},
            {"id": "c1", "type": "ceiling", "corners": [[0, 0], [5000, 4000]], "z": 2700, "thickness": 20},
        ],
        "dimensions": [{"type": "chain", "axis": "h", "base": -400, "points": [[0, 0], [5000, 0]]}],
    }


def test_scene_round_trip_and_scan_path(tmp_path):
    spec, norm_warnings = normalize_spec(_as_built())
    assert not norm_warnings
    scene, warnings = scene_from_spec(spec, scan=r"D:\scans\as-built.e57")
    assert any("not an allowed asset URL" in msg for msg in warnings)
    nodes = scene["nodes"]
    wall = next(n for n in nodes.values() if n["type"] == "wall")
    assert wall["start"] == [0, 0] and wall["end"] == pytest.approx([5, 0])
    assert wall["height"] == pytest.approx(3) and wall["thickness"] == pytest.approx(0.2)
    door = next(n for n in nodes.values() if n["type"] == "door")
    window = next(n for n in nodes.values() if n["type"] == "window")
    assert door["parentId"] == wall["id"] and door["wallId"] == wall["id"]
    assert door["position"] == pytest.approx([1.5, 1.05, 0])
    assert window["position"][1] == pytest.approx(0.9 + 0.6)
    assert door["id"] in wall["children"] and window["id"] in wall["children"]
    slab = next(n for n in nodes.values() if n["type"] == "slab")
    ceiling = next(n for n in nodes.values() if n["type"] == "ceiling")
    assert slab["elevation"] == pytest.approx(0) and slab["thickness"] == pytest.approx(0.2)
    assert ceiling["height"] == pytest.approx(2.7)
    scan = next(n for n in nodes.values() if n["type"] == "scan")
    assert scan["url"] is None
    assert scan["metadata"]["sketch2cad"]["sourcePath"].endswith("as-built.e57")
    assert scan["parentId"].startswith("level_")

    back, extra = spec_from_scene(scene)
    assert back["units"] == "mm"
    kinds = {el["id"]: el for el in back["elements"]}
    assert kinds["w1"]["points"][1] == pytest.approx([5000, 0])
    assert kinds["d1"]["kind"] == "door" and kinds["d1"]["host"] == "w1"
    assert kinds["d1"]["at"] == pytest.approx([1500, 0])
    assert kinds["d1"]["sill"] == pytest.approx(0)
    assert kinds["win"]["kind"] == "window" and kinds["win"]["sill"] == pytest.approx(900)
    assert kinds["f1"]["type"] == "floor" and kinds["f1"]["z"] == pytest.approx(0)
    assert kinds["c1"]["type"] == "ceiling" and kinds["c1"]["z"] == pytest.approx(2700)
    assert extra["scans"][0]["url"] is None
    dumped = json.dumps(scene)
    assert "PASCAL_API_KEY" not in dumped and "Bearer " not in dumped


def test_allowed_scan_url_is_kept_and_dangerous_urls_are_not():
    https, warnings = scene_from_spec(
        {"units": "m", "elements": [{"id": "w", "type": "wall", "points": [[0, 0], [1, 0]]}]},
        scan={"url": "https://cdn.example.com/scan.glb", "name": "cloud"},
    )
    assert warnings == []
    assert next(n for n in https["nodes"].values() if n["type"] == "scan")["url"].startswith("https://")
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://user:secret@cdn.example.com/a"):
        scene, notes = scene_from_spec(
            {"units": "mm", "elements": [{"id": "w", "type": "wall", "points": [[0, 0], [1000, 0]]}]},
            scan=bad,
        )
        scan = next(n for n in scene["nodes"].values() if n["type"] == "scan")
        assert scan["url"] is None
        assert "secret" not in json.dumps(scene) and "secret" not in json.dumps(notes)
        assert notes


def test_package_writes_ifc_with_ceiling_and_no_secrets(tmp_path):
    spec, _ = normalize_spec(_as_built())
    out = write_package(spec, tmp_path / "pkg", scan="https://cdn.example.com/room.glb")
    report = validate_ifc(out["ifc"])
    counts = report["counts"]
    assert counts["IfcWall"] == 1 and counts["IfcDoor"] == 1 and counts["IfcWindow"] == 1
    assert counts["IfcSlab"] == 1 and counts["IfcCovering"] == 1
    assert counts["IfcAnnotation"] == 1
    model = ifcopenshell.open(str(out["ifc"]))
    assert model.by_type("IfcCovering")[0].PredefinedType == "CEILING"
    connection = json.loads(out["connection"].read_text(encoding="utf-8"))
    assert connection["pascal"] == PASCAL_REPO
    assert connection["editor"] == EDITOR_COMMAND
    assert connection["mcp"]["mcpServers"]["pascal"]["command"] == MCP_COMMAND[0]
    assert connection["mcp"]["mcpServers"]["pascal"]["args"] == MCP_COMMAND[1:]
    blob = out["connection"].read_text(encoding="utf-8") + out["scene"].read_text(encoding="utf-8")
    assert "PASCAL_API_KEY" not in blob and "Bearer " not in blob and "change-me" not in blob
    assert out["spec"].exists()


def test_upper_storey_stacks_on_the_lower_height():
    spec = {"units": "mm", "elements": [
        {"id": "low", "type": "wall", "storey": "Ground", "points": [[0, 0], [2000, 0]], "height": 2700, "z": 0},
        {"id": "up", "type": "wall", "storey": "First", "points": [[0, 0], [2000, 0]], "height": 2700, "z": 3000},
    ]}
    scene, _ = scene_from_spec(spec)
    levels = sorted((n for n in scene["nodes"].values() if n["type"] == "level"), key=lambda n: n["level"])
    assert levels[0]["name"] == "Ground" and levels[0]["height"] == pytest.approx(3)
    assert levels[0]["baseElevation"] == pytest.approx(0)
    assert levels[1]["name"] == "First" and levels[1]["baseElevation"] == pytest.approx(0)
    assert levels[1]["metadata"]["sketch2cad"]["elevation"] == pytest.approx(3)
