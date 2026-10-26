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


def parse_reply(text: str) -> dict:
    """Extract the JSON object from a reply (tolerates a ```json fence)."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fence.group(1) if fence else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


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
    kept, seen = [], set()
    for i, el in enumerate(spec["elements"]):
        if not isinstance(el, dict) or el.get("type") not in ELEMENT_TYPES:
            warnings.append(f"Dropped element {i}: unknown type {el.get('type') if isinstance(el, dict) else el!r}")
            continue
        el.setdefault("id", f"{el['type']}_{i}")
        if el["id"] in seen:
            warnings.append(f"duplicate id {el['id']!r}")
        seen.add(el["id"])
        kept.append(el)
    spec["elements"] = kept
    return spec, warnings
