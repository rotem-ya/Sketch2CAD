"""Final versions filed from outside the app, and the house style learned from them.

The DXFs here are built in the test. Nothing from a real project is stored in the repository.
"""
import json
import os

import ezdxf
import pytest

from sketch2cad.bridge import export_package
from sketch2cad.config import Settings
from sketch2cad.documents import Document, create_document
from sketch2cad.finals import lock_path, newest_dwg, register_final
from sketch2cad.io import oda
from sketch2cad.learning import apply_profile, house_style_text, load_profile, normalize_layer
from sketch2cad.projects import create_project


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _project(tmp_path):
    project = create_project(tmp_path / "jobs", "20117", "חצרים", "Hatzerim")
    doc = create_document(project, title_he="ניקוז", title_en="Drainage", discipline="D", subject="SITE")
    return project, doc


def _dxf(path, *, units, ltscale, layers, entities, dimstyles=(), texts=(), frame=False, ctb="", paper=""):
    doc = ezdxf.new("R2018", setup=True)
    doc.units = ezdxf.units.M if units == "m" else ezdxf.units.MM
    doc.header["$LTSCALE"] = ltscale
    if ctb:
        doc.header.custom_vars.append("CTB", ctb)
    for name, color, weight, linetype in layers:
        if linetype not in doc.linetypes:
            doc.linetypes.add(linetype, pattern=[0.5, 0.25, -0.25], description=linetype)
        doc.layers.add(name, color=color, lineweight=weight, linetype=linetype)
    if frame:
        if "FRAME" not in doc.layers:
            doc.layers.add("FRAME", color=7)
        doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "FRAME"})
    for kind, layer, geom in entities:
        attribs = {"layer": layer}
        if kind == "LINE":
            doc.modelspace().add_line(geom[0], geom[1], dxfattribs=attribs)
        elif kind == "LWPOLYLINE":
            doc.modelspace().add_lwpolyline(geom, dxfattribs=attribs)
    for style in dimstyles:
        doc.dimstyles.duplicate_entry("EZDXF", style)
    for text, height in texts:
        doc.modelspace().add_text(text, dxfattribs={"height": height, "layer": "TEXT", "insert": (1, 1)})
    if "TEXT" not in [layer.dxf.name for layer in doc.layers]:
        doc.layers.add("TEXT", color=7)
    if paper:
        width, height = paper.split("x")
        doc.layouts.get("Layout1").page_setup(size=(float(width), float(height)), margins=(0, 0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _geom(doc):
    rows = []
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind == "LINE":
            rows.append((kind, tuple(entity.dxf.start), tuple(entity.dxf.end), entity.dxf.text if False else ""))
        elif kind == "TEXT":
            rows.append((kind, entity.dxf.text, tuple(entity.dxf.insert)))
        else:
            rows.append((kind,))
    return rows


def test_register_final_keeps_outputs_and_warns_when_autocad_has_the_file_open(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(doc.path / "app.dxf", units="mm", ltscale=1, layers=[("NEW", 1, 35, "CONTINUOUS")],
               entities=[("LINE", "NEW", ((0, 0), (1, 0)))], texts=[("HELLO", 2.5)])
    doc.outputs = {"dxf": "app.dxf", "pdf": "app.pdf"}
    doc.save()
    before = dict(doc.outputs)
    folder = tmp_path / "GoogleDrive" / "פרויקט ניקוז"
    folder.mkdir(parents=True)
    older = folder / "ישן.dwg"
    older.write_bytes(b"old")
    os.utime(older, (1_700_000_000, 1_700_000_000))
    newer = folder / "תוכנית סופית.dwg"
    newer.write_bytes(b"new-dwg")
    assert newest_dwg(folder) == newer
    lock = newer.with_suffix(".dwl")
    lock.write_text("open", encoding="utf-8")
    assert lock_path(newer) == lock
    windows = r"E:\GoogleDrive\פרויקט ניקוז\תוכנית סופית.dwg"
    entry, warnings = register_final(doc, name="תוכנית סופית.dxf", data=app.read_bytes(), original_path=windows,
                                     note="נמסר", sent_to_client=True, project=project)
    assert warnings == []
    assert entry["original_path"] == windows
    assert entry["sent_to_client"] is True
    assert entry["rev"] == "A"
    assert (doc.path / entry["file"]).is_file()
    again, lock_warnings = register_final(doc, name="תוכנית סופית.dwg", source=newer, project=project)
    assert lock.name in lock_warnings
    assert again["file"] != entry["file"]
    assert len(list((doc.path / "finals").iterdir())) == 2
    saved = Document.load(doc.path)
    assert saved.outputs == before
    assert len(saved.finals) == 2
    assert "פרויקט" in saved.finals[0]["original_path"]


def test_profile_from_two_synthetic_finals_prefers_the_newer_one(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS"), ("FRAME", 7, 25, "CONTINUOUS")],
        entities=[("LINE", "NEW", ((0, 0), (100, 0)))],
        texts=[("ניקוז", 2.5), ("DRAIN", 2.5)], frame=True, paper="420x297",
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    doc.save()
    first = _dxf(
        tmp_path / "one.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=[("LINE", "NEW", ((0, 0), (5, 0)))],
        texts=[("ניקוז", 2.5)], paper="420x297",
    )
    vowel = "\u05b0$$$-TD"
    second = _dxf(
        tmp_path / "two.dxf", units="m", ltscale=1000,
        layers=[
            (vowel, 150, 35, "CONTINUOUS"),
            ("$$$-DP400", 1, 35, "CONTINUOUS"),
            ("$$$-PVC200", 3, 35, "CONTINUOUS"),
            ("C-STRM-MAIN-PIPE", 4, 30, "NEW_DRAIN"),
        ],
        entities=[
            ("LWPOLYLINE", "$$$-DP400", [(0, 0), (10, 0), (10, 2)]),
            ("LINE", "C-STRM-MAIN-PIPE", ((0, 1), (8, 1))),
        ],
        dimstyles=("ROTEM-250", "ROTEM-100"),
        texts=[("MANHOLE", 3.5)],
        ctb="Screening 100%.ctb", paper="420x297",
    )
    register_final(doc, name="one.dxf", data=first.read_bytes(), project=project)
    register_final(doc, name="two.dxf", data=second.read_bytes(), project=project)
    profile = load_profile()
    assert profile["final_count"] == 2
    assert profile["ltscale"]["value"] == "1000.0"
    assert profile["ltscale"]["counts"]["250.0"] == 1
    assert profile["ltscale"]["counts"]["1000.0"] == 2
    assert profile["units"]["value"] == "m"
    assert profile["language"]["value"] == "en"
    assert profile["plot_style"]["value"] == "Screening 100%.ctb"
    assert normalize_layer(vowel) == "$$$-TD"
    assert profile["layers"]["$$$-TD"]["name"] == vowel
    assert profile["layers"]["$$$-TD"]["color_value"] == "150"
    assert "ROTEM-250" in profile["dimstyle"]["counts"]
    assert "ROTEM-100" in profile["dimstyle"]["counts"]
    assert profile["title_block"]["value"] is False
    examples = list((home / "learning" / "examples").glob("*.json"))
    assert len(examples) == 2
    summaries = [json.loads(path.read_text(encoding="utf-8"))["summary"] for path in examples]
    summary = next(item for item in summaries if any("MANHOLE" in text for text in item["texts_added"]))
    assert "FRAME" in summary["layers_removed"] or "NEW" in summary["layers_removed"]
    assert summary["title_block_before"] is True
    assert summary["title_block_after"] is False
    assert any("MANHOLE" in text for text in summary["texts_added"])


def test_applying_the_profile_keeps_counts_and_coordinates(tmp_path, home):
    _project_unused = tmp_path
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$LTSCALE"] = 1
    doc.layers.add("NEW", color=7)
    doc.modelspace().add_line((0, 0), (12, 4), dxfattribs={"layer": "NEW"})
    doc.modelspace().add_line((1, 1), (2, 8), dxfattribs={"layer": "NEW"})
    doc.modelspace().add_text("KEEP", dxfattribs={"height": 2.5, "insert": (3, 3), "layer": "NEW"})
    before_geom = _geom(doc)
    before_counts = {}
    for entity in doc.modelspace():
        before_counts[entity.dxftype()] = before_counts.get(entity.dxftype(), 0) + 1
    profile = {
        "final_count": 1,
        "ltscale": {"value": "1000"},
        "units": {"value": "mm"},
        "layers": {
            "$$$-DP400": {"name": "$$$-DP400", "color_value": "1", "lineweight_value": "35",
                          "linetype_value": "CONTINUOUS", "counts": 1},
        },
        "kind_layer": {"LINE": {"$$$-DP400": 2}, "TEXT": {"$$$-DP400": 1}},
        "text_style": {"value": "ROTEM"},
    }
    assert apply_profile(doc, profile, spec_units="mm") is True
    after_counts = {}
    for entity in doc.modelspace():
        after_counts[entity.dxftype()] = after_counts.get(entity.dxftype(), 0) + 1
    assert after_counts == before_counts
    assert _geom(doc) == before_geom
    assert doc.header["$LTSCALE"] == 1000
    assert {entity.dxf.layer for entity in doc.modelspace()} == {"$$$-DP400"}
    assert doc.modelspace().query("TEXT").first.dxf.text == "KEEP"
    assert apply_profile(doc, {"final_count": 0}) is False


def test_agent_package_includes_the_house_style(tmp_path, home):
    project, doc = _project(tmp_path)
    (home / "learning").mkdir(parents=True)
    profile = {
        "final_count": 1,
        "ltscale": {"value": "1000"},
        "units": {"value": "m"},
        "language": {"value": "en"},
        "layers": {},
    }
    (home / "learning" / "style_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    example = {"code": doc.code, "rev": "A", "summary": {"layers_added": ["$$$-DP400"], "layers_removed": ["FRAME"],
                                                        "texts_added": ["MANHOLE"], "title_block_after": False}}
    (home / "learning" / "examples").mkdir()
    (home / "learning" / "examples" / f"{doc.code}_1.json").write_text(json.dumps(example), encoding="utf-8")
    out = export_package(doc.path, tmp_path / "package")
    text = (out / "AGENT.md").read_text(encoding="utf-8")
    assert "How Rotem finishes drawings" in text
    assert "$$$-DP400" in text
    assert "Change only what the request and the markings ask for." in text
    assert "1000" in house_style_text(profile, [example])


def test_oda_27_9_is_found_when_the_setting_is_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("ODA_CONVERTER", raising=False)
    monkeypatch.setattr(oda.shutil, "which", lambda name: None)
    root = tmp_path / "ODA"
    older = root / "ODAFileConverter 26.8.0" / "ODAFileConverter.exe"
    newer = root / "ODAFileConverter 27.9.0" / "ODAFileConverter.exe"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    monkeypatch.setattr(oda.os, "name", "nt")
    monkeypatch.setattr(oda, "EXPLICIT_CONVERTERS", (str(newer),))
    assert oda.find_converter(Settings()) == str(newer)
    monkeypatch.setattr(oda, "EXPLICIT_CONVERTERS", ())
    pattern = str(root / "ODAFileConverter*" / "ODAFileConverter.exe")
    monkeypatch.setattr(oda, "WINDOWS_GLOBS", (pattern,))
    assert oda.find_converter(Settings()) == str(newer)
