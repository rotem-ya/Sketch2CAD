"""House style learned from finals the engineer finished outside the app.

The profile is counts, weighted toward the newer final. Applying it changes layers, styles
and header variables only. Entity counts, text and coordinates stay as the renderer drew them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import Settings, app_home
from .documents import Document

NIQQUD = re.compile(r"[\u0591-\u05C7]")
HEBREW = re.compile(r"[\u0590-\u05FF]")
LATIN = re.compile(r"[A-Za-z]")
PROFILE_NAME = "style_profile.json"
PROFILE_VERSION = 2
_INSUNITS = {4: "mm", 6: "m", 1: "in", 5: "cm"}
_TITLE_LAYERS = {"FRAME", "TITLE", "TITLEBLOCK", "SHEET"}
_DIMVARS = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap", "dimscale", "dimlfac", "dimdec", "dimtxsty", "dimtad")


def _use_global(settings: Settings | None = None) -> bool:
    settings = settings or Settings.load()
    return bool(settings.house_style_global)


def learning_root(project=None, *, settings: Settings | None = None, create: bool = False) -> Path | None:
    """Where this profile lives. Reading must not create the folder.

    A project profile is the default. The data-folder profile is used only when that
    opt-in is on. With no project and no opt-in there is nowhere to look.
    """
    if _use_global(settings):
        path = app_home() / "learning"
    elif project is None:
        return None
    else:
        path = Path(project.path) / "learning"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path(project=None, *, settings: Settings | None = None, create: bool = False) -> Path | None:
    root = learning_root(project, settings=settings, create=create)
    if root is None:
        return None
    return root / PROFILE_NAME


def normalize_layer(name: str) -> str:
    """Drop Hebrew vowel marks so ``\\u05b0$$$-TD`` matches ``$$$-TD``. The original is kept elsewhere."""
    return NIQQUD.sub("", name or "").strip()


def _layer_key(name: str) -> str:
    """Identity of a DXF layer: vowel marks ignored, and names are case-insensitive."""
    return normalize_layer(name).casefold()


def _empty_profile(settings: Settings | None = None) -> dict:
    return {"final_count": 0, "scope": "global" if _use_global(settings) else "project",
            "profile_version": PROFILE_VERSION}


def _read_profile(path: Path, settings: Settings | None = None) -> dict:
    empty = _empty_profile(settings)
    try:
        from .procutil import read_text
        data = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("final_count", 0)
    data.setdefault("scope", empty["scope"])
    return data


def load_profile(project=None, *, settings: Settings | None = None, migrate: bool = True) -> dict:
    """The profile for this project, or an empty one. Does not create `learning/` on a miss.

    A file with no version, or an older one, is backed up and rebuilt from the stored finals
    before it is used. Entries written by that rebuild carry an explicit confident or verified flag.
    """
    path = profile_path(project, settings=settings, create=False)
    from .procutil import is_file as _is_file
    if path is None or not _is_file(path):
        return _empty_profile(settings)
    data = _read_profile(path, settings)
    if not migrate or data.get("profile_version") == PROFILE_VERSION:
        return data
    return _migrate_profile(path, project, settings)


def _migrate_profile(path: Path, project, settings: Settings | None) -> dict:
    """Replace one old profile. The previous file is kept beside it, and this runs once."""
    backup = path.parent / f"{PROFILE_NAME}.bak"
    if not backup.exists():
        shutil.copy2(path, backup)
    path.unlink()
    rebuild_profile(project, settings=settings)
    if not path.is_file():
        return _empty_profile(settings)
    return _read_profile(path, settings)


def project_style_override(project) -> bool | None:
    """This project's own switch. None means it follows the global setting."""
    if project is None:
        return None
    value = getattr(project, "apply_house_style", None)
    if value is None:
        return None
    return bool(value)


def style_enabled(project=None, settings: Settings | None = None, profile: dict | None = None) -> bool:
    """On for a project that has finals, unless this project or the global setting says off.

    A project's own value wins. None on the project follows the global setting, and only an
    explicit False there turns the style off. Another project's finals do not turn it on.
    A global profile is used only when that opt-in is set.
    """
    settings = settings or Settings.load()
    override = project_style_override(project)
    if override is False or (override is None and settings.apply_house_style is False):
        return False
    profile = load_profile(project, settings=settings) if profile is None else profile
    return bool(profile.get("final_count"))


def _style_switch_line(project, settings: Settings | None = None) -> str:
    """One panel line: follow the global setting, or on or off for this project."""
    override = project_style_override(project)
    if override is True:
        return "החלת סגנון: פעיל בפרויקט זה"
    if override is False:
        return "החלת סגנון: כבוי בפרויקט זה"
    settings = settings or Settings.load()
    state = "כבוי" if settings.apply_house_style is False else "פעיל"
    return f"החלת סגנון: לפי ההגדרה הכללית ({state})"


def _text_of(entity) -> str:
    if entity.dxftype() == "MTEXT":
        try:
            return entity.plain_text()
        except Exception:  # noqa: BLE001
            return str(getattr(entity, "text", "") or "")
    if entity.dxf.hasattr("text"):
        return str(entity.dxf.text or "")
    return ""


def _language(texts: list[str]) -> str:
    he = sum(1 for text in texts if HEBREW.search(text))
    en = sum(1 for text in texts if LATIN.search(text))
    if he and en:
        return "both" if he == en else ("he" if he > en else "en")
    if he:
        return "he"
    if en:
        return "en"
    return ""


def _paper(doc) -> str:
    for layout in doc.layouts:
        width = getattr(layout.dxf, "paper_width", 0) or 0
        height = getattr(layout.dxf, "paper_height", 0) or 0
        if width and height:
            return f"{width:g}x{height:g}"
    return ""


def _plot_style(doc) -> str:
    try:
        for tag, value in doc.header.custom_vars:
            if str(tag).upper() in {"CTB", "PLOTSTYLE", "STYLESHEET"} and value:
                return str(value)
    except Exception:  # noqa: BLE001
        pass
    for layout in doc.layouts:
        for attr in ("plot_configuration_file", "stylesheet", "current_style_sheet"):
            value = getattr(layout.dxf, attr, "") or ""
            if value:
                return str(value)
    return ""


def _title_block(doc) -> bool:
    for entity in doc.modelspace():
        layer = normalize_layer(entity.dxf.layer).upper() if entity.dxf.hasattr("layer") else ""
        if layer in _TITLE_LAYERS:
            return True
        if entity.dxftype() == "INSERT" and "TITLE" in normalize_layer(entity.dxf.name).upper():
            return True
    return False


def _scale_guess(doc) -> float | None:
    try:
        from ezdxf import bbox
        ext = bbox.extents(doc.modelspace(), fast=True)
        width = float(ext.size.x)
    except Exception:  # noqa: BLE001
        return None
    if width <= 0:
        return None
    ins = int(doc.header.get("$INSUNITS", 0) or 0)
    paper = 0.42 if ins == 6 else 420.0
    guess = width / paper
    for nice in (1, 10, 20, 50, 100, 200, 250, 500, 1000, 2000, 5000):
        if abs(guess - nice) / nice < 0.2:
            return float(nice)
    return round(guess, 3)


def _dimvar(style, name: str):
    try:
        return style.dxf.get(name)
    except (AttributeError, ValueError):
        return None


def observe(doc) -> dict:
    """One drawing's style facts. Layer keys are normalised; ``name`` keeps the original."""
    layers = {}
    for layer in doc.layers:
        original = str(layer.dxf.name)
        if original.upper() in {"0", "DEFPOINTS"}:
            continue
        layers[normalize_layer(original)] = {
            "name": original,
            "color": int(layer.dxf.color),
            "lineweight": int(layer.dxf.lineweight or 0),
            "linetype": str(layer.dxf.linetype or "CONTINUOUS"),
        }
    kinds: dict[str, Counter] = {}
    counts: Counter = Counter()
    texts: list[str] = []
    heights: Counter = Counter()
    for entity in doc.modelspace():
        kind = entity.dxftype()
        counts[kind] += 1
        if entity.dxf.hasattr("layer"):
            kinds.setdefault(kind, Counter())[normalize_layer(entity.dxf.layer)] += 1
        text = _text_of(entity)
        if text:
            texts.append(text)
        if kind == "TEXT" and entity.dxf.hasattr("height"):
            heights[f"{float(entity.dxf.height):.2f}"] += 1
        elif kind == "MTEXT" and entity.dxf.hasattr("char_height"):
            heights[f"{float(entity.dxf.char_height):.2f}"] += 1
    used_dims: Counter = Counter()
    for layout in doc.layouts:
        for entity in layout:
            if entity.dxftype() == "DIMENSION" and entity.dxf.hasattr("dimstyle"):
                used_dims[str(entity.dxf.dimstyle)] += 1
    dimstyles = {}
    for name in used_dims:
        if name.upper() in {"STANDARD", "EZDXF"}:
            continue
        try:
            style = doc.dimstyles.get(name)
        except Exception:  # noqa: BLE001
            continue
        dimstyles[name] = {var: _dimvar(style, var) for var in _DIMVARS}
    styles = [str(style.dxf.name) for style in doc.styles if str(style.dxf.name).upper() != "STANDARD"]
    ins = int(doc.header.get("$INSUNITS", 0) or 0)
    return {
        "units": _INSUNITS.get(ins, str(ins) if ins else ""),
        "insunits": ins,
        "scale_guess": _scale_guess(doc),
        "ltscale": float(doc.header.get("$LTSCALE", 1) or 1),
        "layers": layers,
        "kind_counts": {kind: dict(counter) for kind, counter in kinds.items()},
        "entity_counts": dict(counts),
        "texts": texts,
        "language": _language(texts),
        "text_heights": dict(heights),
        "dimstyles": dimstyles,
        "text_styles": styles,
        "linetypes": [str(item.dxf.name) for item in doc.linetypes],
        "title_block": _title_block(doc),
        "plot_style": _plot_style(doc),
        "paper": _paper(doc),
    }


def _point(value, places: int = 4) -> tuple[float, float]:
    if hasattr(value, "x"):
        return (round(float(value.x), places), round(float(value.y), places))
    return (round(float(value[0]), places), round(float(value[1]), places))


def _entity_key(entity) -> tuple:
    """Geometry identity. A shared handle is preferred by the matcher before this key."""
    kind = entity.dxftype()
    if kind == "LINE":
        ends = tuple(sorted((_point(entity.dxf.start), _point(entity.dxf.end))))
        return ("geom", kind, ends)
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        try:
            points = tuple(_point(point[:2]) for point in entity.get_points("xy"))
        except (AttributeError, TypeError, ValueError):
            points = ()
        return ("geom", kind, points)
    if kind == "CIRCLE":
        return ("geom", kind, _point(entity.dxf.center), round(float(entity.dxf.radius), 4))
    if kind in {"TEXT", "MTEXT"}:
        return ("geom", kind, _text_of(entity), _point(entity.dxf.insert))
    if kind == "INSERT":
        return ("geom", kind, str(entity.dxf.name), _point(entity.dxf.insert))
    return ("handle", kind, str(entity.dxf.handle))


MIN_MATCHES = 5
_UNIT_TO_MM = {1: 25.4, 4: 1.0, 5: 10.0, 6: 1000.0}


def _coord_list(entity) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [_point(entity.dxf.start, 6), _point(entity.dxf.end, 6)]
    if kind in {"LWPOLYLINE", "POLYLINE"}:
        try:
            return [_point(point[:2], 6) for point in entity.get_points("xy")]
        except (AttributeError, TypeError, ValueError):
            return []
    if kind == "CIRCLE":
        return [_point(entity.dxf.center, 6)]
    if kind in {"TEXT", "MTEXT", "INSERT"}:
        return [_point(entity.dxf.insert, 6)]
    return []


def _geometry_agrees(source, final, tol: float = 1e-3) -> bool:
    """Same entity type and the same coordinates, within tolerance. A shared handle is not enough."""
    if source.dxftype() != final.dxftype():
        return False
    left, right = _coord_list(source), _coord_list(final)
    if not left or len(left) != len(right):
        return False
    if source.dxftype() == "LINE":
        left, right = sorted(left), sorted(right)
    return all(abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol for a, b in zip(left, right))


def _matched_pairs(app_doc, final_doc) -> list[tuple]:
    """App entity to final entity. A handle counts only when the type and the geometry agree."""
    by_handle = {}
    by_geom: dict[tuple, list] = {}
    for entity in final_doc.modelspace():
        by_handle[str(entity.dxf.handle)] = entity
        by_geom.setdefault(_entity_key(entity), []).append(entity)
    used: set[int] = set()
    pairs = []
    for source in app_doc.modelspace():
        handle = by_handle.get(str(source.dxf.handle))
        if (handle is not None and id(handle) not in used
                and _geometry_agrees(source, handle)):
            used.add(id(handle))
            pairs.append((source, handle))
            continue
        for candidate in by_geom.get(_entity_key(source), []):
            if id(candidate) not in used and _geometry_agrees(source, candidate):
                used.add(id(candidate))
                pairs.append((source, candidate))
                break
    return pairs


def _bbox(doc) -> tuple[float, float, float, float] | None:
    points = []
    for entity in doc.modelspace():
        points.extend(_coord_list(entity))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _boxes_overlap(left, right) -> bool:
    return not (left[2] < right[0] or right[2] < left[0] or left[3] < right[1] or right[3] < left[1])


def _insunits(doc) -> int:
    return int(doc.header.get("$INSUNITS", 0) or 0)


def comparability(app_doc, final_doc) -> dict:
    """Whether a final may drive mechanical style changes.

    A redraw in different units, or geometry that does not sit on the app output, is recorded
    and never applied.
    """
    if app_doc is None or final_doc is None:
        return {"comparable": False, "reason": "no app output", "matches": 0}
    app_units, final_units = _insunits(app_doc), _insunits(final_doc)
    if app_units and final_units and app_units != final_units:
        return {"comparable": False, "reason": "units differ", "matches": 0}
    app_box, final_box = _bbox(app_doc), _bbox(final_doc)
    if app_box and final_box:
        app_factor = _UNIT_TO_MM.get(app_units, 1.0)
        final_factor = _UNIT_TO_MM.get(final_units, 1.0)
        app_mm = tuple(value * app_factor for value in app_box)
        final_mm = tuple(value * final_factor for value in final_box)
        app_width = app_mm[2] - app_mm[0]
        final_width = final_mm[2] - final_mm[0]
        if app_width > 0 and final_width > 0 and (app_width / final_width > 2 or final_width / app_width > 2):
            return {"comparable": False, "reason": "measured scale differs", "matches": 0}
        if not _boxes_overlap(app_mm, final_mm):
            return {"comparable": False, "reason": "extents do not overlap", "matches": 0}
    matches = len(_matched_pairs(app_doc, final_doc))
    if matches < MIN_MATCHES:
        return {"comparable": False, "reason": "too few matches", "matches": matches}
    return {"comparable": True, "reason": "", "matches": matches}


def _absolute_color(layer) -> int:
    """Layer colour as a positive ACI value. A negative colour only means the layer is off."""
    return abs(int(layer.dxf.color))


def layer_map_from(app_doc, final_doc) -> dict:
    """One final layer for each app layer, matched by handle or geometry.

    Two app layers are never given the same target name. Colour is stored as an absolute
    value. Linetype is not part of the map.
    """
    if app_doc is None or final_doc is None:
        return {}
    source_totals: Counter = Counter()
    for entity in app_doc.modelspace():
        if entity.dxf.hasattr("layer"):
            source_totals[_layer_key(entity.dxf.layer)] += 1
    votes: dict[str, Counter] = {}
    sources: dict[str, str] = {}
    for source, final in _matched_pairs(app_doc, final_doc):
        if not source.dxf.hasattr("layer") or not final.dxf.hasattr("layer"):
            continue
        source_name = str(source.dxf.layer)
        key = _layer_key(source_name)
        try:
            layer = final_doc.layers.get(str(final.dxf.layer))
        except Exception:  # noqa: BLE001
            continue
        token = (str(layer.dxf.name), _absolute_color(layer), int(layer.dxf.lineweight or 0))
        votes.setdefault(key, Counter())[token] += 1
        sources[key] = source_name
    mapped = {}
    for key, counter in votes.items():
        (name, color, weight), winner_count = counter.most_common(1)[0]
        matched = int(sum(counter.values()))
        total = int(source_totals.get(key, matched))
        winner_count = int(winner_count)
        winner_share = winner_count / matched if matched else 0
        layer_share = matched / total if total else 0
        # One target has to win, and it still needs five matches and most of the source layer.
        confident = (winner_count >= MIN_MATCHES and winner_share > 0.5 and layer_share > 0.5)
        mapped[key] = {"source": sources[key], "name": name, "color": int(color),
                       "lineweight": int(weight), "count": winner_count, "matched": winner_count,
                       "total": total, "confident": confident}
    kept = {key: info for key, info in mapped.items() if info.get("confident") is True}
    by_target: dict[str, list[str]] = {}
    for key, info in kept.items():
        by_target.setdefault(_layer_key(info["name"]), []).append(key)
    for keys in by_target.values():
        if len(keys) > 1:
            for key in keys:
                kept.pop(key, None)
    return kept


def _font_in_use(doc) -> str:
    """The font file named by the text style that TEXT and MTEXT actually use."""
    counts: Counter = Counter()
    for layout in doc.layouts:
        for entity in layout.query("TEXT MTEXT"):
            try:
                style = doc.styles.get(str(entity.dxf.style))
            except Exception:  # noqa: BLE001
                continue
            font = str(getattr(style.dxf, "font", "") or "")
            if font:
                counts[font] += 1
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _font_file_exists(font: str) -> bool:
    """True when the font file is absolute, bundled, or in the Windows fonts folder.

    A bare name is not resolved against the working directory.
    """
    if not font:
        return False
    path = Path(font)
    if path.is_absolute() and path.is_file():
        return True
    name = path.name
    if not name or path.is_absolute():
        return False
    from .drawing.text import _BUNDLED_FONT
    if (_BUNDLED_FONT.parent / name).is_file():
        return True
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    if windir and (Path(windir) / "Fonts" / name).is_file():
        return True
    return False


def _spec_of(document: Document | None) -> dict:
    if document is None:
        return {}
    spec_path = document.path / "spec.json"
    if not spec_path.is_file():
        return {}
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return spec if isinstance(spec, dict) else {}


def _sheet_scale(document: Document | None) -> float | None:
    spec = _spec_of(document)
    try:
        scale = float(spec.get("scale"))
    except (TypeError, ValueError):
        return None
    if scale <= 0:
        return None
    return scale


def sheet_kind(spec: dict | None) -> str:
    """``paper`` for a multiview sheet (paper-space LTSCALE), otherwise ``model``."""
    views = spec.get("views") if isinstance(spec, dict) else None
    if isinstance(views, list) and any(isinstance(view, dict) for view in views):
        return "paper"
    return "model"


def _scale_key(scale: float | None, kind: str | None = "model") -> str:
    if scale is None:
        return ""
    return f"{float(scale):g}|{kind or 'model'}"


def _summary(before: dict | None, after: dict) -> dict:
    before = before or {"layers": {}, "entity_counts": {}, "texts": [], "dimstyles": {}, "title_block": False}
    left = set(before.get("layers") or {})
    right = set(after.get("layers") or {})
    old_texts = set(before.get("texts") or [])
    new_texts = set(after.get("texts") or [])
    return {
        "layers_added": sorted(right - left),
        "layers_removed": sorted(left - right),
        "entity_counts_before": before.get("entity_counts") or {},
        "entity_counts_after": after.get("entity_counts") or {},
        "texts_added": sorted(new_texts - old_texts),
        "texts_removed": sorted(old_texts - new_texts),
        "dims_before": len(before.get("dimstyles") or {}),
        "dims_after": len(after.get("dimstyles") or {}),
        "title_block_before": bool(before.get("title_block")),
        "title_block_after": bool(after.get("title_block")),
    }


def _vote(node: dict, key, weight: int) -> None:
    text = "" if key is None else str(key)
    if text == "":
        return
    counts = node.setdefault("counts", {})
    counts[text] = counts.get(text, 0) + weight
    node["value"] = max(counts, key=lambda item: (counts[item], item == text))


def _merge(profile: dict, seen: dict, weight: int) -> dict:
    profile["final_count"] = int(profile.get("final_count", 0)) + 1
    profile.setdefault("observations", []).append(_observation(seen))
    if not seen.get("comparable"):
        return profile
    for field in ("units", "ltscale", "language", "plot_style", "paper"):
        _vote(profile.setdefault(field, {}), seen.get(field), weight)
    title = profile.setdefault("title_block", {"counts": {}})
    flag = "yes" if seen.get("title_block") else "no"
    title.setdefault("counts", {})
    title["counts"][flag] = title["counts"].get(flag, 0) + weight
    title["value"] = title["counts"].get("yes", 0) >= title["counts"].get("no", 0)
    heights = profile.setdefault("text_heights", {"counts": {}})
    for height, count in (seen.get("text_heights") or {}).items():
        heights["counts"][height] = heights["counts"].get(height, 0) + weight * int(count)
    mapped = profile.setdefault("layer_map", {})
    for key, info in (seen.get("layer_map") or {}).items():
        if not info.get("confident"):
            continue
        row = mapped.setdefault(key, {"source": info.get("source") or key, "counts": {}})
        token = f"{info['name']}|{int(info['color'])}|{int(info['lineweight'])}"
        row["counts"][token] = row["counts"].get(token, 0) + weight
        best = max(row["counts"], key=lambda item: (row["counts"][item], item == token))
        name, color, lineweight = best.split("|")
        row["name"] = name
        row["color"] = abs(int(color))
        row["lineweight"] = int(lineweight)
        row["source"] = info.get("source") or row.get("source") or key
        row["matched"] = int(info.get("matched") or info.get("count") or 0)
        row["total"] = int(info.get("total") or row["matched"])
        row["confident"] = info.get("confident") is True
    by_target: dict[str, list[str]] = {}
    for key, info in list(mapped.items()):
        by_target.setdefault(_layer_key(str(info.get("name") or "")), []).append(key)
    for keys in by_target.values():
        if len(keys) > 1:
            for key in keys:
                mapped.pop(key, None)
    scale_key = _scale_key(seen.get("sheet_scale"), seen.get("sheet_kind") or "model")
    if scale_key and seen.get("scale_verified") and seen.get("ltscale") is not None:
        table = profile.setdefault("ltscale_by_scale", {})
        _vote(table.setdefault(scale_key, {}), seen.get("ltscale"), weight)
        table[scale_key]["verified"] = True
    if seen.get("font"):
        _vote(profile.setdefault("font", {}), seen.get("font"), weight)
        profile["font"]["verified"] = True
    if seen.get("dimstyles") and scale_key and seen.get("scale_verified"):
        size_vars = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap", "dimscale")
        node = profile.setdefault("dims_by_scale", {}).setdefault(scale_key, {"vars": {}, "counts": {}})
        node.setdefault("counts", {})
        last = ""
        for name, variables in seen["dimstyles"].items():
            node["counts"][name] = node["counts"].get(name, 0) + weight
            last = name
        def _dim_rank(item: str) -> tuple:
            tail = item.rsplit("_", 1)[-1]
            scaled = tail.replace(".", "", 1).isdigit()
            return (node["counts"][item], 0 if scaled else 1)

        chosen = max(node["counts"], key=_dim_rank)
        chosen_vars = seen["dimstyles"].get(chosen) or {}
        node["name"] = chosen
        node["vars"] = {var: chosen_vars.get(var) for var in size_vars}
        node["verified"] = True
    if seen.get("dimstyles"):
        node = profile.setdefault("dimstyle", {"value": "", "vars": {}, "counts": {}})
        node.setdefault("counts", {})
        last = ""
        for name, variables in seen["dimstyles"].items():
            node["counts"][name] = node["counts"].get(name, 0) + weight
            last = name
            node["vars"] = variables
        node["value"] = max(node["counts"], key=lambda item: (
            node["counts"][item], 0 if item.rsplit("_", 1)[-1].replace(".", "", 1).isdigit() else 1))
        if node["value"] in seen["dimstyles"]:
            node["vars"] = {key: value for key, value in seen["dimstyles"][node["value"]].items()
                            if key not in {"dimlfac", "dimdec"}}
    return profile


def _observation(seen: dict) -> dict:
    layers = {}
    for key, info in (seen.get("layers") or {}).items():
        layers[key] = {"name": info.get("name"), "color": abs(int(info.get("color") or 0)),
                       "lineweight": int(info.get("lineweight") or 0)}
    return {
        "file": seen.get("file") or "",
        "units": seen.get("units") or "",
        "ltscale": seen.get("ltscale"),
        "layers": layers,
        "dimstyles": sorted((seen.get("dimstyles") or {}).keys()),
        "font": seen.get("font") or "",
        "language": seen.get("language") or "",
        "title_block": bool(seen.get("title_block")),
        "plot_style": seen.get("plot_style") or "",
        "comparable": bool(seen.get("comparable")),
        "reason": seen.get("reason") or "",
        "matches": int(seen.get("matches") or 0),
    }


def _read_dxf(path: Path):
    import ezdxf
    return ezdxf.readfile(path)


def _file_md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _finals_child(doc: Document, rel: str) -> Path | None:
    """A file directly inside this drawing's ``finals/`` folder, or None."""
    text = str(rel or "").replace("\\", "/").lstrip("/")
    if not text or ".." in Path(text).parts:
        return None
    root = (doc.path / "finals").resolve()
    target = (doc.path / text).resolve()
    if target.parent != root:
        return None
    return target


def _remember_dxf(doc: Document | None, entry: dict | None, dxf: Path, source_md5: str) -> None:
    """Remember which DXF belongs to this final so the next rebuild can reuse it."""
    if doc is None or entry is None:
        return
    rel = dxf.resolve().relative_to(doc.path.resolve()).as_posix()
    owned = [str(item) for item in (entry.get("dxfs") or [])]
    changed = False
    if entry.get("dxf") != rel:
        entry["dxf"] = rel
        changed = True
    if str(entry.get("dxf_md5") or "").lower() != source_md5.lower():
        entry["dxf_md5"] = source_md5.lower()
        changed = True
    if rel not in owned:
        owned.append(rel)
        entry["dxfs"] = owned
        changed = True
    if changed:
        doc.save()


def _fresh_dxf_name(dwg: Path) -> Path:
    """A new name in the same folder. Never a path that already exists."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = dwg.parent / f"{stamp}_{dwg.stem}.dxf"
    extra = 1
    while dest.exists():
        dest = dwg.parent / f"{stamp}_{extra}_{dwg.stem}.dxf"
        extra += 1
    return dest


def _place_dxf(dwg: Path, data: bytes) -> Path:
    """Write a conversion. Use ``<stored name>.dxf`` when it is free; otherwise a new name."""
    preferred = dwg.with_suffix(".dxf")
    dest = preferred if not preferred.exists() else _fresh_dxf_name(dwg)
    while True:
        try:
            handle = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            dest = _fresh_dxf_name(dwg)
            continue
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        return dest


def _as_dxf(path: Path, entry: dict | None = None, doc: Document | None = None) -> Path | None:
    """DXF for a stored final.

    Reuse the recorded DXF while the DWG's MD5 still matches. The first conversion uses
    ``<stored name>.dxf`` when that name is free. A timestamped name is used only when that
    name is already taken and does not belong to this unchanged DWG.
    """
    if path.suffix.lower() == ".dxf" and path.is_file():
        return path
    if path.suffix.lower() != ".dwg" or not path.is_file():
        return None
    entry = entry if isinstance(entry, dict) else None
    current = _file_md5(path).lower()
    recorded_md5 = str((entry or {}).get("dxf_md5") or "").lower()
    recorded = _finals_child(doc, str((entry or {}).get("dxf") or "")) if doc is not None else None
    if recorded is not None and recorded.is_file() and recorded_md5 and recorded_md5 == current:
        return recorded
    preferred = path.with_suffix(".dxf")
    filed_md5 = str((entry or {}).get("md5") or "").lower()
    inside = doc is None
    if doc is not None and preferred.is_file():
        try:
            rel = preferred.resolve().relative_to(doc.path.resolve()).as_posix()
        except ValueError:
            rel = ""
        inside = _finals_child(doc, rel) is not None
    if preferred.is_file() and filed_md5 and filed_md5 == current and inside:
        _remember_dxf(doc, entry, preferred, current)
        return preferred
    try:
        from .io import oda
        with tempfile.TemporaryDirectory(prefix="s2c_final_") as tmp:
            produced = oda.convert(path, "dxf", tmp)
            dest = _place_dxf(path, Path(produced).read_bytes())
        _remember_dxf(doc, entry, dest, current)
        return dest
    except Exception as exc:  # noqa: BLE001 - a missing converter must not lose the final
        raise RuntimeError(str(exc)) from exc


def app_dxf_for(doc: Document, rev: str, project=None) -> Path | None:
    """The app's DXF for this revision: the current output, or the archived copy."""
    if rev == doc.rev and doc.outputs.get("dxf"):
        current = doc.path / doc.outputs["dxf"]
        if current.is_file():
            return current
    if project is not None:
        archive = project.path / "90_Archive" / f"{doc.code}_Rev{rev}"
        if archive.is_dir():
            found = sorted(archive.glob("*.dxf"))
            if found:
                return found[0]
    return None


def learn_from_final(doc: Document, entry: dict, project=None) -> list[str]:
    """Diff this final against the app output of the same rev and fold it into the profile."""
    warnings: list[str] = []
    stored = doc.path / entry["file"]
    try:
        dxf = _as_dxf(stored, entry, doc)
    except RuntimeError as exc:
        warnings.append(str(exc))
        return warnings
    if dxf is None:
        return warnings
    final_doc = _read_dxf(dxf)
    final = observe(final_doc)
    app_path = app_dxf_for(doc, entry.get("rev") or doc.rev, project)
    app_doc = _read_dxf(app_path) if app_path else None
    before = observe(app_doc) if app_doc is not None else None
    verdict = comparability(app_doc, final_doc)
    final["comparable"] = verdict["comparable"]
    final["reason"] = verdict["reason"]
    final["matches"] = verdict["matches"]
    final["file"] = entry.get("file") or ""
    final["layer_map"] = layer_map_from(app_doc, final_doc) if verdict["comparable"] else {}
    final["font"] = _font_in_use(final_doc)
    spec = _spec_of(doc)
    final["sheet_scale"] = _sheet_scale(doc)
    final["sheet_kind"] = sheet_kind(spec)
    final["scale_verified"] = bool(verdict["comparable"] and final["sheet_scale"])
    stored_profile = profile_path(project, create=False)
    if (stored_profile is not None and stored_profile.is_file()
            and _read_profile(stored_profile).get("profile_version") != PROFILE_VERSION):
        # The saved final is already on the document, so the rebuild counts it once.
        load_profile(project, migrate=True)
        return warnings
    profile = load_profile(project, migrate=False)
    weight = int(profile.get("final_count", 0)) + 1
    profile = _merge(profile, final, weight)
    profile["scope"] = "global" if _use_global() else "project"
    profile["profile_version"] = PROFILE_VERSION
    profile["updated"] = datetime.now().isoformat(timespec="seconds")
    path = profile_path(project, create=True)
    if path is None:
        return warnings
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = _summary(before, final)
    example = {
        "code": doc.code,
        "rev": entry.get("rev") or doc.rev,
        "file": entry.get("file"),
        "weight": weight,
        "summary": summary,
        "units": final.get("units"),
        "ltscale": final.get("ltscale"),
        "language": final.get("language"),
        "plot_style": final.get("plot_style"),
        "paper": final.get("paper"),
        "title_block": final.get("title_block"),
        "comparable": bool(final.get("comparable")),
    }
    root = learning_root(project, create=True)
    if root is None:
        return warnings
    examples = root / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / f"{doc.code}_{Path(entry['file']).stem}.json").write_text(
        json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return warnings


def _rels_for_final(stored_rel: str, entry: dict) -> list[str]:
    rels = [stored_rel, str(entry.get("dxf") or "")]
    rels.extend(str(item) for item in (entry.get("dxfs") or []))
    stored = Path(stored_rel)
    if stored.suffix.lower() == ".dwg":
        rels.append(stored.with_suffix(".dxf").as_posix())
    return [rel for rel in rels if rel]


def forget_final(doc: Document, project, stored_rel: str) -> None:
    """Drop one final, its own DXFs, and rebuild the profile from the finals that remain."""
    gone = [item for item in doc.finals if item.get("file") == stored_rel]
    doc.finals = [item for item in doc.finals if item.get("file") != stored_rel]
    doc.save()
    keep: set[str] = set()
    for item in doc.finals:
        for rel in _rels_for_final(str(item.get("file") or ""), item):
            keep.add(rel.replace("\\", "/"))
    for item in gone:
        for rel in _rels_for_final(stored_rel, item):
            if rel.replace("\\", "/") in keep:
                continue
            target = _finals_child(doc, rel)
            if target is not None and target.is_file():
                target.unlink()
    rebuild_profile(project)


def _projects_in_scope(project, settings: Settings):
    """The projects whose finals feed this profile. A global profile sees every registered project."""
    if not _use_global(settings):
        return [project] if project is not None else []
    from .projects import Project, is_project_folder
    found = []
    seen: set[str] = set()
    folders: list[str] = []
    if project is not None:
        folders.append(str(project.path))
    folders.extend(settings.projects or [])
    for folder in folders:
        try:
            resolved = str(Path(folder).resolve())
        except OSError:
            continue
        if resolved in seen or not is_project_folder(resolved):
            continue
        seen.add(resolved)
        found.append(Project.load(resolved))
    return found


def rebuild_profile(project, settings: Settings | None = None) -> None:
    """Replace the profile with one learned again from every final still in scope."""
    settings = settings or Settings.load()
    root = learning_root(project, settings=settings, create=False)
    if root is not None and root.exists():
        profile_file = root / PROFILE_NAME
        if profile_file.is_file():
            profile_file.unlink()
        examples = root / "examples"
        if examples.is_dir():
            for path in examples.glob("*.json"):
                path.unlink()
    from .documents import list_documents
    for scoped in _projects_in_scope(project, settings):
        for document in list_documents(scoped):
            for entry in list(document.finals):
                learn_from_final(document, entry, scoped)


def recent_examples(project=None, limit: int = 3, *, comparable_only: bool = False) -> list[dict]:
    """Newest example diffs. With ``comparable_only``, observation-only finals are left out."""
    root = learning_root(project, create=False)
    folder = (root / "examples") if root is not None else None
    if folder is None or not folder.is_dir():
        return []
    files = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    found = []
    for path in files:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if comparable_only and item.get("comparable") is not True:
            continue
        found.append(item)
        if len(found) >= limit:
            break
    return found


def learned_lines(profile: dict | None = None, project=None) -> list[str]:
    """Plain lines for the read-only panel. Empty when nothing has been learned."""
    profile = load_profile(project) if profile is None else profile
    if not profile.get("final_count"):
        return []
    scope = "כל הפרויקטים" if profile.get("scope") == "global" else "הפרויקט"
    lines = [f"היקף: {scope}", f"גרסאות סופיות: {profile['final_count']}"]
    if project is not None:
        lines.append(_style_switch_line(project))
    current = profile.get("profile_version") == PROFILE_VERSION
    if current:
        for key, label in (("units", "יחידות"), ("language", "שפת הטקסט"),
                           ("plot_style", "סגנון הדפסה"), ("paper", "גודל גיליון")):
            value = (profile.get(key) or {}).get("value")
            if value not in (None, ""):
                lines.append(f"{label}: {value}")
    for key in sorted(profile.get("ltscale_by_scale") or {}):
        node = profile["ltscale_by_scale"][key] or {}
        if node.get("verified") is not True:
            continue
        value = node.get("value")
        if value not in (None, ""):
            lines.append(f"LTSCALE {key}: {value}")
    if current and not profile.get("ltscale_by_scale"):
        value = (profile.get("ltscale") or {}).get("value")
        if value not in (None, ""):
            lines.append(f"LTSCALE: {value}")
    if current:
        dimstyle = (profile.get("dimstyle") or {}).get("value")
        if dimstyle:
            lines.append(f"סגנון מידות: {dimstyle}")
    font_node = profile.get("font") or {}
    if font_node.get("verified") is True and font_node.get("value"):
        lines.append(f"גופן: {Path(str(font_node['value'])).name}")
    if current:
        title = profile.get("title_block") or {}
        if "value" in title:
            lines.append("מסגרת: " + ("כן" if title["value"] else "לא"))
    layers = profile.get("layer_map") or {}
    for key in sorted(layers)[:8]:
        row = layers[key]
        if row.get("confident") is not True:
            continue
        lines.append(
            f"שכבה {row.get('source', key)} ← {row.get('name', key)}: "
            f"צבע {row.get('color', '')}, עובי {row.get('lineweight', '')}, "
            f"ביטחון {row.get('matched', 0)}/{row.get('total', 0)}"
        )
    for item in profile.get("observations") or []:
        if item.get("comparable"):
            continue
        lines.extend(_observation_lines(item))
    return lines


def _observation_lines(item: dict) -> list[str]:
    """Facts from a final that must not be applied. Shown as a record, not as a style."""
    lines = [f"תצפית בלבד: {item.get('units') or ''} {item.get('reason') or ''}".strip()]
    if item.get("ltscale") not in (None, ""):
        lines.append(f"תצפית LTSCALE: {item['ltscale']}")
    bits = []
    for key in sorted(item.get("layers") or {})[:8]:
        row = item["layers"][key]
        bits.append(f"{row.get('name') or key} צבע {row.get('color')} עובי {row.get('lineweight')}")
    if bits:
        lines.append("תצפית שכבות: " + "; ".join(bits))
    if item.get("dimstyles"):
        lines.append("תצפית מידות: " + ", ".join(item["dimstyles"]))
    if item.get("font"):
        lines.append(f"תצפית גופן: {Path(str(item['font'])).name}")
    if item.get("language"):
        lines.append(f"תצפית שפה: {item['language']}")
    lines.append("תצפית מסגרת: " + ("כן" if item.get("title_block") else "לא"))
    if item.get("plot_style"):
        lines.append(f"תצפית הדפסה: {item['plot_style']}")
    return lines


def _rule_lines(profile: dict) -> list[str]:
    """Apply-able preferences only: a verified scale, and a layer map with real evidence."""
    lines = []
    for key in sorted(profile.get("ltscale_by_scale") or {}):
        node = profile["ltscale_by_scale"][key] or {}
        if node.get("verified") is not True:
            continue
        value = node.get("value")
        if value not in (None, ""):
            lines.append(f"LTSCALE at {key}: {value}")
    for key in sorted(profile.get("dims_by_scale") or {}):
        node = profile["dims_by_scale"][key] or {}
        if node.get("verified") is not True:
            continue
        name = node.get("name") or ""
        if name:
            lines.append(f"Dimension style at {key}: {name}")
    font_node = profile.get("font") or {}
    if font_node.get("verified") is True and font_node.get("value"):
        lines.append(f"Font: {Path(str(font_node['value'])).name}")
    for key in sorted(profile.get("layer_map") or {}):
        row = profile["layer_map"][key]
        if row.get("confident") is not True:
            continue
        lines.append(
            f"Layer {row.get('source', key)} -> {row.get('name', key)}: "
            f"colour {row.get('color', '')}, lineweight {row.get('lineweight', '')}"
        )
    return lines


def _agent_observation_lines(profile: dict) -> list[str]:
    """Facts from a final that is not comparable. No layer map, and no LTSCALE or DIMVARs."""
    lines = []
    for item in profile.get("observations") or []:
        if item.get("comparable"):
            continue
        bits = [str(item.get("units") or ""), str(item.get("reason") or "")]
        lines.append("Final: " + " ".join(bit for bit in bits if bit))
        layers = []
        for key in sorted(item.get("layers") or {})[:8]:
            row = item["layers"][key]
            layers.append(
                f"{row.get('name') or key} colour {row.get('color')} lineweight {row.get('lineweight')}"
            )
        if layers:
            lines.append("Layers in that file: " + "; ".join(layers))
        if item.get("font"):
            lines.append(f"Font file seen: {Path(str(item['font'])).name}")
        if item.get("language"):
            lines.append(f"Text language: {item['language']}")
        lines.append("Title block: " + ("yes" if item.get("title_block") else "no"))
        if item.get("plot_style"):
            lines.append(f"Plot style: {item['plot_style']}")
    return lines


def house_style_text(profile: dict | None = None, examples: list[dict] | None = None, project=None,
                     settings: Settings | None = None) -> str:
    """Agent text. Rules only while style is on. Observations stay labelled and are not rules."""
    profile = load_profile(project, settings=settings) if profile is None else profile
    if not profile.get("final_count") and not profile.get("observations"):
        return ""
    rules = _rule_lines(profile)
    enabled = style_enabled(project, settings, profile)
    chunks = []
    if enabled and rules:
        lines = ["## How Rotem finishes drawings", "",
                 "Change style only. Do not drop elements and do not move geometry."]
        lines.extend(f"- {line}" for line in rules)
        if examples is None:
            found = recent_examples(project, 3, comparable_only=True)
        else:
            found = [item for item in examples if item.get("comparable") is True][:3]
        if found:
            lines.append("")
            lines.append("### Latest differences")
            for example in found:
                summary = example.get("summary") or {}
                lines.append(
                    f"- {example.get('code')} rev {example.get('rev')}: "
                    f"layers added {summary.get('layers_added') or []}; "
                    f"layers removed {summary.get('layers_removed') or []}; "
                    f"texts added {summary.get('texts_added') or []}; "
                    f"title block {summary.get('title_block_after')}"
                )
        chunks.append("\n".join(lines))
    observed = _agent_observation_lines(profile)
    # Style off: a confident final is still context, under the same label, and it is not a rule.
    context = [] if enabled else rules
    if observed or context:
        lines = ["## Observed in Rotem's finals (for context; do not mechanically restyle)", ""]
        if context:
            lines.append("Comparable finals, recorded only as context:")
            lines.extend(f"- {line}" for line in context)
        lines.extend(f"- {line}" for line in observed)
        chunks.append("\n".join(lines))
    if not chunks:
        return ""
    return "\n\n".join(chunks) + "\n"


_SIZE_DIMVARS = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap", "dimscale")


def _layer_is_off(layer) -> bool:
    try:
        return bool(layer.is_off())
    except Exception:  # noqa: BLE001
        return int(getattr(layer.dxf, "color", 7) or 7) < 0


def _set_layer_color(layer, color: int) -> None:
    """Set the absolute colour and put the previous on/off state back."""
    off = _layer_is_off(layer)
    layer.dxf.color = abs(int(color))
    if off:
        layer.off()
    elif _layer_is_off(layer):
        layer.on()


def _mappings_that_do_not_collapse(doc, layer_map: dict) -> dict:
    """Drop a rename onto an app layer that is staying put.

    A swap is kept: both layers are renamed away, so neither is a place the other set falls into.
    """
    indexed = {}
    for key, info in layer_map.items():
        indexed[_layer_key(key)] = info
        indexed[_layer_key(str(info.get("source") or key))] = info
    present = {_layer_key(layer.dxf.name) for layer in doc.layers}
    renamed_away = set()
    for key, info in indexed.items():
        if _layer_key(str(info.get("name") or "")) != key:
            renamed_away.add(key)
    kept = {}
    for key, info in indexed.items():
        target = _layer_key(str(info.get("name") or ""))
        if target and target != key and target in present and target not in renamed_away:
            continue
        kept[key] = info
    return kept


def _layered_entities(doc):
    """Every entity that names a layer: layouts and block definitions, including *D blocks."""
    for entity in doc.entitydb.values():
        if not getattr(entity, "is_alive", True):
            continue
        if entity.dxf.hasattr("layer"):
            yield entity


def _retitle_layer(doc, layer, new_name: str) -> None:
    """Change only the spelling of a layer. DXF treats the old and new names as the same layer."""
    old = layer.dxf.name
    if old == new_name:
        return
    layer.dxf.name = new_name
    doc.layers.replace(old, layer)
    key = doc.layers.key(new_name)
    for entity in _layered_entities(doc):
        if doc.layers.key(entity.dxf.layer) == key:
            entity.dxf.layer = new_name


def apply_profile(doc, profile: dict | None = None, *, project=None, spec_units: str | None = None,
                  sheet_scale: float | None = None, sheet_kind: str = "model") -> bool:
    """Restyle from a per-layer map. Linetype, visibility and geometry stay as they are.

    LTSCALE and the size dimension variables are applied only when this sheet's scale is
    the scale they were learned at. DIMLFAC and DIMDEC are never written.
    """
    del spec_units  # units are recorded, not applied: changing INSUNITS would change the meaning
    profile = load_profile(project) if profile is None else profile
    if not profile.get("final_count"):
        return False
    changed = False
    scale_key = _scale_key(sheet_scale, sheet_kind)
    learned_scale = (profile.get("ltscale_by_scale") or {}).get(scale_key) or {}
    if scale_key and learned_scale.get("verified") is True and learned_scale.get("value") not in (None, ""):
        new_scale = float(learned_scale["value"])
        if float(doc.header.get("$LTSCALE", 1) or 1) != new_scale:
            doc.header["$LTSCALE"] = new_scale
            changed = True
    kept = _mappings_that_do_not_collapse(doc, {
        key: info for key, info in (profile.get("layer_map") or {}).items() if info.get("confident") is True
    })
    for key, info in kept.items():
        target_name = str(info.get("name") or "")
        source_name = str(info.get("source") or key)
        if not target_name or _layer_key(target_name) != key:
            continue
        try:
            layer = doc.layers.get(source_name)
        except Exception:  # noqa: BLE001
            continue
        if layer.dxf.name != target_name:
            _retitle_layer(doc, layer, target_name)
            changed = True
        if _absolute_color(layer) != abs(int(info.get("color") or 0)):
            _set_layer_color(layer, abs(int(info["color"])))
            changed = True
        if int(layer.dxf.lineweight or 0) != int(info.get("lineweight") or 0):
            layer.dxf.lineweight = int(info["lineweight"])
            changed = True
    real = {key: info for key, info in kept.items() if _layer_key(str(info.get("name") or "")) != key}
    planned = []
    for entity in _layered_entities(doc):
        key = _layer_key(entity.dxf.layer)
        info = real.get(key)
        if not info:
            continue
        target_name = str(info.get("name") or "")
        if not target_name:
            continue
        planned.append((entity, key, str(entity.dxf.layer), target_name, info))
    for _entity, _key, source_name, target_name, info in planned:
        if target_name in doc.layers:
            continue
        try:
            source_layer = doc.layers.get(source_name)
        except Exception:  # noqa: BLE001
            continue
        doc.layers.add(target_name, color=abs(int(info.get("color") or 7)),
                       lineweight=int(info.get("lineweight") or -1),
                       linetype=str(source_layer.dxf.linetype or "CONTINUOUS"))
        changed = True
        if _layer_is_off(source_layer):
            doc.layers.get(target_name).off()
    for entity, key, source_name, target_name, info in planned:
        try:
            source_layer = doc.layers.get(source_name)
        except Exception:  # noqa: BLE001
            continue
        entity_lt = str(entity.dxf.linetype) if entity.dxf.hasattr("linetype") else "BYLAYER"
        target_layer = doc.layers.get(target_name)
        source_lt = str(source_layer.dxf.linetype or "CONTINUOUS")
        if entity_lt in {"", "BYLAYER"} and str(target_layer.dxf.linetype or "CONTINUOUS") != source_lt:
            entity.dxf.linetype = source_lt
        entity.dxf.layer = target_name
        changed = True
    for key, info in real.items():
        target_name = str(info.get("name") or "")
        if target_name not in doc.layers:
            continue
        target = doc.layers.get(target_name)
        if _absolute_color(target) != abs(int(info.get("color") or 0)):
            _set_layer_color(target, abs(int(info["color"])))
            changed = True
        if int(target.dxf.lineweight or 0) != int(info.get("lineweight") or 0):
            target.dxf.lineweight = int(info["lineweight"])
            changed = True
    if scale_key:
        node = (profile.get("dims_by_scale") or {}).get(scale_key) or {}
        if node.get("verified") is not True:
            node = {}
        variables = node.get("vars") or {}
        style_name = node.get("name") or ""
        style = None
        if style_name and style_name in doc.dimstyles:
            style = doc.dimstyles.get(style_name)
        if style is not None:
            for name in _SIZE_DIMVARS:
                value = variables.get(name)
                if value is None:
                    continue
                current = _dimvar(style, name)
                if current != value and not (isinstance(current, (int, float)) and isinstance(value, (int, float))
                                              and abs(float(current) - float(value)) < 1e-9):
                    try:
                        style.dxf.set(name, value)
                        changed = True
                    except (AttributeError, TypeError, ValueError):
                        continue
    font_node = profile.get("font") or {}
    font = str(font_node.get("value") or "") if font_node.get("verified") is True else ""
    if font and _font_file_exists(font):
        seen_styles = set()
        for layout in doc.layouts:
            for entity in layout.query("TEXT MTEXT"):
                seen_styles.add(str(entity.dxf.style))
        for name in seen_styles:
            try:
                style = doc.styles.get(name)
            except Exception:  # noqa: BLE001
                continue
            if str(getattr(style.dxf, "font", "") or "") != font:
                style.dxf.font = font
                changed = True
    return changed
