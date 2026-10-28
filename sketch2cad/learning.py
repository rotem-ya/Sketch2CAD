"""House style learned from finals the engineer finished outside the app.

The profile is counts, weighted toward the newer final. Applying it changes layers, styles
and header variables only. Entity counts, text and coordinates stay as the renderer drew them.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import Settings, app_home
from .documents import Document

NIQQUD = re.compile(r"[\u0591-\u05C7]")
HEBREW = re.compile(r"[\u0590-\u05FF]")
LATIN = re.compile(r"[A-Za-z]")
PROFILE_NAME = "style_profile.json"
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


def load_profile(project=None, *, settings: Settings | None = None) -> dict:
    """The profile for this project, or an empty one. Does not create `learning/`."""
    path = profile_path(project, settings=settings, create=False)
    empty = {"final_count": 0, "scope": "global" if _use_global(settings) else "project"}
    if path is None or not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    data.setdefault("final_count", 0)
    data.setdefault("scope", empty["scope"])
    return data


def style_enabled(project=None, settings: Settings | None = None, profile: dict | None = None) -> bool:
    """On for a project that has finals, unless the setting is explicitly off.

    Another project's finals do not turn it on. A global profile is used only when that
    opt-in is set.
    """
    settings = settings or Settings.load()
    if settings.apply_house_style is False:
        return False
    profile = load_profile(project, settings=settings) if profile is None else profile
    return bool(profile.get("final_count"))


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
    dimstyles = {}
    for style in doc.dimstyles:
        name = str(style.dxf.name)
        if name.upper() in {"STANDARD", "EZDXF"}:
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


def _matched_pairs(app_doc, final_doc) -> list[tuple]:
    """App entity to final entity, by handle when it survived, otherwise by geometry."""
    by_handle = {}
    by_geom: dict[tuple, list] = {}
    for entity in final_doc.modelspace():
        by_handle[str(entity.dxf.handle)] = entity
        by_geom.setdefault(_entity_key(entity), []).append(entity)
    used: set[int] = set()
    pairs = []
    for source in app_doc.modelspace():
        handle = by_handle.get(str(source.dxf.handle))
        if handle is not None and id(handle) not in used and handle.dxftype() == source.dxftype():
            used.add(id(handle))
            pairs.append((source, handle))
            continue
        for candidate in by_geom.get(_entity_key(source), []):
            if id(candidate) not in used:
                used.add(id(candidate))
                pairs.append((source, candidate))
                break
    return pairs


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
    votes: dict[str, Counter] = {}
    sources: dict[str, str] = {}
    for source, final in _matched_pairs(app_doc, final_doc):
        if not source.dxf.hasattr("layer") or not final.dxf.hasattr("layer"):
            continue
        source_name = str(source.dxf.layer)
        key = normalize_layer(source_name)
        try:
            layer = final_doc.layers.get(str(final.dxf.layer))
        except Exception:  # noqa: BLE001
            continue
        token = (str(layer.dxf.name), _absolute_color(layer), int(layer.dxf.lineweight or 0))
        votes.setdefault(key, Counter())[token] += 1
        sources[key] = source_name
    mapped = {}
    for key, counter in votes.items():
        name, color, weight = counter.most_common(1)[0][0]
        mapped[key] = {"source": sources[key], "name": name, "color": int(color),
                       "lineweight": int(weight), "count": int(sum(counter.values()))}
    by_target: dict[str, list[str]] = {}
    for key, info in mapped.items():
        by_target.setdefault(normalize_layer(info["name"]), []).append(key)
    for keys in by_target.values():
        if len(keys) > 1:
            for key in keys:
                mapped.pop(key, None)
    return mapped


def _font_in_use(doc) -> str:
    """The font file named by the text style that TEXT and MTEXT actually use."""
    counts: Counter = Counter()
    for entity in doc.modelspace().query("TEXT MTEXT"):
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
    if not font:
        return False
    return Path(font).is_file()


def _sheet_scale(document: Document | None) -> float | None:
    if document is None:
        return None
    spec_path = document.path / "spec.json"
    if not spec_path.is_file():
        return None
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        scale = float(spec.get("scale"))
    except (TypeError, ValueError):
        return None
    if scale <= 0:
        return None
    return scale


def _scale_key(scale: float | None) -> str:
    if scale is None:
        return ""
    return f"{float(scale):g}"


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
        row = mapped.setdefault(key, {"source": info.get("source") or key, "counts": {}})
        token = f"{info['name']}|{int(info['color'])}|{int(info['lineweight'])}"
        row["counts"][token] = row["counts"].get(token, 0) + weight
        best = max(row["counts"], key=lambda item: (row["counts"][item], item == token))
        name, color, lineweight = best.split("|")
        row["name"] = name
        row["color"] = abs(int(color))
        row["lineweight"] = int(lineweight)
        row["source"] = info.get("source") or row.get("source") or key
    by_target: dict[str, list[str]] = {}
    for key, info in list(mapped.items()):
        by_target.setdefault(normalize_layer(str(info.get("name") or "")), []).append(key)
    for keys in by_target.values():
        if len(keys) > 1:
            for key in keys:
                mapped.pop(key, None)
    scale_key = _scale_key(seen.get("sheet_scale"))
    if scale_key and seen.get("ltscale") is not None:
        table = profile.setdefault("ltscale_by_scale", {})
        _vote(table.setdefault(scale_key, {}), seen.get("ltscale"), weight)
    if seen.get("font"):
        _vote(profile.setdefault("font", {}), seen.get("font"), weight)
    if seen.get("dimstyles") and scale_key:
        size_vars = ("dimtxt", "dimasz", "dimexe", "dimexo", "dimgap", "dimscale")
        node = profile.setdefault("dims_by_scale", {}).setdefault(scale_key, {"vars": {}, "counts": {}})
        node.setdefault("counts", {})
        last = ""
        for name, variables in seen["dimstyles"].items():
            node["counts"][name] = node["counts"].get(name, 0) + weight
            last = name
        chosen = max(node["counts"], key=lambda item: (node["counts"][item], item == last))
        chosen_vars = seen["dimstyles"].get(chosen) or {}
        node["name"] = chosen
        node["vars"] = {var: chosen_vars.get(var) for var in size_vars}
    if seen.get("dimstyles"):
        node = profile.setdefault("dimstyle", {"value": "", "vars": {}, "counts": {}})
        node.setdefault("counts", {})
        last = ""
        for name, variables in seen["dimstyles"].items():
            node["counts"][name] = node["counts"].get(name, 0) + weight
            last = name
            node["vars"] = variables
        node["value"] = max(node["counts"], key=lambda item: (node["counts"][item], item == last))
        if node["value"] in seen["dimstyles"]:
            node["vars"] = {key: value for key, value in seen["dimstyles"][node["value"]].items()
                            if key not in {"dimlfac", "dimdec"}}
    if seen.get("scale_guess") is not None:
        _vote(profile.setdefault("scale_guess", {}), seen["scale_guess"], weight)
    return profile


def _read_dxf(path: Path):
    import ezdxf
    return ezdxf.readfile(path)


def _as_dxf(path: Path) -> Path | None:
    if path.suffix.lower() == ".dxf" and path.is_file():
        return path
    if path.suffix.lower() != ".dwg" or not path.is_file():
        return None
    try:
        from .io import oda
        return oda.convert(path, "dxf", path.parent)
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
        dxf = _as_dxf(stored)
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
    final["layer_map"] = layer_map_from(app_doc, final_doc)
    final["font"] = _font_in_use(final_doc)
    final["sheet_scale"] = _sheet_scale(doc)
    profile = load_profile(project)
    weight = int(profile.get("final_count", 0)) + 1
    profile = _merge(profile, final, weight)
    profile["scope"] = "global" if _use_global() else "project"
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
    }
    root = learning_root(project, create=True)
    if root is None:
        return warnings
    examples = root / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / f"{doc.code}_{Path(entry['file']).stem}.json").write_text(
        json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return warnings


def recent_examples(project=None, limit: int = 3) -> list[dict]:
    root = learning_root(project, create=False)
    folder = (root / "examples") if root is not None else None
    if folder is None or not folder.is_dir():
        return []
    files = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    found = []
    for path in files[:limit]:
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return found


def learned_lines(profile: dict | None = None, project=None) -> list[str]:
    """Plain lines for the read-only panel. Empty when nothing has been learned."""
    profile = load_profile(project) if profile is None else profile
    if not profile.get("final_count"):
        return []
    scope = "כל הפרויקטים" if profile.get("scope") == "global" else "הפרויקט"
    lines = [f"היקף: {scope}", f"גרסאות סופיות: {profile['final_count']}"]
    for key, label in (("units", "יחידות"), ("ltscale", "קנה מידה לסוג קו"),
                       ("language", "שפת הטקסט"), ("plot_style", "סגנון הדפסה"), ("paper", "גודל גיליון")):
        value = (profile.get(key) or {}).get("value")
        if value not in (None, ""):
            lines.append(f"{label}: {value}")
    dimstyle = (profile.get("dimstyle") or {}).get("value")
    if dimstyle:
        lines.append(f"סגנון מידות: {dimstyle}")
    font = (profile.get("font") or {}).get("value")
    if font:
        lines.append(f"גופן: {Path(str(font)).name}")
    title = profile.get("title_block") or {}
    if "value" in title:
        lines.append("מסגרת: " + ("כן" if title["value"] else "לא"))
    layers = profile.get("layer_map") or {}
    for key in sorted(layers)[:8]:
        row = layers[key]
        lines.append(f"שכבה {row.get('source', key)} ← {row.get('name', key)}: "
                     f"צבע {row.get('color', '')}, עובי {row.get('lineweight', '')}")
    return lines


def house_style_text(profile: dict | None = None, examples: list[dict] | None = None, project=None) -> str:
    """A short section for the agent package. Empty when nothing has been learned."""
    profile = load_profile(project) if profile is None else profile
    if not profile.get("final_count"):
        return ""
    examples = recent_examples(project, 3) if examples is None else examples[:3]
    lines = ["## How Rotem finishes drawings", ""]
    lines.append("Change style only. Do not drop elements and do not move geometry.")
    for line in learned_lines(profile):
        lines.append(f"- {line}")
    if examples:
        lines.append("")
        lines.append("### Latest differences")
        for example in examples:
            summary = example.get("summary") or {}
            lines.append(
                f"- {example.get('code')} rev {example.get('rev')}: "
                f"layers added {summary.get('layers_added') or []}; "
                f"layers removed {summary.get('layers_removed') or []}; "
                f"texts added {summary.get('texts_added') or []}; "
                f"title block {summary.get('title_block_after')}"
            )
    return "\n".join(lines) + "\n"


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


def apply_profile(doc, profile: dict | None = None, *, project=None, spec_units: str | None = None,
                  sheet_scale: float | None = None) -> bool:
    """Restyle from a per-layer map. Linetype, visibility and geometry stay as they are.

    LTSCALE and the size dimension variables are applied only when this sheet's scale is
    the scale they were learned at. DIMLFAC and DIMDEC are never written.
    """
    del spec_units  # units are recorded, not applied: changing INSUNITS would change the meaning
    profile = load_profile(project) if profile is None else profile
    if not profile.get("final_count"):
        return False
    changed = False
    scale_key = _scale_key(sheet_scale)
    learned_scale = (profile.get("ltscale_by_scale") or {}).get(scale_key) or {}
    if scale_key and learned_scale.get("value") not in (None, ""):
        new_scale = float(learned_scale["value"])
        if float(doc.header.get("$LTSCALE", 1) or 1) != new_scale:
            doc.header["$LTSCALE"] = new_scale
            changed = True
    for source_key, info in (profile.get("layer_map") or {}).items():
        target_name = str(info.get("name") or "")
        source_name = str(info.get("source") or source_key)
        if not target_name or target_name.upper() in {"0", "DEFPOINTS"}:
            continue
        try:
            source_layer = doc.layers.get(source_name)
        except Exception:  # noqa: BLE001
            continue
        same_name = normalize_layer(target_name) == normalize_layer(source_name)
        same_color = _absolute_color(source_layer) == abs(int(info.get("color") or 0))
        same_weight = int(source_layer.dxf.lineweight or 0) == int(info.get("lineweight") or 0)
        if same_name and same_color and same_weight:
            continue
        if target_name not in doc.layers:
            doc.layers.add(target_name, color=abs(int(info.get("color") or 7)),
                           lineweight=int(info.get("lineweight") or -1),
                           linetype=str(source_layer.dxf.linetype or "CONTINUOUS"))
            changed = True
        target = doc.layers.get(target_name if target_name in doc.layers else source_name)
        if not same_color:
            _set_layer_color(target, abs(int(info["color"])))
            changed = True
        if not same_weight:
            target.dxf.lineweight = int(info["lineweight"])
            changed = True
        if not same_name:
            for entity in doc.modelspace():
                if entity.dxf.hasattr("layer") and normalize_layer(entity.dxf.layer) == source_key:
                    entity.dxf.layer = target_name
            # Keep the source layer's visibility. A final that is off must not turn this one off,
            # and moving onto a new layer must not turn a visible layer off or an off layer on.
            if _layer_is_off(source_layer):
                if not _layer_is_off(target):
                    target.off()
            elif _layer_is_off(target):
                target.on()
            changed = True
    if scale_key:
        node = (profile.get("dims_by_scale") or {}).get(scale_key) or {}
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
    font = str((profile.get("font") or {}).get("value") or "")
    if font and _font_file_exists(font):
        seen_styles = set()
        for entity in doc.modelspace().query("TEXT MTEXT"):
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
