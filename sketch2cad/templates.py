"""Title-block templates (FORMATS.md section 3): YAML files describing paper, frame, blocks and text fields.

Repo templates live in <repo>/templates; user templates in <app home>/templates override them by id.
Template coordinates are paper mm from the bottom-left corner of the frame; the title block is drawn in
model space at the sheet scale (1 paper mm = scale mm, or scale/1000 m).
Optional keys beyond the basic format: drawing_area / legend_area {x, y, w, h} (where the layout puts the
drawing and the legend), field `w` (field width: Hebrew is right-aligned at x + w, long text wraps),
field `he_y` (separate line for the Hebrew text in bilingual mode), block line limits x0/x1, y0/y1.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml
from ezdxf.math import Vec2

from . import config
from .drawing.text import LINE_SPACING, put_text, text_width, wrap

REPO_DIR = Path(__file__).parents[1] / "templates"
DEFAULTS = {"margin": 10.0, "frame_width": 0.7, "blocks": [], "fields": []}
UNIT_FACTOR = {"mm": 1.0, "m": 0.001}
LAYER = "FRAME"
SAME_LINE_GAP = 6.0                   # paper mm kept free between en and he text on one line


def template_dirs() -> list[Path]:
    """Lookup order: repo first, user folder last (so user templates win)."""
    return [REPO_DIR, config.app_home() / "templates"]


def _all_templates() -> dict[str, dict]:
    found = {}
    for folder in template_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("id"):
                found[data["id"]] = data
    return found


def list_templates() -> list[dict]:
    """[{id, name: {en, he}, paper: {w, h}}] sorted by id."""
    return [{"id": tid, "name": t.get("name", {"en": tid}), "paper": t.get("paper")}
            for tid, t in sorted(_all_templates().items())]


def load_template(template_id: str) -> dict:
    """Template dict with defaults filled in (frame size and drawing_area computed when missing)."""
    templates = _all_templates()
    if template_id not in templates:
        raise KeyError(f"unknown title-block template {template_id!r}")
    t = {**DEFAULTS, **templates[template_id]}
    fw, fh = t["paper"]["w"] - 2 * t["margin"], t["paper"]["h"] - 2 * t["margin"]
    t["frame"] = {"w": fw, "h": fh}
    if "drawing_area" not in t:
        bottom = max([b["y"] + b["h"] for b in t["blocks"] if b["y"] == 0] or [0])
        t["drawing_area"] = {"x": 3, "y": bottom + 3, "w": fw - 6, "h": fh - bottom - 6}
    return t


# ------------------------------------------------------------------ values
def value_of(obj, key, default=None):
    """Attribute or dict key (project/document may be dataclasses or plain dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def format_scale(scale: float) -> str:
    return f"{float(scale):g}"


def build_values(spec: dict, project=None, document=None) -> dict:
    """Placeholder values: project/document data < spec.sheet.fields < document.title_overrides."""
    pname = value_of(project, "name") or {}
    title = value_of(document, "title") or {}
    values = {
        "code": value_of(document, "code", ""), "rev": value_of(document, "rev", ""),
        "title_en": title.get("en", ""), "title_he": title.get("he", ""),
        "project_code": value_of(project, "code", ""), "project_name_en": pname.get("en", ""),
        "project_name_he": pname.get("he", ""), "client": value_of(project, "client", ""),
        "date": date.today().strftime("%d/%m/%Y"), "scale": format_scale(spec.get("scale", 1)),
        "units": spec.get("units", "mm"), "view": spec.get("view", ""), "paper": spec.get("paper", ""),
    }
    values.update((spec.get("sheet") or {}).get("fields") or {})
    values.update(value_of(document, "title_overrides") or {})
    return values


def _format(template: str, values: dict) -> str:
    """Fill {placeholders} (missing -> ""), then drop empty ' | ' segments."""
    s = template.format_map(values)
    if "|" in s:
        s = " | ".join(part.strip() for part in s.split("|") if part.strip())
    return s.strip()


# ------------------------------------------------------------------ drawing
def _pick_field(field: dict, language: str) -> list[str]:
    """Which of the field's en/he templates to draw in this language mode."""
    has = [lang for lang in ("en", "he") if field.get(lang)]
    if language == "both" or len(has) < 2:
        return has
    return [language]


def _rect_poly(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def draw_titleblock(msp, template: dict, values: dict, scale: float, units: str, *,
                    origin=(0.0, 0.0), language: str = "both") -> dict:
    """Draw frame, title-block rectangles and fields in model space; `origin` = frame bottom-left.

    Returns model-space rectangles (x0, y0, x1, y1): paper, frame, drawing_area and legend_area (if any).
    """
    k = float(scale) * UNIT_FACTOR[units]
    ox, oy = origin
    fw, fh, m = template["frame"]["w"], template["frame"]["h"], template["margin"]

    def pt(x, y) -> Vec2:
        return Vec2(ox + x * k, oy + y * k)

    msp.add_lwpolyline([pt(*p) for p in _rect_poly(0, 0, fw, fh)], close=True,
                       dxfattribs={"layer": LAYER, "const_width": template["frame_width"] * k})
    for b in template["blocks"]:
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        msp.add_lwpolyline([pt(*p) for p in _rect_poly(x, y, x + w, y + h)], close=True,
                           dxfattribs={"layer": LAYER})
        for ln in b.get("lines", []):
            if "y" in ln:
                p1, p2 = pt(x + ln.get("x0", 0), y + ln["y"]), pt(x + ln.get("x1", w), y + ln["y"])
            else:
                p1, p2 = pt(x + ln["x"], y + ln.get("y0", 0)), pt(x + ln["x"], y + ln.get("y1", h))
            msp.add_line(p1, p2, dxfattribs={"layer": LAYER})

    filled = defaultdict(str, {key: "" if v is None else str(v) for key, v in values.items()})
    for field in template["fields"]:
        h = field.get("h", 2.5)
        w = field.get("w", fw - field["x"] - 2)
        texts = {lang: _format(field[lang], filled) for lang in _pick_field(field, language)}
        texts = {lang: s for lang, s in texts.items() if s}
        both = len(texts) == 2
        if both and "he_y" not in field:          # en left + he right on one line: shrink to fit
            unit = text_width(texts["en"], 1.0) + text_width(texts["he"], 1.0)
            h = min(h, (w - SAME_LINE_GAP) / unit)
        en_last = field["y"]                     # baseline of the last English line (a wrapped title grows down)
        for lang, s in texts.items():
            y = field["y"]
            if lang == "he" and both and "he_y" in field:
                y = min(field["he_y"], en_last - LINE_SPACING * h)
            x, align = (field["x"], field.get("align", "left")) if lang == "en" else (field["x"] + w, "right")
            if align == "center":
                x = field["x"] + w / 2
            lines = wrap(s, h, w) if text_width(s, h) > w else [s]
            en_last = y - (len(lines) - 1) * LINE_SPACING * h
            if len(lines) == 1:
                put_text(msp, s, pt(x, y), h * k, layer=LAYER, align=align)
            else:
                put_text(msp, "\\P".join(lines), pt(x, y + h), h * k, layer=LAYER, align=f"TOP_{align.upper()}")

    rects = {"paper": (*pt(-m, -m), *pt(fw + m, fh + m)), "frame": (*pt(0, 0), *pt(fw, fh))}
    for key in ("drawing_area", "legend_area"):
        if key in template:
            a = template[key]
            rects[key] = (*pt(a["x"], a["y"]), *pt(a["x"] + a["w"], a["y"] + a["h"]))
    return rects
