"""Deterministic label placement.

Text boxes are measured in model space from the real paper text height (already
multiplied by the view scale) and the font width estimate, including a bilingual
pair stacked at the MTEXT line pitch. A label keeps `label_at` when that box is
clear of other text, dimension text and drawn linework. `label_pinned` keeps the
authored point even when it collides. The search order is fixed, so the same spec
always lands on the same sheet.
"""
from __future__ import annotations

import math

from ezdxf import bbox as ezbbox
from ezdxf.math import Vec2

from .components import draw_label, leader_label, stack_text
from .context import DrawContext
from .text import LINE_SPACING, pick, text_width

GAP_TEXT_MM = 0.8
GAP_LINE_MM = 0.35
DESCENDER = 0.25                              # fraction of cap height below the baseline
LEADER_FROM_MM = 6.0                          # a nudge this far grows or adds a leader
STEPS_MM = (3.0, 6.0, 10.0, 15.0, 22.0, 32.0, 44.0, 60.0, 80.0, 105.0)
SKIP_LAYERS = {"HATCH", "FRAME", "DIM", "CENTER", "DEFPOINTS", "VIEWPORT", "VIEWPORTS", "NOPLOT"}
_RAW_DIRS = ((0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1),
             (1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1))
DIRS = tuple(Vec2(x, y).normalize() for x, y in _RAW_DIRS)


def _overlaps(a, b, gap: float) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _area_overlap(a, b, eps: float = 1e-4) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > eps and min(a[3], b[3]) - max(a[1], b[1]) > eps


def _seg_hits_rect(a: Vec2, b: Vec2, x0, y0, x1, y1) -> bool:
    dx, dy = b.x - a.x, b.y - a.y
    u0, u1 = 0.0, 1.0
    for pi, qi in ((-dx, a.x - x0), (dx, x1 - a.x), (-dy, a.y - y0), (dy, y1 - a.y)):
        if abs(pi) < 1e-12:
            if qi < 0:
                return False
            continue
        t = qi / pi
        if pi < 0:
            if t > u1:
                return False
            u0 = max(u0, t)
        else:
            if t < u0:
                return False
            u1 = min(u1, t)
    return u0 <= u1


def _circle_hits_rect(center: Vec2, radius: float, x0, y0, x1, y1) -> bool:
    nx = min(max(center.x, x0), x1)
    ny = min(max(center.y, y0), y1)
    dx, dy = center.x - nx, center.y - ny
    return dx * dx + dy * dy <= radius * radius


def _entity_box(entity):
    try:
        ext = ezbbox.extents([entity], fast=True)
    except (AttributeError, TypeError, ValueError):
        return None
    if not ext.has_data:
        return None
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def _iter_texts(layout):
    for entity in layout:
        kind = entity.dxftype()
        if kind in ("TEXT", "MTEXT"):
            yield entity
        elif kind == "DIMENSION":
            yield from _virtual_texts(entity)
        elif kind == "INSERT":
            yield from _virtual_texts(entity)


def _virtual_texts(entity, depth: int = 0):
    if depth > 3:
        return
    try:
        virtual = entity.virtual_entities()
    except (AttributeError, TypeError, ValueError):
        return
    for item in virtual:
        kind = item.dxftype()
        if kind in ("TEXT", "MTEXT"):
            yield item
        elif kind in ("INSERT", "DIMENSION"):
            yield from _virtual_texts(item, depth + 1)


def text_boxes(layout) -> list[tuple[float, float, float, float]]:
    """Axis-aligned boxes of every text entity in a layout, including dimension text."""
    boxes = []
    for entity in _iter_texts(layout):
        box = _entity_box(entity)
        if box and box[2] > box[0] and box[3] > box[1]:
            boxes.append(box)
    return boxes


def overlapping_text(layout) -> list[tuple[int, int]]:
    """Index pairs of text boxes whose areas intersect."""
    boxes = text_boxes(layout)
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _area_overlap(boxes[i], boxes[j]):
                hits.append((i, j))
    return hits


def _arc_segments(entity) -> list[tuple[Vec2, Vec2]]:
    center = Vec2(entity.dxf.center)
    radius = float(entity.dxf.radius)
    a0 = math.radians(float(entity.dxf.start_angle))
    a1 = math.radians(float(entity.dxf.end_angle))
    if a1 <= a0:
        a1 += 2 * math.pi
    span = a1 - a0
    steps = max(2, int(span / (math.pi / 8)) + 1)
    points = [center + Vec2(math.cos(a0 + span * i / steps), math.sin(a0 + span * i / steps)) * radius
              for i in range(steps + 1)]
    return list(zip(points, points[1:]))


def _solid_segments(entity) -> list[tuple[Vec2, Vec2]]:
    points = []
    for index in range(4):
        vertex = getattr(entity.dxf, f"vtx{index}", None)
        if vertex is not None:
            points.append(Vec2(vertex))
    if len(points) < 2:
        return []
    ring = points + [points[0]]
    return [(a, b) for a, b in zip(ring, ring[1:]) if a.distance(b) > 1e-9]


def _segments_of(entity, depth: int = 0):
    kind = entity.dxftype()
    layer = _layer(entity)
    if kind == "INSERT" and depth < 3:
        try:
            for virtual in entity.virtual_entities():
                yield from _segments_of(virtual, depth + 1)
        except (AttributeError, TypeError, ValueError):
            return
        return
    if kind == "DIMENSION" or layer in SKIP_LAYERS:
        return
    if kind == "LINE":
        yield ("seg", Vec2(entity.dxf.start), Vec2(entity.dxf.end))
    elif kind == "LWPOLYLINE":
        points = [Vec2(p[0], p[1]) for p in entity.get_points("xy")]
        if getattr(entity, "closed", False) and points:
            points.append(points[0])
        for start, end in zip(points, points[1:]):
            if start.distance(end) > 1e-9:
                yield ("seg", start, end)
    elif kind == "CIRCLE":
        yield ("circle", Vec2(entity.dxf.center), float(entity.dxf.radius))
    elif kind == "ARC":
        for start, end in _arc_segments(entity):
            yield ("seg", start, end)
    elif kind == "SOLID":
        for start, end in _solid_segments(entity):
            yield ("seg", start, end)


def _layer(entity) -> str:
    if not entity.dxf.hasattr("layer"):
        return ""
    return str(entity.dxf.layer).upper()


def _collect(layout):
    texts = [box for box in (_entity_box(entity) for entity in _iter_texts(layout)) if box]
    segments = []
    circles = []
    for entity in layout:
        for item in _segments_of(entity):
            if item[0] == "circle":
                circles.append((item[1], item[2]))
            else:
                start, end = item[1], item[2]
                segments.append((start, end, (min(start.x, end.x), min(start.y, end.y),
                                              max(start.x, end.x), max(start.y, end.y))))
    return texts, segments, circles


def _preferred(ctx: DrawContext, el: dict, anchor: Vec2, radius: float, lines: list[str], height: float):
    """The position `draw_label` would use if nothing else were on the sheet."""
    explicit = bool(el.get("label_at") and (el.get("leader") or el.get("leader_to")))
    target = Vec2(el["leader_to"]) if el.get("leader_to") else anchor
    if el.get("label_at"):
        at = Vec2(el["label_at"])
        align = "left" if explicit else (el.get("label_align") or "left")
    else:
        at = anchor + Vec2(0, radius + ctx.paper(1.5) + (len(lines) - 1) * LINE_SPACING * height)
        align = "center"
    return at, align, explicit, target


def _as_left(at: Vec2, align: str, width: float, offset: Vec2) -> Vec2:
    base = at + offset
    if align == "center":
        return base - Vec2(width / 2, 0)
    if str(align).lower() == "right":
        return base - Vec2(width, 0)
    return base


def _candidates(ctx: DrawContext, at: Vec2, align: str, lines: list[str], height: float, explicit: bool):
    width = max(text_width(line, height) for line in lines)
    yield at, align, explicit
    for step in STEPS_MM:
        leader = explicit or step + 1e-9 >= LEADER_FROM_MM
        offset_len = ctx.paper(step)
        for direction in DIRS:
            offset = direction * offset_len
            if leader:
                yield _as_left(at, align, width, offset), "left", True
            else:
                yield at + offset, align, False


def _box(at: Vec2, lines: list[str], height: float, align: str, leader: bool, target: Vec2, k: float):
    width = max(text_width(line, height) for line in lines)
    count = len(lines)
    top = at.y + height
    bottom = at.y - (count - 1) * LINE_SPACING * height - DESCENDER * height
    key = (align or "left").lower()
    if key == "center":
        x0, x1 = at.x - width / 2, at.x + width / 2
    elif key == "right":
        x0, x1 = at.x - width, at.x
    else:
        x0, x1 = at.x, at.x + width
    if leader:
        if target.y > at.y + height:
            top = max(top, at.y + height + 1.1 * k)
        else:
            bottom = min(bottom, at.y - (count - 1) * LINE_SPACING * height - 1.1 * k)
    return (x0, bottom, x1, top)


def _hits_geometry(box, segments, circles, gap: float) -> int:
    x0, y0, x1, y1 = box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap
    count = 0
    for start, end, bounds in segments:
        if bounds[2] < x0 or bounds[0] > x1 or bounds[3] < y0 or bounds[1] > y1:
            continue
        if _seg_hits_rect(start, end, x0, y0, x1, y1):
            count += 1
    for center, radius in circles:
        if _circle_hits_rect(center, radius + gap, x0, y0, x1, y1):
            count += 1
    return count


def _hits_text(box, texts, gap: float) -> int:
    return sum(1 for other in texts if _overlaps(box, other, gap))


def _anchor_box(anchor: Vec2, radius: float):
    return (anchor.x - radius, anchor.y - radius, anchor.x + radius, anchor.y + radius)


def _clear(box, texts, segments, circles, gap_text, gap_line, anchor_box) -> bool:
    if _hits_text(box, texts, gap_text):
        return False
    if anchor_box and _overlaps(box, anchor_box, 0):
        return False
    return _hits_geometry(box, segments, circles, gap_line) == 0


def _remember_leader(segments, at: Vec2, lines: list[str], height: float, target: Vec2, k: float) -> None:
    width = max(text_width(line, height) for line in lines)
    if target.y > at.y + height:
        y = at.y + height + 0.8 * k
    else:
        y = at.y - (len(lines) - 1) * LINE_SPACING * height - 0.8 * k
    shelf_start, shelf_end = Vec2(at.x, y), Vec2(at.x + width, y)
    elbow = shelf_end if target.x > at.x + width / 2 else shelf_start
    for start, end in ((shelf_start, shelf_end), (elbow, target)):
        segments.append((start, end, (min(start.x, end.x), min(start.y, end.y),
                                       max(start.x, end.x), max(start.y, end.y))))


def _draw(ctx: DrawContext, lines, at: Vec2, align: str, leader: bool, target: Vec2, height: float) -> None:
    if leader:
        leader_label(ctx, lines, at, target, height)
    else:
        stack_text(ctx, lines, at, height, align)


def _place_one(ctx, el, anchor, radius, texts, segments, circles, gap_text, gap_line, pinned: bool) -> None:
    lines = pick(el.get("label"), ctx.language)
    if not lines:
        return
    height = ctx.text_h("text_height")
    at, align, explicit, target = _preferred(ctx, el, anchor, radius, lines, height)
    anchor_box = _anchor_box(anchor, radius)
    if pinned:
        draw_label(ctx, el, anchor, radius)
        box = _box(at, lines, height, align, explicit, target, ctx.k)
        texts.append(box)
        if explicit:
            _remember_leader(segments, at, lines, height, target, ctx.k)
        return
    chosen = None
    for index, (candidate, cand_align, cand_leader) in enumerate(
            _candidates(ctx, at, align, lines, height, explicit)):
        box = _box(candidate, lines, height, cand_align, cand_leader, target, ctx.k)
        if _clear(box, texts, segments, circles, gap_text, gap_line, anchor_box):
            chosen = (candidate, cand_align, cand_leader, box)
            break
        if index > 400:
            break
    if chosen is None:
        best = None
        for index, (candidate, cand_align, cand_leader) in enumerate(
                _candidates(ctx, at, align, lines, height, explicit)):
            box = _box(candidate, lines, height, cand_align, cand_leader, target, ctx.k)
            score = (_hits_text(box, texts, gap_text), _hits_geometry(box, segments, circles, gap_line), index)
            if best is None or score < best[0]:
                best = (score, (candidate, cand_align, cand_leader, box))
        chosen = best[1]
        ctx.warnings.append(f"label {el.get('id', '?')!r} has no clear position")
    candidate, cand_align, cand_leader, box = chosen
    _draw(ctx, lines, candidate, cand_align, cand_leader, target, height)
    texts.append(box)
    if cand_leader:
        _remember_leader(segments, candidate, lines, height, target, ctx.k)


def place_labels(ctx: DrawContext) -> None:
    """Draw queued element labels, nudging only the ones that collide."""
    items = list(ctx.pending)
    ctx.pending.clear()
    if not items:
        return
    texts, segments, circles = _collect(ctx.msp)
    gap_text, gap_line = ctx.paper(GAP_TEXT_MM), ctx.paper(GAP_LINE_MM)
    pinned = [item for item in items if item[0].get("label_pinned")]
    free = [item for item in items if not item[0].get("label_pinned")]
    for el, anchor, radius in pinned + free:
        _place_one(ctx, el, anchor, radius, texts, segments, circles, gap_text, gap_line,
                   pinned=bool(el.get("label_pinned")))
