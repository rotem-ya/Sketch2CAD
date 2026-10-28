"""Final versions filed from outside the app, and the house style learned from them.

The DXFs here are built in the test. Nothing from a real project is stored in the repository.
"""
import hashlib
import json
import os
import re
from pathlib import Path

import ezdxf
import pytest

from sketch2cad.bridge import export_package
from sketch2cad.config import Settings
from sketch2cad.documents import Document, create_document
from sketch2cad.finals import lock_path, newest_dwg, register_final
from sketch2cad.io import oda
from sketch2cad.learning import (
    PROFILE_VERSION, apply_profile, forget_final, house_style_text, layer_map_from, learned_lines,
    load_profile, normalize_layer, rebuild_profile, style_enabled, _as_dxf, _matched_pairs,
)
from sketch2cad.projects import Project, create_project


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def _lines(layer: str, count: int = 5):
    return [("LINE", layer, ((0, index * 10), (100, index * 10))) for index in range(count)]


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
    for index, style in enumerate(dimstyles):
        doc.dimstyles.duplicate_entry("EZDXF", style)
        doc.modelspace().add_linear_dim(
            base=(0, 8 + index), p1=(index, 0), p2=(5 + index, 0), dimstyle=style)
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
        layers=[("NEW", 1, 25, "CONTINUOUS"), ("EXISTING", 8, 25, "DASHED"), ("FRAME", 7, 25, "CONTINUOUS")],
        entities=_lines("NEW") + [("LINE", "EXISTING", ((0, 5), (40, 5)))],
        texts=[("ניקוז", 2.5), ("DRAIN", 2.5)], frame=True, paper="420x297",
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 250, "units": "mm"}), encoding="utf-8")
    doc.save()
    first = _dxf(
        tmp_path / "one.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW") + [("LINE", "EXISTING", ((0, 5), (40, 5)))],
        texts=[("ניקוז", 2.5)], paper="420x297",
    )
    vowel = "\u05b0$$$-TD"
    second = _dxf(
        tmp_path / "two.dxf", units="mm", ltscale=1000,
        layers=[
            (vowel, 150, 35, "CONTINUOUS"),
            ("$$$-DP400", 1, 35, "CONTINUOUS"),
            ("$$$-PVC200", 3, 35, "CONTINUOUS"),
            ("C-STRM-MAIN-PIPE", 4, 30, "NEW_DRAIN"),
        ],
        entities=_lines(vowel) + [("LINE", "C-STRM-MAIN-PIPE", ((0, 5), (40, 5)))],
        dimstyles=("ROTEM-250", "ROTEM-100"),
        texts=[("MANHOLE", 3.5)],
        ctb="Screening 100%.ctb", paper="420x297",
    )
    register_final(doc, name="one.dxf", data=first.read_bytes(), project=project)
    register_final(doc, name="two.dxf", data=second.read_bytes(), project=project)
    profile = load_profile(project)
    assert profile["scope"] == "project"
    assert profile["final_count"] == 2
    assert profile["ltscale"]["value"] == "1000.0"
    assert profile["ltscale"]["counts"]["250.0"] == 1
    assert profile["ltscale"]["counts"]["1000.0"] == 2
    assert profile["ltscale_by_scale"]["250|model"]["value"] == "1000.0"
    assert "250|paper" not in profile["ltscale_by_scale"]
    assert profile["units"]["value"] == "mm"
    assert "scale_guess" not in profile
    assert profile["language"]["value"] == "en"
    assert profile["plot_style"]["value"] == "Screening 100%.ctb"
    assert normalize_layer(vowel) == "$$$-TD"
    assert profile["layer_map"]["new"]["name"] == vowel
    assert profile["layer_map"]["new"]["color"] == 150
    existing = profile["layer_map"].get("existing")
    assert existing is None or existing["name"] != vowel
    assert "ROTEM-250" in profile["dimstyle"]["counts"]
    assert "ROTEM-100" in profile["dimstyle"]["counts"]
    assert "dimlfac" not in (profile["dimstyle"].get("vars") or {})
    assert profile["title_block"]["value"] is False
    assert not (home / "learning").exists()
    examples = list((project.path / "learning" / "examples").glob("*.json"))
    assert len(examples) == 2
    summaries = [json.loads(path.read_text(encoding="utf-8"))["summary"] for path in examples]
    summary = next(item for item in summaries if any("MANHOLE" in text for text in item["texts_added"]))
    assert "FRAME" in summary["layers_removed"] or "NEW" in summary["layers_removed"]
    assert summary["title_block_before"] is True
    assert summary["title_block_after"] is False
    assert any("MANHOLE" in text for text in summary["texts_added"])
    assert any("ביטחון" in line and "5/" in line for line in learned_lines(profile))


def test_applying_the_profile_keeps_counts_and_coordinates(tmp_path, home):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$LTSCALE"] = 1
    doc.layers.add("NEW", color=7, linetype="DASHED")
    doc.modelspace().add_line((0, 0), (12, 4), dxfattribs={"layer": "NEW", "linetype": "DASHED"})
    hidden = doc.modelspace().add_line((1, 1), (2, 8), dxfattribs={"layer": "NEW"})
    hidden.dxf.invisible = 1
    doc.styles.add("USED", font="arial.ttf")
    doc.modelspace().add_text("KEEP", dxfattribs={"height": 2.5, "insert": (3, 3), "layer": "NEW", "style": "USED"})
    before_geom = _geom(doc)
    before_counts = {}
    for entity in doc.modelspace():
        before_counts[entity.dxftype()] = before_counts.get(entity.dxftype(), 0) + 1
    profile = {
        "final_count": 1,
        "scope": "project",
        "profile_version": PROFILE_VERSION,
        "ltscale_by_scale": {"250|model": {"value": "1000", "verified": True}},
        "layer_map": {
            "NEW": {"source": "NEW", "name": "$$$-DP400", "color": 1, "lineweight": 35, "confident": True},
        },
        "font": {"value": str(tmp_path / "missing-font.ttf")},
        "dims_by_scale": {"250|model": {"name": "EZDXF", "vars": {"dimtxt": 99, "dimlfac": 0.001, "dimdec": 0},
                                       "verified": True}},
    }
    doc.dimstyles.get("EZDXF").dxf.dimtxt = 2.5
    assert apply_profile(doc, profile, sheet_scale=250) is True
    after_counts = {}
    for entity in doc.modelspace():
        after_counts[entity.dxftype()] = after_counts.get(entity.dxftype(), 0) + 1
    assert after_counts == before_counts
    assert _geom(doc) == before_geom
    assert doc.header["$LTSCALE"] == 1000
    assert {entity.dxf.layer for entity in doc.modelspace()} == {"$$$-DP400"}
    assert doc.layers.get("$$$-DP400").dxf.linetype == "DASHED"
    assert doc.layers.get("$$$-DP400").is_off() is False
    dashed = doc.modelspace().query("LINE").first
    assert dashed.dxf.linetype == "DASHED"
    assert hidden.dxf.invisible == 1
    assert doc.modelspace().query("TEXT").first.dxf.text == "KEEP"
    assert doc.styles.get("USED").dxf.font == "arial.ttf"
    assert doc.dimstyles.get("EZDXF").dxf.dimtxt == 99
    assert doc.dimstyles.get("EZDXF").dxf.dimlfac != 0.001
    other = ezdxf.new("R2018", setup=True)
    other.header["$LTSCALE"] = 62.5
    other.dimstyles.get("EZDXF").dxf.dimtxt = 2.5
    other.layers.add("NEW", color=7)
    other.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "NEW"})
    apply_profile(other, profile, sheet_scale=20)
    assert other.header["$LTSCALE"] == 62.5
    assert other.dimstyles.get("EZDXF").dxf.dimtxt == 2.5
    assert apply_profile(doc, {"final_count": 0}) is False


def _write_profile(project, profile: dict) -> None:
    folder = project.path / "learning"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "style_profile.json").write_text(json.dumps(profile), encoding="utf-8")


def test_agent_package_rules_follow_the_project_style_switch(tmp_path, home):
    """Rules only while style is on. An observation never carries a mismatched scale or a weak map."""
    project, doc = _project(tmp_path)
    profile = {
        "profile_version": PROFILE_VERSION,
        "final_count": 2,
        "scope": "project",
        "ltscale": {"value": "1000"},
        "ltscale_by_scale": {"250|model": {"value": "250", "verified": True}},
        "dims_by_scale": {"250|model": {"name": "ROTEM-250", "vars": {"dimtxt": 2.5}, "verified": True}},
        "layer_map": {
            "new": {"source": "NEW", "name": "SITE", "color": 3, "lineweight": 30,
                    "confident": True, "matched": 6, "total": 6},
            "text": {"source": "TEXT", "name": "NOTES", "color": 7, "lineweight": 18,
                     "confident": False, "matched": 1, "total": 149},
        },
        "observations": [{
            "units": "m", "ltscale": 1000.0, "reason": "units differ", "comparable": False,
            "layers": {"SITE-PIPE": {"name": "SITE-PIPE", "color": 3, "lineweight": 30}},
            "dimstyles": ["ROTEM-100"], "font": "arial.ttf", "language": "en",
            "title_block": False, "plot_style": "Screening 100%.ctb",
        }],
    }
    _write_profile(project, profile)
    example = {"code": doc.code, "rev": "A", "comparable": True,
               "summary": {"layers_added": ["$$$-DP400"], "layers_removed": ["FRAME"],
                           "texts_added": ["MANHOLE"], "title_block_after": False}}
    observed = {"code": doc.code, "rev": "A", "comparable": False,
                "summary": {"layers_added": ["OBSERVE-ONLY"], "layers_removed": [],
                            "texts_added": [], "title_block_after": False}}
    examples = project.path / "learning" / "examples"
    examples.mkdir()
    (examples / f"{doc.code}_1.json").write_text(json.dumps(example), encoding="utf-8")
    (examples / f"{doc.code}_2.json").write_text(json.dumps(observed), encoding="utf-8")

    on = (export_package(doc.path, tmp_path / "on") / "AGENT.md").read_text(encoding="utf-8")
    assert "How Rotem finishes drawings" in on
    assert "Layer NEW -> SITE" in on
    assert "LTSCALE at 250|model: 250" in on
    assert "Dimension style at 250|model: ROTEM-250" in on
    assert "$$$-DP400" in on
    assert "OBSERVE-ONLY" not in on
    assert "Change only what the request and the markings ask for." in on
    assert "Observed in Rotem's finals (for context; do not mechanically restyle)" in on
    assert "SITE-PIPE" in on
    assert "NOTES" not in on
    assert "1000" not in on
    assert "ROTEM-100" not in on

    project.apply_house_style = False
    project.save()
    off = (export_package(doc.path, tmp_path / "off") / "AGENT.md").read_text(encoding="utf-8")
    assert "How Rotem finishes drawings" not in off
    assert "$$$-DP400" not in off
    assert "NOTES" not in off
    assert "1000" not in off
    assert "ROTEM-100" not in off
    assert "Observed in Rotem's finals (for context; do not mechanically restyle)" in off
    assert "Comparable finals, recorded only as context:" in off
    assert "Layer NEW -> SITE" in off
    assert "LTSCALE at 250|model: 250" in off
    assert "Dimension style at 250|model: ROTEM-250" in off
    assert "SITE-PIPE" in off
    assert "Change only what the request and the markings ask for." in off


def test_project_style_override_leaves_other_projects_alone(tmp_path, home):
    one, _doc = _project(tmp_path)
    two = create_project(tmp_path / "jobs", "20118", "אחר", "Other")
    for project in (one, two):
        _write_profile(project, {"final_count": 1, "scope": "project", "profile_version": PROFILE_VERSION})
    assert one.apply_house_style is None
    assert style_enabled(one) is True
    assert style_enabled(two) is True
    one.apply_house_style = False
    one.save()
    saved = Project.load(one.path)
    assert saved.apply_house_style is False
    assert style_enabled(saved) is False
    assert style_enabled(Project.load(two.path)) is True
    assert "כבוי בפרויקט זה" in "\n".join(learned_lines(project=saved))
    assert "לפי ההגדרה הכללית" in "\n".join(learned_lines(project=two))
    settings = Settings.load()
    settings.apply_house_style = False
    settings.save()
    assert style_enabled(Project.load(two.path)) is False
    two.apply_house_style = True
    two.save()
    assert style_enabled(Project.load(two.path)) is True
    assert style_enabled(Project.load(one.path)) is False
    bare = json.loads((two.path / "project.json").read_text(encoding="utf-8"))
    bare.pop("apply_house_style")
    (two.path / "project.json").write_text(json.dumps(bare), encoding="utf-8")
    assert Project.load(two.path).apply_house_style is None
    assert style_enabled(Project.load(two.path)) is False


def _mask(text: str) -> str:
    """Drop handles and time stamps so two saves of the same drawing compare equal."""
    volatile = {"$HANDSEED", "$TDCREATE", "$TDUPDATE", "$TDINDWG", "$TDUSRTIMER",
                "$VERSIONGUID", "$FINGERPRINTGUID"}
    lines = text.splitlines()
    out = []
    index = 0
    while index < len(lines):
        code = lines[index].strip()
        value = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if code in {"5", "105", "330", "360"}:
            out.extend([lines[index], "0"])
            index += 2
            continue
        if code == "9" and value in volatile:
            index += 4
            continue
        out.append(lines[index])
        index += 1
    text = "\n".join(out)
    text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d+(?:\+\d{2}:\d{2})?", "TIME", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "TIME", text)
    return text


def test_an_unchanged_final_changes_nothing(tmp_path, home):
    project, doc = _project(tmp_path)
    _dxf(doc.path / "sheet.dxf", units="mm", ltscale=250,
         layers=[("NEW", 1, 35, "DASHED"), ("EXISTING", 8, 25, "DASHED")],
         entities=_lines("NEW") + [("LINE", "EXISTING", ((0, 5), (40, 5)))],
         texts=[("NOTE", 2.5)])
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 250}), encoding="utf-8")
    doc.save()
    register_final(doc, name="sheet.dxf", data=(doc.path / "sheet.dxf").read_bytes(), project=project)
    fresh = ezdxf.readfile(doc.path / "sheet.dxf")
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    fresh.saveas(before)
    loaded = ezdxf.readfile(before)
    apply_profile(loaded, load_profile(project), sheet_scale=250)
    loaded.saveas(after)
    assert _mask(before.read_text(encoding="utf-8", errors="replace")) == _mask(
        after.read_text(encoding="utf-8", errors="replace"))


def test_single_sheet_and_multiview_at_the_same_scale_stay_apart(tmp_path, home):
    project, doc = _project(tmp_path)
    _dxf(doc.path / "sheet.dxf", units="mm", ltscale=1250,
         layers=[("NEW", 1, 25, "CONTINUOUS")],
         entities=_lines("NEW"))
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 500, "units": "mm"}), encoding="utf-8")
    doc.save()
    register_final(doc, name="model.dxf", data=(doc.path / "sheet.dxf").read_bytes(), project=project)
    paper = _dxf(tmp_path / "paper.dxf", units="mm", ltscale=2.5,
                 layers=[("NEW", 1, 25, "CONTINUOUS")],
                 entities=_lines("NEW"))
    (doc.path / "spec.json").write_text(json.dumps({
        "scale": 500, "units": "mm", "views": [{"id": "side", "scale": 50}],
    }), encoding="utf-8")
    doc.outputs = {"dxf": "paper.dxf"}
    (doc.path / "paper.dxf").write_bytes(paper.read_bytes())
    doc.save()
    register_final(doc, name="paper.dxf", source=doc.path / "paper.dxf", project=project)
    profile = load_profile(project)
    assert profile["ltscale_by_scale"]["500|model"]["value"] == "1250.0"
    assert profile["ltscale_by_scale"]["500|paper"]["value"] == "2.5"
    single = ezdxf.readfile(doc.path / "sheet.dxf")
    multi = ezdxf.readfile(doc.path / "paper.dxf")
    apply_profile(multi, profile, sheet_scale=500, sheet_kind="paper")
    apply_profile(single, profile, sheet_scale=500, sheet_kind="model")
    assert multi.header["$LTSCALE"] == 2.5
    assert single.header["$LTSCALE"] == 1250


def test_swapping_dash_layers_keeps_the_sets_apart():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("NEW", color=1, linetype="CONTINUOUS")
    doc.layers.add("NEW_DASH", color=1, linetype="DASHED")
    solid = doc.modelspace().add_line((0, 0), (12, 0), dxfattribs={"layer": "NEW"})
    dashed = doc.modelspace().add_line((0, 4), (16, 4), dxfattribs={"layer": "NEW_DASH"})
    profile = {
        "final_count": 1,
        "layer_map": {
            "NEW": {"source": "NEW", "name": "NEW_DASH", "color": 1, "lineweight": 35, "confident": True},
            "NEW_DASH": {"source": "NEW_DASH", "name": "NEW", "color": 1, "lineweight": 25, "confident": True},
        },
    }
    apply_profile(doc, profile)
    assert solid.dxf.layer == "NEW_DASH"
    assert dashed.dxf.layer == "NEW"
    assert dashed.dxf.linetype == "DASHED"
    assert solid.dxf.linetype == "CONTINUOUS"
    assert sum(1 for entity in doc.modelspace() if entity.dxf.layer == "NEW") == 1
    assert sum(1 for entity in doc.modelspace() if entity.dxf.layer == "NEW_DASH") == 1


def test_a_target_that_is_an_unmapped_app_layer_is_skipped():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("NEW", color=1, linetype="CONTINUOUS")
    doc.layers.add("EXISTING", color=8, linetype="DASHED")
    line = doc.modelspace().add_line((0, 0), (4, 0), dxfattribs={"layer": "NEW", "linetype": "DASHED"})
    kept = doc.modelspace().add_line((0, 1), (4, 1), dxfattribs={"layer": "EXISTING"})
    profile = {
        "final_count": 1,
        "layer_map": {
            "NEW": {"source": "NEW", "name": "EXISTING", "color": 8, "lineweight": 25, "confident": True},
        },
    }
    apply_profile(doc, profile)
    assert line.dxf.layer == "NEW"
    assert line.dxf.linetype == "DASHED"
    assert kept.dxf.layer == "EXISTING"
    assert doc.layers.get("NEW").is_off() is False
    assert doc.layers.get("EXISTING").is_off() is False


def test_paper_space_text_follows_the_layer_mapping():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("TEXT", color=7)
    paper = doc.layouts.new("SHEET")
    text = paper.add_text("TITLE", dxfattribs={"layer": "TEXT", "height": 2.5, "insert": (1, 1)})
    profile = {
        "final_count": 1,
        "layer_map": {
            "TEXT": {"source": "TEXT", "name": "NOTES", "color": 7, "lineweight": 18, "confident": True},
            "UNUSED": {"source": "UNUSED", "name": "NOWHERE", "color": 1, "lineweight": 25, "confident": True},
        },
    }
    apply_profile(doc, profile)
    assert text.dxf.layer == "NOTES"
    assert "NOTES" in doc.layers
    assert "NOWHERE" not in doc.layers


def test_dimstyle_is_the_one_dimensions_use(tmp_path, home):
    project, doc = _project(tmp_path)
    drawing = ezdxf.new("R2018", setup=True)
    drawing.dimstyles.duplicate_entry("EZDXF", "SKETCH2CAD_50")
    drawing.dimstyles.duplicate_entry("EZDXF", "SKETCH2CAD")
    drawing.layers.add("NEW", color=1)
    for index in range(5):
        drawing.modelspace().add_line((0, index * 10), (100, index * 10), dxfattribs={"layer": "NEW"})
    drawing.modelspace().add_linear_dim(
        base=(0, 3), p1=(0, 0), p2=(10, 0), dimstyle="SKETCH2CAD")
    path = doc.path / "sheet.dxf"
    drawing.saveas(path)
    doc.outputs = {"dxf": "sheet.dxf"}
    doc.save()
    register_final(doc, name="sheet.dxf", data=path.read_bytes(), project=project)
    profile = load_profile(project)
    assert profile["dimstyle"]["value"] == "SKETCH2CAD"
    assert "SKETCH2CAD_50" not in profile["dimstyle"]["counts"]


def _referenced_layers(doc) -> set[str]:
    names = set()
    for entity in doc.entitydb.values():
        if getattr(entity, "is_alive", True) and entity.dxf.hasattr("layer"):
            names.add(str(entity.dxf.layer))
    return names


def _assert_referenced_layers_exist(doc) -> None:
    table = {doc.layers.key(layer.dxf.name) for layer in doc.layers}
    missing = [name for name in _referenced_layers(doc) if doc.layers.key(name) not in table]
    assert missing == []


def test_dimension_blocks_follow_a_dim_layer_rename():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("DIM", color=7, lineweight=18)
    dim = doc.modelspace().add_linear_dim(
        base=(0, 5), p1=(0, 0), p2=(10, 0), dimstyle="EZDXF", dxfattribs={"layer": "DIM"})
    dim.render()
    before = len(list(doc.layers))
    profile = {
        "final_count": 1,
        "layer_map": {"DIM": {"source": "DIM", "name": "$$$-DIM", "color": 30, "lineweight": 35, "confident": True}},
    }
    apply_profile(doc, profile)
    assert dim.dimension.dxf.layer == "$$$-DIM"
    block_layers = {entity.dxf.layer for block in doc.blocks for entity in block
                    if entity.dxf.hasattr("layer") and doc.layers.key(entity.dxf.layer) not in {"0", "defpoints"}}
    assert "$$$-DIM" in block_layers
    assert "DIM" not in {doc.layers.key(name) for name in block_layers}
    _assert_referenced_layers_exist(doc)
    assert len(list(doc.layers)) >= before


def test_case_only_layer_rename_keeps_the_layer():
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("TEXT", color=7, lineweight=18)
    text = doc.modelspace().add_text("Hello", dxfattribs={"layer": "TEXT", "height": 2.5, "insert": (0, 0)})
    before = {layer.dxf.handle for layer in doc.layers}
    profile = {
        "final_count": 1,
        "layer_map": {"TEXT": {"source": "TEXT", "name": "Text", "color": 150, "lineweight": 25, "confident": True}},
    }
    apply_profile(doc, profile)
    layer = doc.layers.get("Text")
    assert layer.dxf.name == "Text"
    assert layer.dxf.color == 150
    assert layer.dxf.handle in before
    assert text.dxf.layer == "Text"
    assert len(list(doc.layers)) == len(before)
    _assert_referenced_layers_exist(doc)


def test_synthetic_finals_never_point_at_a_missing_layer(tmp_path, home):
    """Every layer named by a layout or a block still exists after style is applied."""
    project, doc = _project(tmp_path)
    sheet = _dxf(doc.path / "sheet.dxf", units="mm", ltscale=2.5,
                 layers=[("NEW", 1, 25, "CONTINUOUS"), ("DIM", 7, 18, "CONTINUOUS"),
                         ("TEXT", 7, 18, "CONTINUOUS")],
                 entities=_lines("NEW"), texts=[("NOTE", 2.5)])
    drawing = ezdxf.readfile(sheet)
    drawing.modelspace().add_linear_dim(
        base=(0, 5), p1=(0, 0), p2=(10, 0), dimstyle="EZDXF", dxfattribs={"layer": "DIM"}).render()
    drawing.saveas(sheet)
    doc.outputs = {"dxf": "sheet.dxf"}
    doc.save()
    finals = [
        ("same.dxf", sheet.read_bytes(), {"NEW": "NEW", "DIM": "DIM", "TEXT": "TEXT"}),
        ("dim.dxf", None, {"DIM": "$$$-DIM"}),
        ("text.dxf", None, {"TEXT": "Text"}),
        ("dash.dxf", None, {"NEW": "NEW_DASH"}),
    ]
    built = []
    for name, data, mapping in finals:
        if data is None:
            edited = ezdxf.readfile(sheet)
            for source, target in mapping.items():
                if source == target:
                    continue
                if target not in edited.layers:
                    edited.layers.add(target, color=150, lineweight=35)
                for entity in list(edited.entitydb.values()):
                    if entity.dxf.hasattr("layer") and edited.layers.key(entity.dxf.layer) == edited.layers.key(source):
                        entity.dxf.layer = target
            path = tmp_path / name
            edited.saveas(path)
            data = path.read_bytes()
        register_final(doc, name=name, data=data, project=project)
        applied = ezdxf.readfile(sheet)
        apply_profile(applied, load_profile(project))
        _assert_referenced_layers_exist(applied)
        built.append(applied)
    assert len(built) == 4


def test_final_that_is_off_does_not_turn_a_layer_off(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(doc.path / "sheet.dxf", units="mm", ltscale=1,
               layers=[("NEW", 1, 25, "CONTINUOUS")],
               entities=_lines("NEW"))
    doc.outputs = {"dxf": "sheet.dxf"}
    doc.save()
    final = ezdxf.readfile(app)
    final.layers.get("NEW").off()
    final_path = tmp_path / "off.dxf"
    final.saveas(final_path)
    register_final(doc, name="off.dxf", data=final_path.read_bytes(), project=project)
    target = ezdxf.readfile(app)
    assert target.layers.get("NEW").is_off() is False
    apply_profile(target, load_profile(project), sheet_scale=None)
    assert target.layers.get("NEW").is_off() is False


def test_font_comes_from_the_style_the_text_uses(tmp_path, home):
    from sketch2cad.drawing.text import _BUNDLED_FONT
    from sketch2cad.learning import _font_in_use

    doc = ezdxf.new("R2018", setup=True)
    doc.styles.add("UNUSED", font="missing-first.ttf")
    doc.styles.add("USED", font=str(_BUNDLED_FONT))
    doc.modelspace().add_text("HELLO", dxfattribs={"style": "USED", "height": 2.5, "insert": (0, 0)})
    assert _font_in_use(doc) == str(_BUNDLED_FONT)
    assert _font_in_use(doc) != "missing-first.ttf"


def test_reading_the_profile_does_not_create_the_learning_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    project, _doc = _project(tmp_path)
    assert load_profile(project)["final_count"] == 0
    assert not (tmp_path / "home" / "learning").exists()
    assert not (project.path / "learning").exists()


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


def _force_handle(path, kind, handle):
    drawing = ezdxf.readfile(path)
    for entity in drawing.modelspace():
        if entity.dxftype() != kind:
            continue
        old = entity.dxf.handle
        if old in drawing.entitydb:
            del drawing.entitydb[old]
        entity.dxf.handle = handle
        drawing.entitydb[handle] = entity
        drawing.saveas(path)
        return
    raise AssertionError(kind)


def test_metres_redraw_is_observation_only_and_changes_nothing(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW"), texts=[("NOTE", 2.5)],
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 100, "units": "mm"}), encoding="utf-8")
    doc.save()
    final = _dxf(
        tmp_path / "metres.dxf", units="m", ltscale=1000,
        layers=[("SITE-PIPE", 3, 30, "CONTINUOUS"), ("TEXT", 7, 18, "CONTINUOUS")],
        entities=[("LINE", "SITE-PIPE", ((200000, 500000), (200100, 500000)))],
        dimstyles=("ROTEM-100",),
        texts=[("MANHOLE", 2.5)],
        ctb="Screening 100%.ctb",
    )
    register_final(doc, name="metres.dxf", data=final.read_bytes(), project=project)
    profile = load_profile(project)
    assert profile["final_count"] == 1
    assert profile.get("layer_map") in (None, {})
    assert "ltscale_by_scale" not in profile or "100|model" not in profile["ltscale_by_scale"]
    assert not profile.get("ltscale_by_scale")
    assert not profile.get("dims_by_scale")
    assert "scale_guess" not in profile
    observed = profile["observations"][0]
    assert observed["comparable"] is False
    assert observed["reason"] == "units differ"
    assert observed["units"] == "m"
    assert observed["ltscale"] == 1000.0
    assert observed["layers"]["SITE-PIPE"]["color"] == 3
    assert observed["layers"]["SITE-PIPE"]["lineweight"] == 30
    assert "ROTEM-100" in observed["dimstyles"]
    assert observed["language"] == "en"
    assert observed["title_block"] is False
    assert observed["plot_style"] == "Screening 100%.ctb"
    panel = "\n".join(learned_lines(profile))
    assert "תצפית בלבד" in panel
    assert "תצפית שכבות" in panel and "SITE-PIPE" in panel
    assert "תצפית מידות" in panel and "ROTEM-100" in panel
    assert "100|model" not in panel
    package = house_style_text(profile, project=project)
    assert "Observed in Rotem's finals (for context; do not mechanically restyle)" in package
    assert "SITE-PIPE" in package
    assert "1000" not in package
    assert "ROTEM-100" not in package
    assert "How Rotem finishes drawings" not in package
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    ezdxf.readfile(app).saveas(before)
    loaded = ezdxf.readfile(before)
    assert apply_profile(loaded, profile, sheet_scale=100, sheet_kind="model") is False
    loaded.saveas(after)
    assert _mask(before.read_text(encoding="utf-8", errors="replace")) == _mask(
        after.read_text(encoding="utf-8", errors="replace"))


def test_one_shared_handle_with_different_geometry_is_not_a_mapping(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW", 6) + [("LWPOLYLINE", "NEW", [(0, 0), (10, 0), (10, 10)])],
    )
    final = _dxf(
        tmp_path / "redraw.dxf", units="mm", ltscale=1000,
        layers=[("TEXT", 7, 18, "CONTINUOUS")],
        entities=[("LWPOLYLINE", "TEXT", [(500, 500), (800, 500), (800, 900)])],
    )
    _force_handle(app, "LWPOLYLINE", "FFFF")
    _force_handle(final, "LWPOLYLINE", "FFFF")
    app_doc, final_doc = ezdxf.readfile(app), ezdxf.readfile(final)

    def _polyline(drawing):
        return next(entity for entity in drawing.modelspace() if entity.dxftype() == "LWPOLYLINE")

    assert _polyline(app_doc).dxf.handle == "FFFF"
    assert _polyline(final_doc).dxf.handle == "FFFF"
    assert _matched_pairs(app_doc, final_doc) == []
    mapped = layer_map_from(app_doc, final_doc)
    assert all(str(info.get("name")).upper() != "TEXT" for info in mapped.values())
    assert not any(info.get("confident") for info in mapped.values())
    doc.outputs = {"dxf": "sheet.dxf"}
    doc.save()
    register_final(doc, name="redraw.dxf", data=final.read_bytes(), project=project)
    profile = load_profile(project)
    assert not profile.get("layer_map")
    assert profile["observations"][0]["comparable"] is False


def test_a_minority_of_a_layer_is_not_applied(tmp_path, home):
    project, doc = _project(tmp_path)
    app = _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW", 12),
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 250, "units": "mm"}), encoding="utf-8")
    doc.save()
    final = _dxf(
        tmp_path / "few.dxf", units="mm", ltscale=250,
        layers=[("SITE", 150, 35, "CONTINUOUS")],
        entities=_lines("SITE", 5),
    )
    register_final(doc, name="few.dxf", data=final.read_bytes(), project=project)
    profile = load_profile(project)
    assert profile["observations"][0]["comparable"] is True
    assert profile["observations"][0]["matches"] == 5
    assert "new" not in (profile.get("layer_map") or {})
    target = ezdxf.readfile(app)
    apply_profile(target, profile, sheet_scale=250, sheet_kind="model")
    assert {entity.dxf.layer for entity in target.modelspace()} == {"NEW"}


def test_forgetting_a_final_rebuilds_the_profile(tmp_path, home):
    project, doc = _project(tmp_path)
    _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW"),
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 250, "units": "mm"}), encoding="utf-8")
    doc.save()
    kept = _dxf(
        tmp_path / "kept.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW"),
    )
    renamed = _dxf(
        tmp_path / "renamed.dxf", units="mm", ltscale=1000,
        layers=[("SITE", 150, 35, "CONTINUOUS")],
        entities=_lines("SITE"),
    )
    register_final(doc, name="kept.dxf", data=kept.read_bytes(), project=project)
    dropped, _warnings = register_final(doc, name="renamed.dxf", data=renamed.read_bytes(), project=project)
    assert load_profile(project)["layer_map"]["new"]["name"] == "SITE"
    forget_final(doc, project, dropped["file"])
    saved = Document.load(doc.path)
    assert len(saved.finals) == 1
    assert saved.finals[0]["file"] != dropped["file"]
    assert not (doc.path / dropped["file"]).is_file()
    profile = load_profile(project)
    assert profile["final_count"] == 1
    assert profile["layer_map"]["new"]["name"] == "NEW"
    assert profile["layer_map"]["new"]["confident"] is True
    assert profile["ltscale_by_scale"]["250|model"]["value"] == "250.0"
    assert profile["ltscale_by_scale"]["250|model"]["verified"] is True
    assert len(list((project.path / "learning" / "examples").glob("*.json"))) == 1


def _split_lines(counts):
    """App lines on NEW, the same geometry in the final split across A, B and C."""
    app = ezdxf.new("R2018", setup=True)
    final = ezdxf.new("R2018", setup=True)
    app.layers.add("NEW", color=1)
    index = 0
    for layer, count, color in counts:
        if layer not in [item.dxf.name for item in final.layers]:
            final.layers.add(layer, color=color, lineweight=25)
        for _ in range(count):
            start, end = (0, index), (100, index)
            app.modelspace().add_line(start, end, dxfattribs={"layer": "NEW"})
            final.modelspace().add_line(start, end, dxfattribs={"layer": layer})
            index += 1
    return app, final


def test_a_split_vote_does_not_move_the_layer():
    for counts in ((("A", 40, 1), ("B", 35, 3), ("C", 25, 5)),
                   (("A", 34, 1), ("B", 33, 3), ("C", 33, 5))):
        app, final = _split_lines(counts)
        mapped = layer_map_from(app, final)
        assert "new" not in mapped
    app, final = _split_lines((("A", 6, 1), ("B", 4, 3)))
    mapped = layer_map_from(app, final)
    assert mapped["new"]["name"] == "A"
    assert mapped["new"]["confident"] is True


def test_legacy_profile_is_rebuilt_before_use_and_is_not_applied(tmp_path, home):
    """A PR #13 file with no version: metres LTSCALE, ROTEM_M and font txt must not become rules."""
    project, doc = _project(tmp_path)
    app = _dxf(
        doc.path / "sheet.dxf", units="mm", ltscale=250,
        layers=[("NEW", 1, 25, "CONTINUOUS")],
        entities=_lines("NEW"), texts=[("NOTE", 2.5)],
    )
    doc.outputs = {"dxf": "sheet.dxf"}
    (doc.path / "spec.json").write_text(json.dumps({"scale": 100, "units": "mm"}), encoding="utf-8")
    doc.save()
    final = _dxf(
        tmp_path / "metres.dxf", units="m", ltscale=1000,
        layers=[("SITE-PIPE", 3, 30, "CONTINUOUS")],
        entities=[("LINE", "SITE-PIPE", ((200000, 500000), (200100, 500000)))],
        dimstyles=("ROTEM_M",), texts=[("MANHOLE", 2.5)],
    )
    register_final(doc, name="metres.dxf", data=final.read_bytes(), project=project)
    legacy = {
        "final_count": 1,
        "scope": "project",
        "units": {"value": "m", "counts": {"m": 1}},
        "ltscale": {"value": "1000.0", "counts": {"1000.0": 1}},
        "scale_guess": {"value": "341", "counts": {"341": 1}},
        "ltscale_by_scale": {"100|model": {"value": "1000.0", "counts": {"1000.0": 1}}},
        "dims_by_scale": {"100|model": {
            "name": "ROTEM_M",
            "vars": {"dimtxt": 250, "dimasz": 180, "dimexe": 60, "dimexo": 30, "dimgap": 30, "dimscale": 100},
            "counts": {"ROTEM_M": 1},
        }},
        "dimstyle": {"value": "ROTEM_M", "vars": {"dimtxt": 250}, "counts": {"ROTEM_M": 1}},
        "font": {"value": "txt", "counts": {"txt": 1}},
        "layer_map": {"new": {"source": "NEW", "name": "TEXT", "color": 7, "lineweight": 18, "count": 1}},
    }
    _write_profile(project, legacy)
    loaded = load_profile(project)
    assert loaded["profile_version"] == PROFILE_VERSION
    assert not loaded.get("ltscale_by_scale")
    assert not loaded.get("dims_by_scale")
    assert (loaded.get("font") or {}).get("verified") is not True
    assert (loaded.get("units") or {}).get("value") != "m"
    backup = (project.path / "learning" / "style_profile.json.bak").read_text(encoding="utf-8")
    assert "1000.0" in backup and "ROTEM_M" in backup and "txt" in backup
    assert load_profile(project)["profile_version"] == PROFILE_VERSION
    assert "ROTEM_M" in (project.path / "learning" / "style_profile.json.bak").read_text(encoding="utf-8")

    fresh = ezdxf.readfile(app)
    fresh.dimstyles.duplicate_entry("EZDXF", "ROTEM_M")
    fresh.dimstyles.get("ROTEM_M").dxf.dimtxt = 2.5
    assert apply_profile(fresh, legacy, sheet_scale=100, sheet_kind="model") is False
    assert fresh.header["$LTSCALE"] == 250
    assert fresh.dimstyles.get("ROTEM_M").dxf.dimtxt == 2.5
    direct = house_style_text(legacy, project=project)
    assert "How Rotem finishes drawings" not in direct
    assert "LTSCALE at 100|model" not in direct
    assert "ROTEM_M" not in direct
    assert "Font: txt" not in direct

    target = ezdxf.readfile(app)
    assert apply_profile(target, project=project, sheet_scale=100, sheet_kind="model") is False
    assert target.header["$LTSCALE"] == 250
    package = (export_package(doc.path, tmp_path / "package") / "AGENT.md").read_text(encoding="utf-8")
    assert "How Rotem finishes drawings" not in package
    assert "LTSCALE at 100|model" not in package
    assert "ROTEM_M" not in package
    assert "Font: txt" not in package
    lines = learned_lines(project=project)
    panel = "\n".join(lines)
    assert "יחידות: m" not in panel
    assert "LTSCALE 100|model" not in panel
    assert not any(line.startswith("LTSCALE") for line in lines)


def test_global_forget_keeps_finals_from_other_projects(tmp_path, home):
    one, doc1 = _project(tmp_path)
    two = create_project(tmp_path / "jobs", "20118", "אחר", "Other")
    doc2 = create_document(two, title_he="אחר", title_en="Other", discipline="D", subject="SITE")
    settings = Settings.load()
    settings.house_style_global = True
    settings.projects = [str(one.path), str(two.path)]
    settings.save()

    def _final(doc, project, layer):
        _dxf(doc.path / "sheet.dxf", units="mm", ltscale=250,
             layers=[("NEW", 1, 25, "CONTINUOUS")], entities=_lines("NEW"))
        doc.outputs = {"dxf": "sheet.dxf"}
        (doc.path / "spec.json").write_text(json.dumps({"scale": 250, "units": "mm"}), encoding="utf-8")
        doc.save()
        path = tmp_path / f"{layer}.dxf"
        _dxf(path, units="mm", ltscale=250, layers=[(layer, 3, 35, "CONTINUOUS")], entities=_lines(layer))
        return register_final(doc, name=f"{layer}.dxf", data=path.read_bytes(), project=project)

    _final(doc1, one, "NEW")
    dropped, _warnings = _final(doc2, two, "SITE")
    assert load_profile(one)["final_count"] == 2
    forget_final(doc2, two, dropped["file"])
    profile = load_profile(one)
    assert profile["final_count"] == 1
    assert profile["layer_map"]["new"]["name"] == "NEW"
    assert profile["scope"] == "global"


def _snapshot(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def _stored_dwg(folder, stem: str, payload: bytes):
    dwg = folder / f"20261020_120000_{stem}.dwg"
    dxf = folder / f"20261020_120000_{stem}.dxf"
    dwg.write_bytes(payload)
    drawing = ezdxf.new("R2018")
    drawing.modelspace().add_line((0, 0), (1, 0))
    drawing.saveas(dxf)
    entry = {
        "file": f"finals/{dwg.name}",
        "name": f"{stem}.dwg",
        "md5": hashlib.md5(payload).hexdigest(),
        "rev": "A",
    }
    return dwg, dxf, entry


def test_rebuild_and_forget_leave_existing_final_files_unchanged(tmp_path, home, monkeypatch):
    """A stored DXF next to an unchanged DWG is reused. Nothing already in finals/ is rewritten."""
    project, doc = _project(tmp_path)
    folder = doc.path / "finals"
    folder.mkdir()
    kept_dwg, kept_dxf, kept = _stored_dwg(folder, "kept", b"KEPT-DWG")
    drop_dwg, drop_dxf, dropped = _stored_dwg(folder, "drop", b"DROP-DWG")
    (folder / "note.txt").write_bytes(b"leave-me")
    doc.finals = [kept, dropped]
    doc.save()
    before = _snapshot(folder)

    def converted(*_args, **_kwargs):
        raise AssertionError("an unchanged final was converted again")

    monkeypatch.setattr(oda, "convert", converted)
    rebuild_profile(project)
    assert _snapshot(folder) == before
    forget_final(doc, project, dropped["file"])
    after = _snapshot(folder)
    assert drop_dwg.name not in after
    for name, data in before.items():
        if name == drop_dwg.name:
            continue
        assert after[name] == data
    assert kept_dxf.read_bytes() == before[kept_dxf.name]
    assert drop_dxf.read_bytes() == before[drop_dxf.name]


def test_a_changed_dwg_is_converted_to_a_new_file(tmp_path, monkeypatch):
    project, doc = _project(tmp_path)
    folder = doc.path / "finals"
    folder.mkdir()
    dwg, dxf, entry = _stored_dwg(folder, "plan", b"OLD-DWG")
    original = dxf.read_bytes()
    dwg.write_bytes(b"NEW-DWG")
    written = []

    def fake_convert(src, fmt, out_dir=None, **_kwargs):
        dest = Path(out_dir) / f"{Path(src).stem}.dxf"
        dest.write_bytes(b"CONVERTED")
        written.append(dest)
        return dest

    monkeypatch.setattr(oda, "convert", fake_convert)
    produced = _as_dxf(dwg, entry)
    assert produced is not None and produced != dxf
    assert produced.parent == folder
    assert produced.name != dxf.name
    assert produced.read_bytes() == b"CONVERTED"
    assert dxf.read_bytes() == original
    assert not any(path.parent == folder and path.name == dxf.name for path in written)
