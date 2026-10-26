"""Deterministic spec helpers: format text, catalog lines, reply parsing, normalization.

An outside agent returns a spec. These functions check and complete it. They do not call a model
and they do not use the network.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FORMATS_DOC = Path(__file__).resolve().parents[1] / "docs" / "FORMATS.md"
ELEMENT_TYPES = ("pipe", "bend", "flange", "gate_valve", "connector", "manhole", "trench_drain", "rect",
                 "polyline", "text", "flow_arrow", "north_arrow", "weld", "break", "circle")


def _spec_format() -> str:
    """The spec.json section of docs/FORMATS.md."""
    text = FORMATS_DOC.read_text(encoding="utf-8")
    start = text.find("## 1.")
    end = text.find("## 2.")
    return text[start:end].strip() if start >= 0 else text


def _catalog_lines(catalog) -> str:
    if catalog is None:
        return "(empty)"
    lines = []
    for it in catalog.items():
        dims = {**(it.get("dims") or {}), **{k: v for k, v in (it.get("flange") or {}).items() if k != "std"}}
        dim_txt = ", ".join(f"{k}={v}" for k, v in dims.items())
        lines.append(f"{it['id']} | {it.get('category', '')} | {catalog.label(it)} | {dim_txt}")
    return "\n".join(lines) or "(empty)"


def _valid_scale(scale) -> bool:
    if isinstance(scale, str):
        return scale.strip().upper() == "NTS"
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        return False
    return scale > 0


def _xy(value) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    return all(isinstance(part, (int, float)) and not isinstance(part, bool) for part in value)


def _validate_views_strict(views) -> None:
    """Raise ValueError when `views` is present and not a list of view objects."""
    if views is None:
        return
    if not isinstance(views, list):
        raise ValueError("views must be a list")
    for index, view in enumerate(views):
        if not isinstance(view, dict):
            raise ValueError(f"view {index} must be an object")
        if not _valid_scale(view.get("scale")):
            raise ValueError(f"view {index}: scale must be a positive number or 'NTS'")
        for key in ("elements", "dimensions"):
            if key in view and not isinstance(view[key], list):
                raise ValueError(f"view {index}: {key} must be a list")
        if "origin_on_sheet" in view and view["origin_on_sheet"] is not None and not _xy(view["origin_on_sheet"]):
            raise ValueError(f"view {index}: origin_on_sheet must be [x, y]")


def parse_reply(text: str) -> dict:
    """Extract the JSON object from a reply (tolerates a ```json fence).

    A top-level `views` list, or one inside `spec`, is checked: each view needs a scale
    (a positive number or \"NTS\") and list-shaped elements/dimensions. `normalize_spec`
    is what drops unknown element types and fills defaults.
    """
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fence.group(1) if fence else text[text.find("{"): text.rfind("}") + 1]
    obj = json.loads(raw)
    if isinstance(obj, dict):
        _validate_views_strict(obj.get("views"))
        spec = obj.get("spec")
        if isinstance(spec, dict):
            _validate_views_strict(spec.get("views"))
    return obj


def _normalize_elements(elements, warnings: list[str], label: str = "element") -> list:
    kept, seen = [], set()
    for i, el in enumerate(elements):
        if not isinstance(el, dict) or el.get("type") not in ELEMENT_TYPES:
            kind = el.get("type") if isinstance(el, dict) else el
            warnings.append(f"Dropped {label} {i}: unknown type {kind!r}")
            continue
        el.setdefault("id", f"{el['type']}_{i}")
        if el["id"] in seen:
            el["id"] = f"{el['id']}_{i}"
        seen.add(el["id"])
        kept.append(el)
    return kept


def _normalize_views(spec: dict, warnings: list[str]) -> None:
    if "views" not in spec:
        return
    views = spec.get("views")
    if not isinstance(views, list):
        warnings.append("views is not a list and was dropped")
        spec["views"] = []
        return
    kept, seen = [], set()
    for i, view in enumerate(views):
        if not isinstance(view, dict):
            warnings.append(f"Dropped view {i}: not an object")
            continue
        if not _valid_scale(view.get("scale")):
            warnings.append(f"Dropped view {i}: scale must be a positive number or 'NTS'")
            continue
        view = dict(view)
        if isinstance(view.get("scale"), str):
            view["scale"] = "NTS"
        vid = str(view.get("id") or f"view_{i}")
        if vid in seen:
            vid = f"{vid}_{i}"
        seen.add(vid)
        view["id"] = vid
        if "origin_on_sheet" in view and not _xy(view.get("origin_on_sheet")):
            warnings.append(f"view {vid}: origin_on_sheet must be [x, y] and was dropped")
            view.pop("origin_on_sheet", None)
        elements = view.get("elements", [])
        if not isinstance(elements, list):
            warnings.append(f"view {vid}: elements is not a list")
            elements = []
        view["elements"] = _normalize_elements(elements, warnings, f"view {vid} element")
        dimensions = view.get("dimensions", [])
        if not isinstance(dimensions, list):
            warnings.append(f"view {vid}: dimensions is not a list")
            dimensions = []
        view["dimensions"] = dimensions
        kept.append(view)
    spec["views"] = kept


def normalize_spec(spec: dict, *, units: str = "mm", language: str = "both") -> tuple[dict, list[str]]:
    """Fill defaults and drop elements the renderer does not know; returns (spec, warnings)."""
    warnings = []
    spec = dict(spec or {})
    spec.setdefault("version", 1)
    spec.setdefault("units", units)
    spec.setdefault("scale", 20)
    spec.setdefault("paper", "A3")
    spec.setdefault("language", language)
    spec.setdefault("view", "")
    spec.setdefault("sheet", {"template": "a3_legend", "fields": {}})
    for key in ("elements", "dimensions", "legend"):
        if not isinstance(spec.get(key), list):
            spec[key] = []
    spec.setdefault("notes", {"en": [], "he": []})
    spec.setdefault("params", {})
    spec["elements"] = _normalize_elements(spec["elements"], warnings)
    _normalize_views(spec, warnings)
    return spec, warnings
