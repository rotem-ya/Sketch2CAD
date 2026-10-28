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


def learning_dir() -> Path:
    path = app_home() / "learning"
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path() -> Path:
    return learning_dir() / PROFILE_NAME


def normalize_layer(name: str) -> str:
    """Drop Hebrew vowel marks so ``\\u05b0$$$-TD`` matches ``$$$-TD``. The original is kept elsewhere."""
    return NIQQUD.sub("", name or "").strip()


def load_profile() -> dict:
    path = profile_path()
    if not path.is_file():
        return {"final_count": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"final_count": 0}
    if not isinstance(data, dict):
        return {"final_count": 0}
    data.setdefault("final_count", 0)
    return data


def style_enabled(settings: Settings | None = None, profile: dict | None = None) -> bool:
    """On by default once at least one final has been learned. An explicit False stays off."""
    profile = load_profile() if profile is None else profile
    if not profile.get("final_count"):
        return False
    settings = settings or Settings.load()
    if settings.apply_house_style is None:
        return True
    return bool(settings.apply_house_style)


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
    layers = profile.setdefault("layers", {})
    for key, info in (seen.get("layers") or {}).items():
        row = layers.setdefault(key, {"name": info["name"], "counts": 0, "color": {}, "lineweight": {}, "linetype": {}})
        row["name"] = info["name"]
        row["counts"] = int(row.get("counts", 0)) + weight
        for part in ("color", "lineweight", "linetype"):
            bucket = row.setdefault(part, {})
            token = str(info[part])
            bucket[token] = bucket.get(token, 0) + weight
            row[part + "_value"] = max(bucket, key=lambda item: (bucket[item], item == token))
    kinds = profile.setdefault("kind_layer", {})
    for kind, layers_used in (seen.get("kind_counts") or {}).items():
        bucket = kinds.setdefault(kind, {})
        for layer, count in layers_used.items():
            bucket[layer] = bucket.get(layer, 0) + weight * int(count)
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
            node["vars"] = seen["dimstyles"][node["value"]]
    if seen.get("text_styles"):
        _vote(profile.setdefault("text_style", {}), seen["text_styles"][0], weight)
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
    final = observe(_read_dxf(dxf))
    app_path = app_dxf_for(doc, entry.get("rev") or doc.rev, project)
    before = observe(_read_dxf(app_path)) if app_path else None
    profile = load_profile()
    weight = int(profile.get("final_count", 0)) + 1
    profile = _merge(profile, final, weight)
    profile["updated"] = datetime.now().isoformat(timespec="seconds")
    profile_path().write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    examples = learning_dir() / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / f"{doc.code}_{Path(entry['file']).stem}.json").write_text(
        json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return warnings


def recent_examples(limit: int = 3) -> list[dict]:
    folder = learning_dir() / "examples"
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    found = []
    for path in files[:limit]:
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return found


def learned_lines(profile: dict | None = None) -> list[str]:
    """Plain lines for the read-only panel. Empty when nothing has been learned."""
    profile = load_profile() if profile is None else profile
    if not profile.get("final_count"):
        return []
    lines = [f"גרסאות סופיות: {profile['final_count']}"]
    for key, label in (("units", "יחידות"), ("ltscale", "קנה מידה לסוג קו"),
                       ("language", "שפת הטקסט"), ("plot_style", "סגנון הדפסה"), ("paper", "גודל גיליון")):
        value = (profile.get(key) or {}).get("value")
        if value not in (None, ""):
            lines.append(f"{label}: {value}")
    dimstyle = (profile.get("dimstyle") or {}).get("value")
    if dimstyle:
        lines.append(f"סגנון מידות: {dimstyle}")
    text_style = (profile.get("text_style") or {}).get("value")
    if text_style:
        lines.append(f"סגנון טקסט: {text_style}")
    title = profile.get("title_block") or {}
    if "value" in title:
        lines.append("מסגרת: " + ("כן" if title["value"] else "לא"))
    layers = profile.get("layers") or {}
    for key in sorted(layers, key=lambda item: (-int(layers[item].get("counts", 0)), item))[:8]:
        row = layers[key]
        lines.append(f"שכבה {row.get('name', key)}: צבע {row.get('color_value', '')}, "
                     f"עובי {row.get('lineweight_value', '')}, קו {row.get('linetype_value', '')}")
    return lines


def house_style_text(profile: dict | None = None, examples: list[dict] | None = None) -> str:
    """A short section for the agent package. Empty when nothing has been learned."""
    profile = load_profile() if profile is None else profile
    if not profile.get("final_count"):
        return ""
    examples = recent_examples(3) if examples is None else examples[:3]
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


def _ensure_linetype(doc, name: str) -> str:
    name = name or "CONTINUOUS"
    if name.upper() == "CONTINUOUS" or name in doc.linetypes:
        return name
    try:
        doc.linetypes.add(name, pattern=[0.5, 0.25, -0.25], description=name)
    except Exception:  # noqa: BLE001
        return "CONTINUOUS"
    return name


def _layer_value(row: dict, part: str, cast):
    raw = row.get(part + "_value")
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return None


def apply_profile(doc, profile: dict | None = None, *, spec_units: str | None = None) -> bool:
    """Restyle `doc` from the profile. Coordinates, text and entity counts are not touched."""
    profile = load_profile() if profile is None else profile
    if not profile.get("final_count"):
        return False
    ltscale = (profile.get("ltscale") or {}).get("value")
    if ltscale not in (None, ""):
        doc.header["$LTSCALE"] = float(ltscale)
    learned_units = (profile.get("units") or {}).get("value")
    if spec_units and learned_units == spec_units:
        import ezdxf
        mapping = {"mm": ezdxf.units.MM, "m": ezdxf.units.M}
        if learned_units in mapping:
            doc.units = mapping[learned_units]
    for row in (profile.get("layers") or {}).values():
        name = row.get("name") or ""
        if not name or name.upper() in {"0", "DEFPOINTS"}:
            continue
        color = _layer_value(row, "color", int)
        weight = _layer_value(row, "lineweight", int)
        linetype = _ensure_linetype(doc, str(row.get("linetype_value") or "CONTINUOUS"))
        if name not in doc.layers:
            doc.layers.add(name, color=color if color is not None else 7,
                           lineweight=weight if weight is not None else -1, linetype=linetype)
        else:
            layer = doc.layers.get(name)
            if color is not None:
                layer.dxf.color = color
            if weight is not None:
                layer.dxf.lineweight = weight
            layer.dxf.linetype = linetype
    kind_layer = {}
    for kind, bucket in (profile.get("kind_layer") or {}).items():
        if bucket:
            kind_layer[kind] = max(bucket, key=lambda item: (bucket[item], item))
    norm_to_original = {key: (row.get("name") or key) for key, row in (profile.get("layers") or {}).items()}
    for entity in doc.modelspace():
        target = kind_layer.get(entity.dxftype())
        original = norm_to_original.get(target)
        if original and entity.dxf.hasattr("layer"):
            entity.dxf.layer = original
    dim_name = (profile.get("dimstyle") or {}).get("value")
    if dim_name:
        if dim_name not in doc.dimstyles and "EZDXF" in doc.dimstyles:
            doc.dimstyles.duplicate_entry("EZDXF", dim_name)
        elif dim_name not in doc.dimstyles and "STANDARD" in doc.dimstyles:
            doc.dimstyles.duplicate_entry("STANDARD", dim_name)
        style = doc.dimstyles.get(dim_name) if dim_name in doc.dimstyles else None
        for var, value in ((profile.get("dimstyle") or {}).get("vars") or {}).items():
            if style is not None and value is not None:
                try:
                    style.dxf.set(var, value)
                except (AttributeError, TypeError, ValueError):
                    continue
        if dim_name in doc.dimstyles:
            for entity in doc.modelspace().query("DIMENSION"):
                entity.dxf.dimstyle = dim_name
    text_style = (profile.get("text_style") or {}).get("value")
    if text_style:
        if text_style not in doc.styles:
            doc.styles.add(text_style, font="arial.ttf")
        for entity in doc.modelspace().query("TEXT MTEXT"):
            entity.dxf.style = text_style
    return True
