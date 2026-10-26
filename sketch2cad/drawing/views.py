"""Multi-view sheets. Experimental: real plans can still need a scale set by hand.

One paperspace layout holds the title block, the legend, the notes and a VIEWPORT
per view. Model space keeps every view at true size, separated so the viewports
do not see each other. Text in a view is paper height times that view's scale
(ezdxf has no annotative text). Titles and the scale label are paper-space text
at ISO 3098 heights. A view scale of "NTS" is fitted to paper and labelled NTS.
"auto" keeps each role at a readable scale (detail 1:50, section 1:100, plan 1:500)
and grows a side view only into space the main view has left.
"""
from __future__ import annotations

import math
import re

from ezdxf import bbox as ezbbox
from ezdxf.math import Matrix44, Vec2

from .. import templates
from . import components, dims, legend, place
from .context import LTSCALE_FACTOR, DrawContext
from .layout import content_extents
from .text import LINE_SPACING, put_text, text_width

ELEMENT_ERRORS = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError)
LAYOUT_NAME = "SHEET"
VIEW_PAD_MM = 2.0
TITLE_H = 3.5
SCALE_H = 2.5
FRAME_CLEAR_MM = 10.0                     # viewports and their titles stay this far inside the frame
VIEW_GAP_MM = 8.0
CAPTION_GAP = 1.5                         # viewport top → scale baseline
RULE_GAP = 0.8                            # scale cap-height → underline
TITLE_GAP = 1.0                           # underline → title baseline
NTS_FIT_MM = 80.0
TITLEBLOCK_GAP_MM = 2.0                   # viewports stay this far off the title-block rectangles
# Denominators `scale: "auto"` may pick, and the only ones a fit warning may name.
FIT_SCALES = (50, 100, 125, 200, 250, 500, 1000)
# Coarsest readable denominator for a role. A detail stays at 1:50 or larger, and so on.
_ROLE_FLOOR = (("detail", 50.0), ("section", 100.0), ("elevation", 100.0), ("profile", 100.0), ("plan", 500.0))


def _draw_elements(ctx: DrawContext, elements) -> None:
    for index, el in enumerate(elements or []):
        if not isinstance(el, dict):
            ctx.warnings.append(f"element {index + 1} skipped: not an object")
            continue
        try:
            components.draw_element(ctx, el)
        except ELEMENT_ERRORS as exc:
            ctx.warnings.append(f"element {el.get('id', index + 1)!r} ({el.get('type')}) skipped: {exc!r}")


def _finish_labels(ctx: DrawContext) -> None:
    if ctx.nudge_labels:
        place.nudge_dimensions(ctx)
        place.place_labels(ctx)
        # A label that fell back onto its spec point can cover dimension text. Slide that text off it.
        place.nudge_dimensions(ctx)


def _label_strings(el: dict) -> list[str]:
    found = []
    for key in ("label", "text"):
        value = el.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value)
        elif isinstance(value, dict):
            found.extend(part for part in (value.get("en"), value.get("he")) if part)
    return found


def _rough_bounds(elements, text_scale: float | None = None, unit: float = 1.0):
    """Geometry bounds. With `text_scale`, label width is included so an NTS view is not too narrow."""
    xs, ys = [], []

    def add(x, y, radius=0.0):
        xs.extend((float(x) - radius, float(x) + radius))
        ys.extend((float(y) - radius, float(y) + radius))

    for el in elements or []:
        if not isinstance(el, dict):
            continue
        radius = float(el.get("radius") or 0)
        for key in ("at", "center"):
            point = el.get(key)
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                add(point[0], point[1], radius)
        for point in list(el.get("points") or []) + list(el.get("corners") or []):
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                add(point[0], point[1])
        strings = _label_strings(el)
        if text_scale and strings:
            anchor = el.get("label_at") or el.get("at") or el.get("center")
            if not (isinstance(anchor, (list, tuple)) and len(anchor) >= 2):
                pts = list(el.get("points") or []) + list(el.get("corners") or [])
                anchor = pts[len(pts) // 2] if pts else None
            if isinstance(anchor, (list, tuple)) and len(anchor) >= 2:
                width = max(text_width(part, 2.5) for part in strings) * text_scale * unit
                height = 2.5 * 2 * LINE_SPACING * text_scale * unit
                add(anchor[0], anchor[1], 0)
                xs.extend((float(anchor[0]) - width / 2, float(anchor[0]) + width / 2))
                ys.extend((float(anchor[1]), float(anchor[1]) + height))
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _is_auto(view: dict) -> bool:
    scale = view.get("scale")
    return isinstance(scale, str) and scale.strip().upper() == "AUTO"


def _resolve_scale(view: dict, unit: float, sheet_scale: float, free=None,
                   language: str = "both") -> tuple[float, bool]:
    """Return (numeric scale denominator, not_to_scale).

    ``auto`` is the largest standard view scale whose slot fits `free` (paper millimetres).
    It does not copy the sheet scale. With no `free`, the view only has to fit a large sheet.
    """
    scale = view.get("scale")
    if isinstance(scale, str) and scale.strip().upper() == "NTS":
        bounds = _rough_bounds(view.get("elements"))
        fit = float(view.get("fit_mm") or NTS_FIT_MM)
        if not bounds or fit <= 0:
            return float(sheet_scale), True
        longer = max(bounds[2] - bounds[0], bounds[3] - bounds[1], unit)
        return longer / (fit * unit), True
    if _is_auto(view):
        floor = _view_floor(view)
        fw, fh = (float(free[0]), float(free[1])) if free else (1.0e6, 1.0e6)
        for candidate in FIT_SCALES:
            if candidate > floor + 1e-6:
                break
            slot_w, slot_h = _slot_for_scale(view, float(candidate), unit, language)
            if slot_w <= fw + 1e-6 and slot_h <= fh + 1e-6:
                return float(candidate), False
        return floor, False
    return float(scale), False


def _block_name(doc, view_id: str) -> str:
    base = "S2C_VIEW_" + re.sub(r"\W+", "_", str(view_id))[:32]
    name, n = base, 2
    while name in doc.blocks:
        name = f"{base}_{n}"
        n += 1
    return name


def _draw_model_view(ctx: DrawContext, view: dict, cursor_x: float, base_y: float):
    """Draw one extra view at true size and park it beside the primary view."""
    numeric, nts = _resolve_scale(view, ctx.u, ctx.scale, language=ctx.language)
    name = _block_name(ctx.doc, view.get("id", "view"))
    block = ctx.doc.blocks.new(name)
    child = ctx.fork(block, numeric)
    child.nudge_labels = ctx.nudge_labels
    _draw_elements(child, view.get("elements"))
    try:
        dims.draw_dimensions(child, view.get("dimensions") or [])
    except ELEMENT_ERRORS as exc:
        ctx.warnings.append(f"view {view.get('id')!r}: dimensions skipped ({exc!r})")
    # Free text stays put. Labels are placed before the extents, so the viewport
    # window includes the label boxes and the leaders.
    place.draw_fixed_text(child)
    _finish_labels(child)
    ext = ezbbox.extents(block, fast=True)
    if not ext.has_data:
        ctx.doc.blocks.delete_block(name, safe=True)
        ctx.warnings.append(f"view {view.get('id')!r} has nothing to draw")
        return None
    x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    dx, dy = cursor_x - x0, base_y - y0
    shift = Matrix44.translate(dx, dy, 0)
    for entity in list(block):
        try:
            entity.transform(shift)
            entity.move_to_layout(ctx.msp)
        except (AttributeError, TypeError, ValueError) as exc:
            ctx.warnings.append(f"view {view.get('id')!r}: could not place {entity.dxftype()} ({exc})")
    try:
        ctx.doc.blocks.delete_block(name, safe=True)
    except ValueError:
        ctx.warnings.append(f"view {view.get('id')!r}: temporary block {name} was kept")
    return {
        "id": str(view.get("id")),
        "title": view.get("title"),
        "numeric": numeric,
        "nts": nts,
        "bbox": (x0 + dx, y0 + dy, x1 + dx, y1 + dy),
        "fixed": view.get("origin_on_sheet"),
    }


_STANDARD_VIEWS = {
    "plan": ("PLAN", "תוכנית"),
    "elevation": ("ELEVATION", "חזית"),
    "section": ("SECTION", "חתך"),
    "detail": ("DETAIL", "פרט"),
    "profile": ("PROFILE", "חתך אורך"),
}


def _primary_title(spec: dict, language: str) -> str:
    """Bilingual sheet title. A bare word such as ``plan`` becomes ``PLAN / תוכנית``."""
    raw = spec.get("view_title", spec.get("view") or "")
    if isinstance(raw, dict):
        return _title_text(raw, language)
    text = str(raw).strip()
    mapped = _STANDARD_VIEWS.get(text.lower())
    if mapped:
        return _title_text({"en": mapped[0], "he": mapped[1]}, language)
    return text


def _title_text(title, language: str) -> str:
    """One line: ``SECTION A-A / חתך א-א`` when both languages are shown."""
    if isinstance(title, str):
        return title.strip()
    if not isinstance(title, dict):
        return ""
    en = str(title.get("en") or "").strip()
    he = str(title.get("he") or "").strip()
    if language == "he":
        return he or en
    if language == "en":
        return en or he
    if en and he:
        return f"{en} / {he}"
    return en or he


def _scale_label(numeric: float, nts: bool) -> str:
    if nts:
        return "SCALE NTS"
    return f"SCALE 1:{templates.format_scale(numeric)}"


def _caption_band() -> float:
    """Paper mm from the viewport top up to the top of the title line box.

    The title is MTEXT. Its line box is the cap height times the line pitch, so a bilingual
    title stays inside the slot and inside the frame.
    """
    return CAPTION_GAP + SCALE_H + RULE_GAP + TITLE_GAP + TITLE_H * LINE_SPACING


def _window(bbox, k: float) -> tuple[float, float, float, float]:
    """Paper width, paper height, model width and model height of a viewport, with a small pad."""
    x0, y0, x1, y1 = bbox
    pad = VIEW_PAD_MM * k
    world_w = max(x1 - x0, k) + 2 * pad
    world_h = max(y1 - y0, k) + 2 * pad
    return world_w / k, world_h / k, world_w, world_h


def _rects_overlap(a, b, gap: float) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _inside(area, x, y, w, h) -> bool:
    return (x >= area["x"] - 1e-6 and y >= area["y"] - 1e-6
            and x + w <= area["x"] + area["w"] + 1e-6 and y + h <= area["y"] + area["h"] + 1e-6)


def _scan(area, w, h, occupied, gap: float):
    """A free slot if one exists. Candidates sit against the area or an occupied rectangle."""
    if w > area["w"] + 1e-6 or h > area["h"] + 1e-6:
        return None
    xs = [area["x"], area["x"] + area["w"] - w]
    ys = [area["y"], area["y"] + area["h"] - h]
    for other in occupied:
        xs.append(other[2] + gap)
        xs.append(other[0] - gap - w)
        ys.append(other[3] + gap)
        ys.append(other[1] - gap - h)
    best = None
    for x in xs:
        for y in ys:
            if not _inside(area, x, y, w, h) or _hits_occupied(x, y, w, h, occupied, gap):
                continue
            key = (-round(y, 4), round(x, 4))
            if best is None or key < best[0]:
                best = (key, (x, y))
    return None if best is None else best[1]


def _best_free(area, w, h, occupied, gap: float) -> tuple[float, float]:
    """Width and height of the free rectangle this view comes closest to fitting."""
    xs = sorted({area["x"], area["x"] + area["w"]}
                | {edge for other in occupied for edge in (other[0] - gap, other[2] + gap)})
    ys = sorted({area["y"], area["y"] + area["h"]}
                | {edge for other in occupied for edge in (other[1] - gap, other[3] + gap)})
    best = None
    x0_limit, x1_limit = area["x"] - 1e-6, area["x"] + area["w"] + 1e-6
    y0_limit, y1_limit = area["y"] - 1e-6, area["y"] + area["h"] + 1e-6
    for i, x0 in enumerate(xs):
        if x0 < x0_limit:
            continue
        for x1 in xs[i + 1:]:
            if x1 > x1_limit:
                continue
            for j, y0 in enumerate(ys):
                if y0 < y0_limit:
                    continue
                for y1 in ys[j + 1:]:
                    if y1 > y1_limit:
                        continue
                    rect = (x0, y0, x1, y1)
                    if any(_rects_overlap(rect, other, 0) for other in occupied):
                        continue
                    fw, fh = x1 - x0, y1 - y0
                    if fw > 1 and fh > 1:
                        fill = max(w / fw, h / fh)
                        key = (fill, -(fw * fh))
                        if best is None or key < best[0]:
                            best = (key, (fw, fh))
    if best is None:
        return float(area["w"]), float(area["h"])
    return best[1]


def _clear_of_titleblock(area: dict, template: dict) -> dict:
    """Raise the floor (or lower the ceiling) so the packing area misses title-block rectangles."""
    x0, y0 = float(area["x"]), float(area["y"])
    x1, y1 = x0 + float(area["w"]), y0 + float(area["h"])
    for block in template.get("blocks") or []:
        by0 = float(block["y"])
        by1 = by0 + float(block["h"])
        bx0 = float(block["x"])
        bx1 = bx0 + float(block["w"])
        if bx1 <= x0 + 1e-6 or bx0 >= x1 - 1e-6:
            continue
        mid = (y0 + y1) / 2
        if by1 <= mid:
            y0 = max(y0, by1 + TITLEBLOCK_GAP_MM)
        elif by0 >= mid:
            y1 = min(y1, by0 - TITLEBLOCK_GAP_MM)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return dict(area)
    cleared = dict(area)
    cleared["x"], cleared["y"] = x0, y0
    cleared["w"], cleared["h"] = x1 - x0, y1 - y0
    return cleared


def _hits_titleblock(x, y, w, h, template: dict) -> bool:
    rect = (x, y, x + w, y + h)
    for block in template.get("blocks") or []:
        other = (float(block["x"]), float(block["y"]),
                 float(block["x"]) + float(block["w"]), float(block["y"]) + float(block["h"]))
        if _rects_overlap(rect, other, 0):
            return True
    return False


def _element_points(el: dict) -> list[tuple[float, float, float]]:
    """(x, y, radius) of one element's geometry. `fit_mm` is not a coordinate."""
    found = []
    radius = float(el.get("radius") or 0)
    for key in ("at", "center"):
        point = el.get(key)
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            found.append((float(point[0]), float(point[1]), radius))
    for point in list(el.get("points") or []) + list(el.get("corners") or []):
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            found.append((float(point[0]), float(point[1]), 0.0))
    return found


def _role_floor(name: str) -> float:
    """Coarsest readable denominator named by `name` (a title or an id)."""
    low = name.lower()
    for role, floor in _ROLE_FLOOR:
        if role in low:
            return floor
    return 500.0


def _view_floor(view: dict) -> float:
    title = view.get("title")
    if isinstance(title, dict):
        title = f"{title.get('en') or ''} {title.get('he') or ''}"
    return _role_floor(f"{view.get('id') or ''} {title or ''}")


def primary_floor(spec: dict) -> float:
    """Coarsest readable denominator of the main view (`plan` unless the view name says otherwise)."""
    raw = spec.get("view_title", spec.get("view") or "plan")
    if isinstance(raw, dict):
        raw = f"{raw.get('en') or ''} {raw.get('he') or ''}"
    return _role_floor(str(raw or "plan"))


def _estimate_bounds(view: dict, scale: float, unit: float):
    """Model bounds used to try an auto scale.

    Geometry sets the box. A callout or a label counts only when it sits next to that geometry,
    so a note parked on the site plan does not turn a few-metre detail into a 200 m drawing.
    `fit_mm` is an NTS paper size and is not part of the box. A move allowance is added in paper.
    """
    geom_x, geom_y = [], []
    extras: list[tuple[float, float, float, float]] = []

    def remember_text(el, x, y):
        strings = _label_strings(el)
        if not strings:
            extras.append((x, y, 0.0, 0.0))
            return
        width = max(text_width(part, 2.5) for part in strings) * scale * unit
        height = 2.5 * 2 * LINE_SPACING * scale * unit
        extras.append((x, y, width, height))

    for el in view.get("elements") or []:
        if not isinstance(el, dict):
            continue
        points = _element_points(el)
        if el.get("type") == "text":
            for x, y, _radius in points:
                remember_text(el, x, y)
            continue
        for x, y, radius in points:
            geom_x.extend((x - radius, x + radius))
            geom_y.extend((y - radius, y + radius))
        anchor = el.get("label_at")
        if isinstance(anchor, (list, tuple)) and len(anchor) >= 2:
            remember_text(el, float(anchor[0]), float(anchor[1]))
    if not geom_x and not extras:
        return None
    if geom_x:
        x0, y0, x1, y1 = min(geom_x), min(geom_y), max(geom_x), max(geom_y)
        span = max(x1 - x0, y1 - y0, unit)
        reach = max(span, 4.0 if unit < 1 else 4000.0)

        def near(x, y) -> bool:
            return x0 - reach <= x <= x1 + reach and y0 - reach <= y <= y1 + reach

        xs, ys = list(geom_x), list(geom_y)
        for x, y, width, height in extras:
            if not near(x, y):
                continue
            xs.extend((x - width / 2, x + width / 2))
            ys.extend((y, y + height))
        for dim in view.get("dimensions") or []:
            if not isinstance(dim, dict):
                continue
            for point in dim.get("points") or []:
                if isinstance(point, (list, tuple)) and len(point) >= 2 and near(float(point[0]), float(point[1])):
                    xs.append(float(point[0]))
                    ys.append(float(point[1]))
            base = dim.get("base")
            if isinstance(base, (int, float)) and not isinstance(base, bool):
                if str(dim.get("axis")) == "v" and near(float(base), (y0 + y1) / 2):
                    xs.append(float(base))
                elif str(dim.get("axis")) != "v" and near((x0 + x1) / 2, float(base)):
                    ys.append(float(base))
    else:
        xs = [item[0] for item in extras]
        ys = [item[1] for item in extras]
        for _x, _y, width, height in extras:
            xs.extend((_x - width / 2, _x + width / 2))
            ys.extend((_y, _y + height))
    if not xs:
        return None
    pad = (place.MAX_MOVE_MM + VIEW_PAD_MM) * scale * unit
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _geometry_reach(view: dict, unit: float):
    """(x0, y0, x1, y1, reach) of the view's geometry, or None when it has none."""
    xs, ys = [], []
    for el in view.get("elements") or []:
        if not isinstance(el, dict) or el.get("type") == "text":
            continue
        for x, y, radius in _element_points(el):
            xs.extend((x - radius, x + radius))
            ys.extend((y - radius, y + radius))
    if not xs:
        return None
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    span = max(x1 - x0, y1 - y0, unit)
    return x0, y0, x1, y1, max(span, 4.0 if unit < 1 else 4000.0)


def _auto_view_content(view: dict, unit: float) -> dict:
    """The view without a callout, label or dimension base that sits far from its geometry.

    Those points are not part of a few-metre detail. `fit_mm` is left untouched and is only
    read for an NTS view.
    """
    reach_box = _geometry_reach(view, unit)
    if reach_box is None:
        return view
    x0, y0, x1, y1, reach = reach_box

    def near(x: float, y: float) -> bool:
        return x0 - reach <= x <= x1 + reach and y0 - reach <= y <= y1 + reach

    elements = []
    for el in view.get("elements") or []:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "text":
            points = _element_points(el)
            if points and not any(near(x, y) for x, y, _radius in points):
                continue
            elements.append(el)
            continue
        anchor = el.get("label_at")
        if isinstance(anchor, (list, tuple)) and len(anchor) >= 2 and not near(float(anchor[0]), float(anchor[1])):
            el = {key: value for key, value in el.items() if key != "label_at"}
        elements.append(el)
    dimensions = []
    for dim in view.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        points = [point for point in (dim.get("points") or [])
                  if isinstance(point, (list, tuple)) and len(point) >= 2]
        if points and not any(near(float(point[0]), float(point[1])) for point in points):
            continue
        base = dim.get("base")
        if isinstance(base, (int, float)) and not isinstance(base, bool) and points:
            cx = sum(float(point[0]) for point in points) / len(points)
            cy = sum(float(point[1]) for point in points) / len(points)
            far = (str(dim.get("axis")) == "v" and not near(float(base), cy)) or (
                str(dim.get("axis")) != "v" and not near(cx, float(base)))
            if far:
                dim = {key: value for key, value in dim.items() if key != "base"}
        dimensions.append(dim)
    return {**view, "elements": elements, "dimensions": dimensions}


def _next_fit_scale(scale: float) -> float | None:
    for candidate in FIT_SCALES:
        if candidate > scale + 1e-6:
            return float(candidate)
    return None


def _fit_denominator(needed: float) -> float | None:
    """Smallest standard denominator at least `needed`, or None past the series."""
    for candidate in FIT_SCALES:
        if candidate + 1e-6 >= needed:
            return float(candidate)
    return None


def _slot_for_scale(view: dict, scale: float, unit: float, language: str,
                    nts: bool = False) -> tuple[float, float]:
    """Paper slot (width, height) of `view` at `scale`, including its caption."""
    bounds = _estimate_bounds(view, scale, unit) or (0.0, 0.0, max(unit, 1e-6), max(unit, 1e-6))
    k = max(float(scale), 1e-9) * unit
    pw, ph, _, _ = _window(bounds, k)
    title = _title_text(view.get("title"), language)
    label = _scale_label(scale, nts)
    caption_w = max(text_width(title, TITLE_H) if title else 0.0, text_width(label, SCALE_H))
    return max(pw, caption_w), ph + _caption_band()


def _preferred_side_slot(view: dict, unit: float, language: str, area: dict) -> tuple[float, float]:
    """Slot reserved for one side view before the main view is scaled.

    An auto view reserves its readable floor (the smallest paper size that stays readable).
    A number or NTS reserves the slot that scale actually needs.
    """
    raw = view.get("scale")
    if isinstance(raw, str) and raw.strip().upper() == "NTS":
        numeric, _ = _resolve_scale(view, unit, float(FIT_SCALES[-1]))
        return _slot_for_scale(view, numeric, unit, language, nts=True)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return _slot_for_scale(view, float(raw), unit, language)
    return _slot_for_scale(view, _view_floor(view), unit, language)


def _packing_area(spec: dict, template: dict) -> dict:
    """The rectangle `compose` packs views into, after the title block, frame clear and notes column."""
    needs_column = bool(spec.get("legend") and not spec.get("legend_at")) or (
        _has_notes(spec.get("notes")) and not spec.get("notes_at"))
    area = _clear_of_titleblock(template.get("drawing_area") or {"x": 0, "y": 0, "w": 380, "h": 240}, template)
    area = _reserve_side(area, template, needs_column)
    frame = template.get("frame") or {"w": 400, "h": 277}
    return _clip_area(area, float(frame["w"]), float(frame["h"]))


def primary_free_mm(spec: dict, template: dict, language: str) -> tuple[float, float] | None:
    """Paper millimetres the primary view may fill, or None when the sheet has no other views.

    Side views are reserved at their readable floor, not at the largest scale that fits.
    The primary is scaled into what that reservation leaves: the row above those slots, which
    is the layout `compose` tries first, or the column beside them when a row does not fit.
    The returned size is the content box, after the primary caption and the viewport pad, so
    it matches `content_extents` against `_paper_fits`.
    """
    views = [view for view in (spec.get("views") or []) if isinstance(view, dict)]
    if not views:
        return None
    area = _packing_area(spec, template)
    unit = 0.001 if str(spec.get("units", "mm")).lower() == "m" else 1.0
    slots = [_preferred_side_slot(view, unit, language, area) for view in views]
    gap = VIEW_GAP_MM
    pad = 2 * VIEW_PAD_MM
    band = _caption_band()
    row_h = max(height for _, height in slots)
    row_w = sum(width for width, _ in slots) + gap * (len(slots) - 1)
    col_w = max(width for width, _ in slots)
    stack_h = sum(height for _, height in slots) + gap * (len(slots) - 1)

    def room(slot_w: float, slot_h: float) -> tuple[float, float]:
        return max(20.0, slot_w - pad), max(20.0, slot_h - band - pad)

    if row_w <= float(area["w"]) + 1e-6 and row_h + gap < float(area["h"]) - band - pad:
        return room(float(area["w"]), float(area["h"]) - gap - row_h)
    if stack_h <= float(area["h"]) + 1e-6 and col_w + gap < float(area["w"]) - pad:
        return room(float(area["w"]) - gap - col_w, float(area["h"]))
    return max(40.0, float(area["w"]) * 0.6), max(40.0, float(area["h"]) * 0.55)


def _side_free(area: dict, primary: dict, frame_w: float, frame_h: float) -> tuple[float, float]:
    """Paper rectangle one side view can occupy once the primary has taken its slot."""
    area = _clip_area(area, frame_w, frame_h)
    gap = VIEW_GAP_MM
    under = float(area["h"]) - float(primary["slot_h"]) - gap
    beside = float(area["w"]) - float(primary["slot_w"]) - gap
    options = []
    if under >= 20:
        options.append((float(area["w"]), under))
    if beside >= 20:
        options.append((beside, float(area["h"])))
    if options:
        return max(options, key=lambda wh: wh[0] * wh[1])
    return max(40.0, float(area["w"]) * 0.45), max(40.0, float(area["h"]) * 0.35)


def _clip_area(area: dict, frame_w: float, frame_h: float) -> dict:
    """Keep auto-placed views at least FRAME_CLEAR_MM inside the frame."""
    x0 = max(float(area["x"]), FRAME_CLEAR_MM)
    y0 = max(float(area["y"]), FRAME_CLEAR_MM)
    x1 = min(float(area["x"]) + float(area["w"]), frame_w - FRAME_CLEAR_MM)
    y1 = min(float(area["y"]) + float(area["h"]), frame_h - FRAME_CLEAR_MM)
    if x1 - x0 < 40 or y1 - y0 < 40:
        return dict(area)
    clipped = dict(area)
    clipped["x"], clipped["y"] = x0, y0
    clipped["w"], clipped["h"] = x1 - x0, y1 - y0
    return clipped


def _spread(start: float, total: float, sizes: list[float], gap: float) -> list[float]:
    """Place sizes along an axis, at least `gap` apart, leftover space shared including the ends."""
    if not sizes:
        return []
    spare = total - (sum(sizes) + gap * (len(sizes) - 1))
    pad = spare / (len(sizes) + 1) if spare > 0 else 0.0
    cursor = start + pad
    out = []
    for size in sizes:
        out.append(cursor)
        cursor += size + gap + pad
    return out


def _hits_occupied(x, y, w, h, occupied, gap: float) -> bool:
    rect = (x, y, x + w, y + h)
    return any(_rects_overlap(rect, other, gap) for other in occupied)


def _row_layout(area, primary, rest) -> dict | None:
    """Plan above, other views in a row underneath. Slot tops align, so title baselines match."""
    gap = VIEW_GAP_MM
    row_h = max(item["slot_h"] for item in rest)
    row_w = sum(item["slot_w"] for item in rest) + gap * (len(rest) - 1)
    if primary["slot_h"] + gap + row_h > area["h"] + 1e-6:
        return None
    if max(primary["slot_w"], row_w) > area["w"] + 1e-6:
        return None
    top_h = area["h"] - gap - row_h
    px = area["x"] + max(0.0, (area["w"] - primary["slot_w"]) / 2)
    py = area["y"] + row_h + gap + max(0.0, (top_h - primary["slot_h"]) / 2)
    if not _inside(area, px, py, primary["slot_w"], primary["slot_h"]):
        return None
    xs = _spread(area["x"], area["w"], [item["slot_w"] for item in rest], gap)
    top = area["y"] + row_h
    placed = {primary["key"]: (px, py)}
    for item, x in zip(rest, xs):
        y = top - item["slot_h"]
        if not _inside(area, x, y, item["slot_w"], item["slot_h"]):
            return None
        placed[item["key"]] = (x, y)
    return placed


def _column_layout(area, primary, rest) -> dict | None:
    """Plan on the left, other views stacked in a column on the right and spread over the height."""
    gap = VIEW_GAP_MM
    col_w = max(item["slot_w"] for item in rest)
    stack_h = sum(item["slot_h"] for item in rest) + gap * (len(rest) - 1)
    if primary["slot_w"] + gap + col_w > area["w"] + 1e-6:
        return None
    if max(primary["slot_h"], stack_h) > area["h"] + 1e-6:
        return None
    left_w = area["w"] - gap - col_w
    px = area["x"] + max(0.0, (left_w - primary["slot_w"]) / 2)
    py = area["y"] + max(0.0, (area["h"] - primary["slot_h"]) / 2)
    if not _inside(area, px, py, primary["slot_w"], primary["slot_h"]):
        return None
    ys = _spread(area["y"], area["h"], [item["slot_h"] for item in rest], gap)
    right = area["x"] + left_w + gap
    placed = {primary["key"]: (px, py)}
    for item, y in zip(rest, ys):
        x = right + max(0.0, (col_w - item["slot_w"]) / 2)
        if not _inside(area, x, y, item["slot_w"], item["slot_h"]):
            return None
        placed[item["key"]] = (x, y)
    return placed


def _pack(area, items: list[dict], frame_w: float, frame_h: float) -> dict[str, tuple[float, float]]:
    """Slot bottom-left of each view, in frame millimetres.

    The plan stays large. Other views share a row under it when that fits (their titles then share a
    baseline), otherwise a column beside it. Either way they stay FRAME_CLEAR_MM inside the frame.
    """
    area = _clip_area(area, frame_w, frame_h)
    occupied = []
    placed = {}

    def take(item, pos, overflow=False):
        x, y = pos
        if overflow or not _inside(area, x, y, item["slot_w"], item["slot_h"]):
            item["skip"] = True
            item["overflow"] = True
            return
        placed[item["key"]] = (x, y)
        occupied.append((x, y, x + item["slot_w"], y + item["slot_h"]))
        item["overflow"] = False

    fixed = [item for item in items if item.get("fixed")]
    floating = [item for item in items if not item.get("fixed")]
    for item in fixed:
        # origin_on_sheet is the viewport's bottom-left; the slot may be wider than the viewport.
        overhang = max(0.0, item["slot_w"] - item["vp_w"]) / 2
        take(item, (float(item["fixed"][0]) - overhang, float(item["fixed"][1])))
    primary = next((item for item in floating if item["key"] == "primary"), None)
    rest = [item for item in floating if item["key"] != "primary"]
    layout = None
    if primary and rest and not fixed:
        for candidate in (_row_layout(area, primary, rest), _column_layout(area, primary, rest)):
            if candidate is None:
                continue
            if any(_hits_occupied(candidate[item["key"]][0], candidate[item["key"]][1],
                                  item["slot_w"], item["slot_h"], occupied, VIEW_GAP_MM)
                   for item in [primary, *rest]):
                continue
            layout = candidate
            break
    elif primary and not rest:
        cx = area["x"] + max(0.0, (area["w"] - primary["slot_w"]) / 2)
        cy = area["y"] + max(0.0, (area["h"] - primary["slot_h"]) / 2)
        if not _hits_occupied(cx, cy, primary["slot_w"], primary["slot_h"], occupied, VIEW_GAP_MM):
            layout = {primary["key"]: (cx, cy)}
    if layout:
        for item in [primary, *rest] if primary else []:
            if item is None or item["key"] not in layout:
                continue
            take(item, layout[item["key"]])
        return placed
    pending = ([primary] if primary else []) + rest
    for item in pending:
        if item is None:
            continue
        found = _scan(area, item["slot_w"], item["slot_h"], occupied, VIEW_GAP_MM)
        if found and _inside(area, found[0], found[1], item["slot_w"], item["slot_h"]):
            take(item, found)
        else:
            item["skip"] = True
            item["overflow"] = True
            item["free"] = _best_free(area, item["slot_w"], item["slot_h"], occupied, VIEW_GAP_MM)
    return placed


def _write_caption(msp, origin: Vec2, width: float, height: float, title: str, scale_label: str,
                   max_top: float | None = None) -> None:
    """Title, an underline, and ``SCALE 1:50`` centred above the viewport.

    The title's line box stays at or below `max_top` (the frame). A measured MTEXT width keeps
    a bilingual title on one line, inside that box.
    """
    top = origin.y + height
    scale_y = top + CAPTION_GAP
    rule_y = scale_y + SCALE_H + RULE_GAP
    title_y = rule_y + TITLE_GAP
    if max_top is not None and title:
        limit = max_top - TITLE_H * LINE_SPACING
        if title_y > limit:
            shift = title_y - limit
            scale_y -= shift
            rule_y -= shift
            title_y -= shift
    center = origin.x + width / 2
    put_text(msp, scale_label, (center, scale_y), SCALE_H, layer="TEXT", align="CENTER", nowrap=True)
    if not title:
        return
    title_w = text_width(title, TITLE_H)
    put_text(msp, title, (center, title_y), TITLE_H, layer="TEXT", align="CENTER", nowrap=True,
             box_width=title_w)
    half = title_w / 2
    msp.add_line((center - half, rule_y), (center + half, rule_y), dxfattribs={"layer": "TEXT"})


def _reserve_side(area: dict, template: dict, needed: bool) -> dict:
    """Shrink the packing area when the legend has no column of its own."""
    if not needed or template.get("legend_area"):
        return dict(area)
    reserve = min(110.0, area["w"] * 0.28)
    if area["w"] - reserve < 40:
        return dict(area)
    shrunk = dict(area)
    shrunk["w"] = area["w"] - reserve
    shrunk["legend_box"] = {"x": area["x"] + shrunk["w"], "y": area["y"], "w": reserve, "h": area["h"]}
    return shrunk


def _area_with_column(area: dict) -> dict:
    """The packing area plus the notes column, when that column was held back."""
    box = area.get("legend_box")
    if not isinstance(box, dict):
        return area
    widened = dict(area)
    widened["w"] = float(area["w"]) + float(box["w"])
    widened.pop("legend_box", None)
    return widened


def _assign_auto_scales(auto_specs, primary_item, fixed_items, auto_item, area, frame_size,
                        unit: float, language: str) -> tuple[dict, dict]:
    """Readable floor per auto view, then a finer scale when the sheet still packs.

    The primary scale is already the largest that fits the space these floors leave. Each side
    view starts at its own floor and grows (a smaller denominator) only while every view still
    packs. Nothing is made coarser than its floor. When the floors do not pack, the notes
    column is opened and tried.
    """
    if not auto_specs:
        return {}, area
    fw, fh = frame_size["w"], frame_size["h"]
    scales = {str(view.get("id")): _view_floor(view) for view in auto_specs}

    def fits(scale_map: dict, pack_area: dict) -> bool:
        items = [primary_item(), *fixed_items()]
        for view in auto_specs:
            vid = str(view.get("id"))
            items.append(auto_item(view, scale_map[vid]))
        _pack(pack_area, items, fw, fh)
        return not any(item.get("skip") for item in items)

    pack_area = area
    if not fits(scales, pack_area):
        widened = _area_with_column(area)
        if widened is not area:
            pack_area = widened
    for view in auto_specs:
        vid = str(view.get("id"))
        floor = scales[vid]
        for candidate in FIT_SCALES:
            if candidate > floor + 1e-6:
                break
            if candidate + 1e-6 >= floor:
                break
            trial = {**scales, vid: float(candidate)}
            if fits(trial, pack_area):
                scales[vid] = float(candidate)
                break
    return scales, pack_area


def _scale_advice(item: dict, free_w: float, free_h: float) -> float | None:
    """A larger standard denominator that really fits `free_w` x `free_h`, or None.

    NTS views get no number. A denominator at or below the current scale is not named:
    that would be the same drawing or a larger one, and this view already did not fit.
    """
    if item.get("nts"):
        return None
    numeric = float(item["numeric"])
    k = float(item["k"])
    if k <= 1e-12 or numeric <= 0 or free_w <= 1 or free_h <= 1:
        return None
    x0, y0, x1, y1 = item["bbox"]
    model_w = max(x1 - x0, 0.0) / k * numeric
    model_h = max(y1 - y0, 0.0) / k * numeric
    pad = 2 * VIEW_PAD_MM
    band = _caption_band()
    caption_w = float(item.get("caption_w") or 0.0)
    if caption_w > free_w + 1e-6 or free_w <= pad + 1 or free_h <= pad + band + 1:
        return None
    need_w = model_w / (free_w - pad) if model_w > 0 else 1.0
    need_h = model_h / (free_h - pad - band) if model_h > 0 else 1.0
    start = _fit_denominator(math.ceil(max(need_w, need_h) - 1e-6))
    if start is None:
        return None
    for scale in FIT_SCALES:
        if scale + 1e-9 < start or scale <= numeric + 1e-6:
            continue
        paper_w = model_w / scale + pad
        paper_h = model_h / scale + pad
        if max(paper_w, caption_w) <= free_w + 1e-6 and paper_h + band <= free_h + 1e-6:
            return float(scale)
    return None


def _largest_hole(area: dict, occupied: list, gap: float = 0.0):
    """(x, y, w, h) of the largest empty rectangle inside `area`, or None."""
    xs = sorted({float(area["x"]), float(area["x"]) + float(area["w"])}
                | {edge for other in occupied for edge in (other[0] - gap, other[2] + gap)})
    ys = sorted({float(area["y"]), float(area["y"]) + float(area["h"])}
                | {edge for other in occupied for edge in (other[1] - gap, other[3] + gap)})
    best = None
    x0_limit = float(area["x"]) - 1e-6
    x1_limit = float(area["x"]) + float(area["w"]) + 1e-6
    y0_limit = float(area["y"]) - 1e-6
    y1_limit = float(area["y"]) + float(area["h"]) + 1e-6
    for i, x0 in enumerate(xs):
        if x0 < x0_limit:
            continue
        for x1 in xs[i + 1:]:
            if x1 > x1_limit:
                continue
            for j, y0 in enumerate(ys):
                if y0 < y0_limit:
                    continue
                for y1 in ys[j + 1:]:
                    if y1 > y1_limit:
                        continue
                    rect = (x0, y0, x1, y1)
                    if any(_rects_overlap(rect, other, 0) for other in occupied):
                        continue
                    fw, fh = x1 - x0, y1 - y0
                    if fw < 8 or fh < 8:
                        continue
                    size = fw * fh
                    if best is None or size > best[0]:
                        best = (size, (x0, y0, fw, fh))
    return None if best is None else best[1]


def _fit_into(area: dict, item: dict, occupied: list, template: dict):
    """A viewport that shows all of `item` inside a free rectangle, or None.

    Returns (vp_x, y, pw, ph, slot_w, slot_h) in frame millimetres. The caption sits in the
    same rectangle. A short strip and the notes column both count: the packing gap is not
    required, so a 32 mm band can still hold a view.
    """
    holes = []
    hole = _largest_hole(area, occupied, 0.0)
    if hole:
        holes.append(hole)
    legend = area.get("legend_box")
    if isinstance(legend, dict):
        holes.append((float(legend["x"]), float(legend["y"]), float(legend["w"]), float(legend["h"])))
    x0, y0, x1, y1 = item["bbox"]
    model_w = max(x1 - x0, 1e-6)
    model_h = max(y1 - y0, 1e-6)
    band = _caption_band()
    best = None
    for hx, hy, fw, fh in holes:
        content_h = fh - band
        if fw < 8 or content_h < 8:
            continue
        ph = content_h
        pw = ph * (model_w / model_h)
        if pw > fw:
            pw = fw
            ph = pw * (model_h / model_w)
        # A long thin view would collapse below a readable strip. Keep at least 8 mm and let the
        # viewport show the whole model with a margin, instead of dropping the view.
        if pw < 8 <= fw:
            pw = min(fw, 8.0)
        if ph < 8 <= content_h:
            ph = min(content_h, 8.0)
        if pw < 8 or ph < 8 or ph + band > fh + 1e-6:
            continue
        slot_w, slot_h = pw, ph + band
        x = hx + max(0.0, (fw - slot_w) / 2)
        y = hy + max(0.0, (fh - slot_h) / 2)
        if _hits_titleblock(x, y, pw, ph, template):
            continue
        score = pw * ph
        if best is None or score > best[0]:
            best = (score, (x, y, pw, ph, slot_w, slot_h))
    return None if best is None else best[1]


def compose(ctx: DrawContext, spec: dict, template: dict, values: dict, language: str,
            primary_bbox=None) -> tuple[dict, str]:
    """Draw extra views, then a 1:1 paper-space sheet. Returns (title-block rects, layout name).

    `primary_bbox` is the primary view measured after its labels and leaders were placed.
    """
    px0, py0, px1, py1 = content_extents(ctx) if primary_bbox is None else primary_bbox
    if px1 - px0 < 1e-9 and py1 - py0 < 1e-9:
        px1, py1 = px0 + ctx.k, py0 + ctx.k
    span = max(px1 - px0, py1 - py0, ctx.k)
    gap_model = span * 0.3 + ctx.paper(30)
    cursor = px1 + gap_model
    raw = [view for view in (spec.get("views") or []) if isinstance(view, dict)]
    fixed_specs = [view for view in raw if not _is_auto(view)]
    auto_specs = [view for view in raw if _is_auto(view)]
    views = []

    def draw_at(view, auto: bool = False) -> None:
        nonlocal cursor
        record = _draw_model_view(ctx, view, cursor, py0)
        if record:
            record["auto"] = auto
            views.append(record)
            cursor = record["bbox"][2] + gap_model

    for view in fixed_specs:
        draw_at(view)

    margin = float(template.get("margin", 10))
    frame = Vec2(margin, margin)
    # An explicit notes_at already occupies its own spot, so it does not take a side column.
    needs_column = bool(spec.get("legend") and not spec.get("legend_at")) or (
        _has_notes(spec.get("notes")) and not spec.get("notes_at"))
    area = _clear_of_titleblock(template["drawing_area"], template)
    area = _reserve_side(area, template, needs_column)
    frame_size = template["frame"]

    def item_for(key, title, numeric, nts, bbox, k, fixed):
        pw, ph, _mw, _mh = _window(bbox, k)
        title_text = _title_text(title, language)
        scale_label = _scale_label(numeric, nts)
        caption_w = max(text_width(title_text, TITLE_H) if title_text else 0.0,
                        text_width(scale_label, SCALE_H))
        return {
            "key": key, "title": title_text, "scale_label": scale_label,
            "numeric": numeric, "nts": nts, "bbox": bbox, "k": k, "fixed": fixed,
            "caption_w": caption_w,
            "vp_w": pw, "vp_h": ph, "slot_w": max(pw, caption_w), "slot_h": ph + _caption_band(),
        }

    def primary_item():
        return item_for("primary", _primary_title(spec, language), ctx.scale, False,
                        (px0, py0, px1, py1), ctx.k, spec.get("origin_on_sheet"))

    def fixed_items():
        items = []
        for view in views:
            item = item_for(view["id"], view["title"], view["numeric"], view["nts"], view["bbox"],
                            view["numeric"] * ctx.u, view["fixed"])
            item["auto"] = bool(view.get("auto"))
            items.append(item)
        return items

    def auto_item(view, scale):
        bounds = _estimate_bounds(view, scale, ctx.u) or (0.0, 0.0, ctx.u, ctx.u)
        item = item_for(str(view.get("id")), view.get("title"), scale, False, bounds,
                        scale * ctx.u, view.get("origin_on_sheet"))
        item["auto"] = True
        return item

    chosen, area = _assign_auto_scales(
        auto_specs, primary_item, fixed_items, auto_item, area, frame_size, ctx.u, language)
    for view in auto_specs:
        content = _auto_view_content(view, ctx.u)
        draw_at({**content, "scale": chosen.get(str(view.get("id")), _view_floor(view))}, auto=True)

    ctx.doc.header["$PSLTSCALE"] = 1
    ctx.doc.header["$LTSCALE"] = LTSCALE_FACTOR          # paper mm; viewport scale equalises dashes
    paper = template["paper"]
    psp = ctx.doc.layouts.new(LAYOUT_NAME)
    psp.page_setup(size=(paper["w"], paper["h"]), margins=(0, 0, 0, 0), units="mm", scale=(1, 1))
    try:
        ctx.doc.layouts.set_active_layout(psp.name)
    except (KeyError, ValueError):
        pass

    items = [primary_item(), *fixed_items()]
    placed = _pack(area, items, frame_size["w"], frame_size["h"])
    pack_area = _clip_area(area, frame_size["w"], frame_size["h"])
    occupied = []
    for item in items:
        if item.get("skip"):
            continue
        pos = placed.get(item["key"])
        if pos is None:
            continue
        x, y = pos
        occupied.append((x, y, x + item["slot_w"], y + item["slot_h"]))

    def add_viewport(item, vp_x, y, pw, ph, index, world_h, label) -> bool:
        if _hits_titleblock(vp_x, y, pw, ph, template):
            return False
        x0, y0, x1, y1 = item["bbox"]
        center = frame + Vec2(vp_x + pw / 2, y + ph / 2)
        psp.add_viewport(
            center=(center.x, center.y),
            size=(pw, ph),
            view_center_point=((x0 + x1) / 2, (y0 + y1) / 2),
            view_height=world_h if world_h > 1e-9 else ph * item["k"],
            status=2 + index,
            dxfattribs={"layer": "VIEWPORT"},
        )
        _write_caption(psp, frame + Vec2(vp_x, y), pw, ph, item["title"], label,
                       max_top=frame.y + float(frame_size["h"]))
        return True

    rects = templates.draw_titleblock(psp, template, values, 1, "mm", origin=frame, language=language)
    column_taken = False
    for index, item in enumerate(items):
        if item.get("skip"):
            ctx.warnings.append(_view_fit_warning(item, area))
            fitted = _fit_into(pack_area, item, occupied, template)
            if fitted is None:
                ctx.warnings.append(
                    f"ERROR: view {item['key']!r} could not be placed in any free rectangle. "
                    f"Use a larger sheet")
                continue
            vp_x, y, pw, ph, slot_w, slot_h = fitted
            legend = pack_area.get("legend_box")
            if isinstance(legend, dict) and vp_x >= float(legend["x"]) - 1e-6:
                column_taken = True
            x0, y0, x1, y1 = item["bbox"]
            model_w = max(x1 - x0, 0.0)
            model_h = max(y1 - y0, 0.0)
            world_h = max(model_h, model_w * ph / pw) if pw > 1e-9 else ph * item["k"]
            label = item["scale_label"]
            if not item.get("nts") and ph > 1e-9 and item["numeric"] and item["k"]:
                unit = item["k"] / float(item["numeric"])
                if unit > 1e-12 and world_h > 1e-9:
                    shown = world_h / ph / unit
                    label = _scale_label(shown, False)
                    floor = _role_floor(f"{item.get('key') or ''} {item.get('title') or ''}")
                    readable = item.get("auto") or (item.get("key") == "primary" and spec.get("_s2c_auto_scale"))
                    if readable and shown > floor + 0.5:
                        ctx.warnings.append(
                            f"ERROR: view {item['key']!r} is at 1:{shown:.0f}, coarser than the readable "
                            f"1:{floor:g}. Use a larger sheet")
            left, bottom = frame.x + vp_x, frame.y + y
            if (left < -1e-4 or bottom < -1e-4 or left + pw > float(paper["w"]) + 1e-4
                    or bottom + ph > float(paper["h"]) + 1e-4):
                continue
            if add_viewport(item, vp_x, y, pw, ph, index, world_h, label):
                occupied.append((vp_x - max(0.0, slot_w - pw) / 2, y,
                                 vp_x - max(0.0, slot_w - pw) / 2 + slot_w, y + slot_h))
            continue
        pos = placed.get(item["key"])
        if pos is None:
            continue
        x, y = pos
        pw, ph = item["vp_w"], item["vp_h"]
        vp_x = x + max(0.0, item["slot_w"] - pw) / 2
        if _hits_titleblock(vp_x, y, pw, ph, template):
            ctx.warnings.append(
                f"ERROR: view {item['key']!r} would overlap the title block - "
                f"not placed off the sheet. Use a larger sheet")
            continue
        x0, y0, x1, y1 = item["bbox"]
        k = item["k"]
        pad = VIEW_PAD_MM * k
        world_h = max(y1 - y0, 0.0) + 2 * pad
        add_viewport(item, vp_x, y, pw, ph, index, world_h, item["scale_label"])

    notes_area = dict(area)
    if column_taken:
        notes_area.pop("legend_box", None)
    _place_sheet_notes(ctx, spec, template, psp, frame, notes_area)
    return rects, psp.name


def _view_fit_warning(item: dict, area: dict) -> str:
    free_w, free_h = item.get("free") or (float(area["w"]), float(area["h"]))
    where = "NTS" if item.get("nts") else f"1:{float(item['numeric']):g}"
    base = (f"ERROR: view {item['key']!r} ({item['slot_w']:.0f} x {item['slot_h']:.0f} paper mm) "
            f"does not fit the free {free_w:.0f} x {free_h:.0f} mm at {where} - "
            f"not placed off the sheet.")
    better = _scale_advice(item, free_w, free_h)
    if better is None:
        return f"{base} Use a larger sheet"
    return f"{base} Consider 1:{better:g} or a larger sheet"


def _has_notes(notes) -> bool:
    notes = notes or {}
    return bool(notes.get("en") or notes.get("he"))


def _place_sheet_notes(ctx, spec, template, psp, frame: Vec2, area: dict) -> None:
    """Legend and notes are paper-space text. `legend_at` / `notes_at` are frame millimetres."""
    paper = ctx.for_paper(psp)
    column = template.get("legend_area") or area.get("legend_box")
    max_w = column["w"] if column else None
    pending = []
    table = legend.legend_table(paper, spec.get("legend") or [], max_w)
    notes = legend.notes_block(paper, spec.get("notes"), max_w)
    if table:
        pending.append((table, spec.get("legend_at")))
    if notes:
        pending.append((notes, spec.get("notes_at")))
    if not pending:
        return
    if column is None and any(at is None for _, at in pending):
        ctx.warnings.append("legend / notes have no paper position on this multi-view sheet")
    y_top = (column["y"] + column["h"]) if column else 0.0
    for block, at in pending:
        if at:
            block.draw(frame + Vec2(at))
            continue
        if column is None:
            continue
        block.draw(frame + Vec2(column["x"], y_top))
        y_top -= block.height + 4.0
