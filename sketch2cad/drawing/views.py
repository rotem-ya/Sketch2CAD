"""Multi-view sheets.

One paperspace layout holds the title block, the legend, the notes and a VIEWPORT
per view. Model space keeps every view at true size, separated so the viewports
do not see each other. Text in a view is paper height times that view's scale
(ezdxf has no annotative text). Titles and the scale label are paper-space text
at ISO 3098 heights. A view scale of "NTS" is fitted to paper and labelled NTS.
"""
from __future__ import annotations

import re

from ezdxf import bbox as ezbbox
from ezdxf.math import Matrix44, Vec2

from .. import templates
from . import components, dims, legend, place
from .context import LTSCALE_FACTOR, DrawContext
from .layout import content_extents
from .text import LINE_SPACING, pick, put_text

ELEMENT_ERRORS = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError)
LAYOUT_NAME = "SHEET"
VIEW_PAD_MM = 2.0
TITLE_H = 3.5
SCALE_H = 2.5
SLOT_GAP_MM = 4.0
NTS_FIT_MM = 80.0
PACK_STEP_MM = 5.0


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
        place.place_labels(ctx)


def _rough_bounds(elements) -> tuple[float, float, float, float] | None:
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
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _resolve_scale(view: dict, unit: float, sheet_scale: float) -> tuple[float, bool]:
    """Return (numeric scale denominator, not_to_scale)."""
    scale = view.get("scale")
    if isinstance(scale, str) and scale.strip().upper() == "NTS":
        bounds = _rough_bounds(view.get("elements"))
        fit = float(view.get("fit_mm") or NTS_FIT_MM)
        if not bounds or fit <= 0:
            return float(sheet_scale), True
        longer = max(bounds[2] - bounds[0], bounds[3] - bounds[1], unit)
        return longer / (fit * unit), True
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
    numeric, nts = _resolve_scale(view, ctx.u, ctx.scale)
    name = _block_name(ctx.doc, view.get("id", "view"))
    block = ctx.doc.blocks.new(name)
    child = ctx.fork(block, numeric)
    child.nudge_labels = ctx.nudge_labels
    _draw_elements(child, view.get("elements"))
    try:
        dims.draw_dimensions(child, view.get("dimensions") or [])
    except ELEMENT_ERRORS as exc:
        ctx.warnings.append(f"view {view.get('id')!r}: dimensions skipped ({exc!r})")
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


def _title_lines(title, language: str) -> list[str]:
    if isinstance(title, str):
        return [title] if title.strip() else []
    return pick(title, language)


def _scale_label(numeric: float, nts: bool) -> str:
    if nts:
        return "NTS"
    return f"1:{templates.format_scale(numeric)}"


def _band(line_count: int) -> float:
    count = max(line_count, 1)
    return 1.2 + (count - 1) * LINE_SPACING * TITLE_H + TITLE_H + 1.0


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
    x_min, y_min = area["x"], area["y"]
    x_max = area["x"] + area["w"] - w
    y_max = area["y"] + area["h"] - h
    if w > area["w"] + 1e-6 or h > area["h"] + 1e-6:
        return None
    y = y_max
    while y >= y_min - 1e-6:
        x = x_min
        while x <= x_max + 1e-6:
            rect = (x, y, x + w, y + h)
            if not any(_rects_overlap(rect, other, gap) for other in occupied):
                return x, y
            x += PACK_STEP_MM
        y -= PACK_STEP_MM
    return None


def _pack(area, items: list[dict]) -> dict[str, tuple[float, float]]:
    """Bottom-left of each viewport, in frame millimetres. Primary is centred when it fits."""
    occupied = []
    placed = {}

    def take(item, pos, overflow=False):
        x, y = pos
        placed[item["key"]] = (x, y)
        occupied.append((x, y, x + item["slot_w"], y + item["slot_h"]))
        item["overflow"] = overflow or not _inside(area, x, y, item["slot_w"], item["slot_h"])

    fixed = [item for item in items if item.get("fixed")]
    floating = [item for item in items if not item.get("fixed")]
    for item in fixed:
        take(item, (float(item["fixed"][0]), float(item["fixed"][1])))
    primary = next((item for item in floating if item["key"] == "primary"), None)
    rest = [item for item in floating if item["key"] != "primary"]
    if primary:
        cx = area["x"] + max(0.0, (area["w"] - primary["slot_w"]) / 2)
        cy = area["y"] + max(0.0, (area["h"] - primary["slot_h"]) / 2)
        rect = (cx, cy, cx + primary["slot_w"], cy + primary["slot_h"])
        if _inside(area, cx, cy, primary["slot_w"], primary["slot_h"]) and not any(
                _rects_overlap(rect, other, SLOT_GAP_MM) for other in occupied):
            take(primary, (cx, cy))
        else:
            found = _scan(area, primary["slot_w"], primary["slot_h"], occupied, SLOT_GAP_MM)
            take(primary, found or (cx, cy), overflow=found is None)
    for item in rest:
        found = _scan(area, item["slot_w"], item["slot_h"], occupied, SLOT_GAP_MM)
        if found:
            take(item, found)
        else:
            edge = max((rect[2] for rect in occupied), default=area["x"]) + SLOT_GAP_MM
            take(item, (edge, area["y"]), overflow=True)
    return placed


def _write_caption(msp, origin: Vec2, width: float, height: float, lines: list[str], scale_label: str) -> None:
    top = origin.y + height
    put_text(msp, scale_label, (origin.x + width, top + 1.2), SCALE_H, layer="TEXT", align="RIGHT")
    if not lines:
        return
    baseline = top + 1.2 + (len(lines) - 1) * LINE_SPACING * TITLE_H
    for index, line in enumerate(lines):
        put_text(msp, line, (origin.x, baseline - index * LINE_SPACING * TITLE_H), TITLE_H, layer="TEXT", align="LEFT")


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


def compose(ctx: DrawContext, spec: dict, template: dict, values: dict, language: str) -> tuple[dict, str]:
    """Draw extra views, then a 1:1 paper-space sheet. Returns (title-block rects, layout name)."""
    px0, py0, px1, py1 = content_extents(ctx)
    if px1 - px0 < 1e-9 and py1 - py0 < 1e-9:
        px1, py1 = px0 + ctx.k, py0 + ctx.k
    span = max(px1 - px0, py1 - py0, ctx.k)
    gap = span * 0.3 + ctx.paper(30)
    cursor = px1 + gap
    views = []
    for view in spec.get("views") or []:
        record = _draw_model_view(ctx, view, cursor, py0)
        if record:
            views.append(record)
            cursor = record["bbox"][2] + gap

    ctx.doc.header["$PSLTSCALE"] = 1
    ctx.doc.header["$LTSCALE"] = LTSCALE_FACTOR          # paper mm; viewport scale equalises dashes
    paper = template["paper"]
    psp = ctx.doc.layouts.new(LAYOUT_NAME)
    psp.page_setup(size=(paper["w"], paper["h"]), margins=(0, 0, 0, 0), units="mm", scale=(1, 1))
    try:
        ctx.doc.layouts.set_active_layout(psp.name)
    except (KeyError, ValueError):
        pass

    margin = float(template.get("margin", 10))
    frame = Vec2(margin, margin)
    area = _reserve_side(template["drawing_area"], template, bool(spec.get("legend") or _has_notes(spec.get("notes"))))

    def item_for(key, title, numeric, nts, bbox, k, fixed):
        pw, ph, _mw, _mh = _window(bbox, k)
        lines = _title_lines(title, language)
        return {
            "key": key, "lines": lines, "scale_label": _scale_label(numeric, nts),
            "numeric": numeric, "bbox": bbox, "k": k, "fixed": fixed,
            "vp_w": pw, "vp_h": ph, "slot_w": pw, "slot_h": ph + _band(len(lines)),
        }

    items = [item_for("primary", spec.get("view") or "", ctx.scale, False, (px0, py0, px1, py1), ctx.k, None)]
    items += [item_for(view["id"], view["title"], view["numeric"], view["nts"], view["bbox"],
                       view["numeric"] * ctx.u, view["fixed"]) for view in views]
    placed = _pack(area, items)

    rects = templates.draw_titleblock(psp, template, values, 1, "mm", origin=frame, language=language)
    for index, item in enumerate(items):
        pos = placed.get(item["key"])
        if pos is None:
            continue
        if item.get("overflow"):
            ctx.warnings.append(f"view {item['key']!r} does not fit the drawing area")
        x, y = pos
        pw, ph = item["vp_w"], item["vp_h"]
        x0, y0, x1, y1 = item["bbox"]
        k = item["k"]
        pad = VIEW_PAD_MM * k
        center = frame + Vec2(x + pw / 2, y + ph / 2)
        world_h = max(y1 - y0, 0.0) + 2 * pad
        psp.add_viewport(
            center=(center.x, center.y),
            size=(pw, ph),
            view_center_point=((x0 + x1) / 2, (y0 + y1) / 2),
            view_height=world_h if world_h > 1e-9 else ph * k,
            status=2 + index,
            dxfattribs={"layer": "VIEWPORT"},
        )
        _write_caption(psp, frame + Vec2(x, y), pw, ph, item["lines"], item["scale_label"])

    _place_sheet_notes(ctx, spec, template, psp, frame, area)
    return rects, psp.name


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
