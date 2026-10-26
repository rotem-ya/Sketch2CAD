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
from .layout import _suggest_scale, content_extents
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
    # Free text stays put and is part of the view. The viewport is measured before labels move.
    place.draw_fixed_text(child)
    ext = ezbbox.extents(block, fast=True)
    if not ext.has_data:
        ctx.doc.blocks.delete_block(name, safe=True)
        ctx.warnings.append(f"view {view.get('id')!r} has nothing to draw")
        return None
    x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    _finish_labels(child)
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
    """Paper mm from the viewport top up to the top of the title cap."""
    return CAPTION_GAP + SCALE_H + RULE_GAP + TITLE_GAP + TITLE_H


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


def _best_fill(area, w, h, occupied, gap: float) -> float:
    """Smallest content/free-space ratio. Above 1 means the view does not fit what is left."""
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
                        best = fill if best is None else min(best, fill)
    if best is None:
        return max(w / max(area["w"], 1e-6), h / max(area["h"], 1e-6))
    return best


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
            item["suggest_fill"] = _best_fill(area, item["slot_w"], item["slot_h"], occupied, VIEW_GAP_MM)
    return placed


def _write_caption(msp, origin: Vec2, width: float, height: float, title: str, scale_label: str) -> None:
    """Title, an underline, and ``SCALE 1:50`` centred above the viewport."""
    top = origin.y + height
    scale_y = top + CAPTION_GAP
    rule_y = scale_y + SCALE_H + RULE_GAP
    title_y = rule_y + TITLE_GAP
    center = origin.x + width / 2
    put_text(msp, scale_label, (center, scale_y), SCALE_H, layer="TEXT", align="CENTER", nowrap=True)
    if not title:
        return
    put_text(msp, title, (center, title_y), TITLE_H, layer="TEXT", align="CENTER", nowrap=True)
    half = text_width(title, TITLE_H) / 2
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


def compose(ctx: DrawContext, spec: dict, template: dict, values: dict, language: str,
            primary_bbox=None) -> tuple[dict, str]:
    """Draw extra views, then a 1:1 paper-space sheet. Returns (title-block rects, layout name).

    `primary_bbox` is the primary view measured before its labels were moved.
    """
    px0, py0, px1, py1 = content_extents(ctx) if primary_bbox is None else primary_bbox
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
    # An explicit notes_at already occupies its own spot, so it does not take a side column.
    needs_column = bool(spec.get("legend") and not spec.get("legend_at")) or (
        _has_notes(spec.get("notes")) and not spec.get("notes_at"))
    area = _reserve_side(template["drawing_area"], template, needs_column)

    def item_for(key, title, numeric, nts, bbox, k, fixed):
        pw, ph, _mw, _mh = _window(bbox, k)
        title_text = _title_text(title, language)
        scale_label = _scale_label(numeric, nts)
        caption_w = max(text_width(title_text, TITLE_H) if title_text else 0.0,
                        text_width(scale_label, SCALE_H))
        return {
            "key": key, "title": title_text, "scale_label": scale_label,
            "numeric": numeric, "bbox": bbox, "k": k, "fixed": fixed,
            "vp_w": pw, "vp_h": ph, "slot_w": max(pw, caption_w), "slot_h": ph + _caption_band(),
        }

    items = [item_for("primary", _primary_title(spec, language), ctx.scale, False, (px0, py0, px1, py1),
                      ctx.k, spec.get("origin_on_sheet"))]
    items += [item_for(view["id"], view["title"], view["numeric"], view["nts"], view["bbox"],
                       view["numeric"] * ctx.u, view["fixed"]) for view in views]
    frame_size = template["frame"]
    placed = _pack(area, items, frame_size["w"], frame_size["h"])

    rects = templates.draw_titleblock(psp, template, values, 1, "mm", origin=frame, language=language)
    for index, item in enumerate(items):
        if item.get("skip"):
            ctx.warnings.append(_view_fit_warning(item, area))
            continue
        pos = placed.get(item["key"])
        if pos is None:
            continue
        x, y = pos
        pw, ph = item["vp_w"], item["vp_h"]
        vp_x = x + max(0.0, item["slot_w"] - pw) / 2
        x0, y0, x1, y1 = item["bbox"]
        k = item["k"]
        pad = VIEW_PAD_MM * k
        center = frame + Vec2(vp_x + pw / 2, y + ph / 2)
        world_h = max(y1 - y0, 0.0) + 2 * pad
        psp.add_viewport(
            center=(center.x, center.y),
            size=(pw, ph),
            view_center_point=((x0 + x1) / 2, (y0 + y1) / 2),
            view_height=world_h if world_h > 1e-9 else ph * k,
            status=2 + index,
            dxfattribs={"layer": "VIEWPORT"},
        )
        _write_caption(psp, frame + Vec2(vp_x, y), pw, ph, item["title"], item["scale_label"])

    _place_sheet_notes(ctx, spec, template, psp, frame, area)
    return rects, psp.name


def _view_fit_warning(item: dict, area: dict) -> str:
    area_fill = max(item["slot_w"] / max(area["w"], 1e-6), item["slot_h"] / max(area["h"], 1e-6))
    # Remaining free space, not the whole sheet: a view that is smaller than the sheet can still
    # be too big for the gap that is left, and the advice must be a smaller scale.
    fill = max(float(item.get("suggest_fill") or area_fill), area_fill, 1.0)
    better = _suggest_scale(float(item["numeric"]), fill)
    return (f"ERROR: view {item['key']!r} ({item['slot_w']:.0f} x {item['slot_h']:.0f} paper mm) "
            f"does not fit the {area['w']:.0f} x {area['h']:.0f} mm area at 1:{item['numeric']:g} - "
            f"not placed off the sheet. Consider 1:{better:g} or a larger sheet")


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
