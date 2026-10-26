"""Deterministic label placement.

Text boxes are measured in model space from the real paper text height (already
multiplied by the view scale) and the font width estimate, including a bilingual
pair stacked at the MTEXT line pitch. A label keeps `label_at` when that box is
clear of other text, dimension text, drawn linework and existing leaders. A leader
is rejected when it crosses another text box, another leader, or a dimension line.
The search stays within about 25 mm of that spot, and a leader we add is rejected
when it is longer than that. If nothing nearby is clear, the label is drawn at the
original point with the leader the spec asked for, and an error is reported. A label
is never omitted. Label boxes keep GAP_TEXT_MM of
clear paper between them. `label_pinned` keeps the authored point even when it
collides. Free `text` is not queued here. Dimension text may only slide along its
dimension line or flip to the other side of it. The search order is fixed, so the
same spec always lands on the same sheet.
"""
from __future__ import annotations

import math

from ezdxf import bbox as ezbbox
from ezdxf.math import Vec2

from .components import draw_label, leader_label, stack_text
from .context import DrawContext
from .text import LINE_SPACING, pick, text_width

GAP_TEXT_MM = 1.5
GAP_LINE_MM = 0.35
DESCENDER = 0.25                              # fraction of cap height below the baseline
LEADER_FROM_MM = 6.0                          # a nudge this far grows or adds a leader
MAX_LEADER_MM = 25.0                          # a leader we add stays about this long on paper
MAX_MOVE_MM = 25.0                            # and the label itself stays about this close
STEPS_MM = (3.0, 6.0, 10.0, 15.0, 22.0, 25.0)
SLIDE_MM = (0.0, 4.0, 8.0, 14.0, 22.0, 32.0, 46.0, 64.0, 86.0)
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
            yield from _dimension_texts(entity)
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


def _dimension_texts(entity):
    """Text actually stored on the dimension, including text that was slid along the line."""
    try:
        block = entity.get_geometry_block()
    except (AttributeError, KeyError, ValueError):
        yield from _virtual_texts(entity)
        return
    for item in block:
        if item.dxftype() in ("TEXT", "MTEXT"):
            yield item


def _dimension_lines(layout) -> list[tuple[Vec2, Vec2]]:
    """Dimension line and extension lines, in model space."""
    found = []
    for dim in layout.query("DIMENSION"):
        try:
            block = dim.get_geometry_block()
        except (AttributeError, KeyError, ValueError):
            continue
        for entity in block:
            if entity.dxftype() != "LINE":
                continue
            start, end = Vec2(entity.dxf.start), Vec2(entity.dxf.end)
            if start.distance(end) > 1e-9:
                found.append((start, end))
    return found


def _segments_cross(a: Vec2, b: Vec2, c: Vec2, d: Vec2, eps: float = 1e-8) -> bool:
    """True when the open segments cross or overlap. Sharing an endpoint does not count."""
    def orient(p, q, r) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    if d1 * d2 < -eps and d3 * d4 < -eps:
        return True
    if abs(d1) > eps or abs(d2) > eps or abs(d3) > eps or abs(d4) > eps:
        return False
    # Collinear: an interior overlap, not a shared endpoint.
    length = max(a.distance(b), 1e-12)

    def param(p) -> float:
        return (p - a).dot(b - a) / (length * length)

    lo, hi = sorted((param(c), param(d)))
    return min(hi, 1.0) - max(lo, 0.0) > 1e-6


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


def _shelf(at: Vec2, lines: list[str], height: float, target: Vec2, k: float):
    """Shelf line under the text, or over it when the target is above. Matches `leader_label`."""
    width = max(text_width(line, height) for line in lines)
    if target.y > at.y + height:
        y = at.y + height + 0.8 * k
    else:
        y = at.y - (len(lines) - 1) * LINE_SPACING * height - 0.8 * k
    return Vec2(at.x, y), Vec2(at.x + width, y)


def _pointer(at: Vec2, lines: list[str], height: float, target: Vec2, k: float, elbow: str | None):
    left, right = _shelf(at, lines, height, target, k)
    if elbow == "left":
        start = left
    elif elbow == "right":
        start = right
    else:
        start = right if target.x > (left.x + right.x) / 2 else left
    return start, Vec2(target)


def _pointer_hits(start: Vec2, end: Vec2, texts, gap: float) -> int:
    count = 0
    for box in texts:
        if _seg_hits_rect(start, end, box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap):
            count += 1
    return count


def _line_crosses(start: Vec2, end: Vec2, leaders, dim_lines) -> int:
    count = 0
    for other_a, other_b, *_rest in leaders:
        if _segments_cross(start, end, other_a, other_b):
            count += 1
    for other_a, other_b in dim_lines:
        if _segments_cross(start, end, other_a, other_b):
            count += 1
    return count


def _remember_leader(leaders, at: Vec2, lines: list[str], height: float, target: Vec2, k: float,
                     elbow: str | None) -> None:
    left, right = _shelf(at, lines, height, target, k)
    start, end = _pointer(at, lines, height, target, k, elbow)
    for a, b in ((left, right), (start, end)):
        leaders.append((a, b, (min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))))


def _elbow_name(at, lines, height, target, k, which: str) -> str:
    left, right = _shelf(at, lines, height, target, k)
    nearer = "right" if target.x > (left.x + right.x) / 2 else "left"
    if which == "far":
        return "left" if nearer == "right" else "right"
    return nearer


def _options(ctx, at, align, lines, height, explicit, target):
    """(at, align, leader, elbow). A leader tries the near shelf end, then the far one."""
    if explicit:
        yield at, align, True, _elbow_name(at, lines, height, target, ctx.k, "near")
        yield at, align, True, _elbow_name(at, lines, height, target, ctx.k, "far")
    else:
        yield at, align, False, None
    width = max(text_width(line, height) for line in lines)
    for step in STEPS_MM:
        leader = explicit or step + 1e-9 >= LEADER_FROM_MM
        offset_len = ctx.paper(step)
        for direction in DIRS:
            offset = direction * offset_len
            if leader:
                moved = _as_left(at, align, width, offset)
                for which in ("near", "far"):
                    yield moved, "left", True, _elbow_name(moved, lines, height, target, ctx.k, which)
            else:
                yield at + offset, align, False, None


def _draw(ctx: DrawContext, lines, at: Vec2, align: str, leader: bool, target: Vec2, height: float,
          elbow: str | None, rotation: float = 0.0) -> None:
    if leader:
        leader_label(ctx, lines, at, target, height, elbow=elbow)
    else:
        stack_text(ctx, lines, at, height, align, rotation)


def _in_limits(box, bounds, content) -> bool:
    """Inside the drawing area, or inside the geometry when the geometry itself already overflows it."""
    if _box_inside(box, bounds):
        return True
    if content and bounds and not _box_inside(content, bounds) and _box_inside(box, content):
        return True
    return False


def _box_inside(box, bounds) -> bool:
    if not bounds:
        return True
    return (box[0] >= bounds[0] - 1e-6 and box[1] >= bounds[1] - 1e-6
            and box[2] <= bounds[2] + 1e-6 and box[3] <= bounds[3] + 1e-6)


def _attach_span(ctx, item, start: int) -> None:
    """Label entities are drawn after the element's span was opened. Add them to that span."""
    span = item.get("span")
    if not span:
        return
    span.setdefault("entities", [])
    span["entities"].extend(list(ctx.msp)[start:])


def _label_model_height(ctx, el) -> float:
    """Paper height in model units. `label_height` is the spec's own size and is not snapped."""
    if el.get("label_height") is not None:
        return ctx.paper(float(el["label_height"]))
    return ctx.text_h("text_height")


def _element_paper_height(ctx, el) -> float:
    """Paper height in model units. An explicit `height` is kept; the style default is already snapped."""
    if el.get("height") is not None:
        return ctx.paper(float(el["height"]))
    return ctx.paper(float(ctx.style["text_height"]))


def _item_placement(ctx, item):
    """Lines, height, preferred point, align, leader flag and target for one queued item."""
    el, anchor, radius = item["el"], item["anchor"], item["radius"]
    if item.get("kind") == "text":
        lines = pick(el.get("text"), ctx.language)
        height = _element_paper_height(ctx, el)
        explicit = bool(el.get("leader_to"))
        at = Vec2(el.get("at") or anchor)
        align = el.get("align") or "left"
        target = Vec2(el["leader_to"]) if explicit else anchor
        return lines, height, at, align, explicit, target, float(el.get("rotation") or 0)
    lines = pick(el.get("label"), ctx.language)
    height = _label_model_height(ctx, el)
    at, align, explicit, target = _preferred(ctx, el, anchor, radius, lines, height)
    return lines, height, at, align, explicit, target, 0.0


def draw_fixed_text(ctx: DrawContext) -> None:
    """Draw queued free text at its authored point. It is not moved and gets no leader of its own."""
    items = [item for item in ctx.pending if item.get("kind") == "text"]
    if not items:
        return
    ctx.pending = [item for item in ctx.pending if item.get("kind") != "text"]
    for item in items:
        el = item["el"]
        at = Vec2(el.get("at") or item["anchor"])
        height = _element_paper_height(ctx, el)
        lines = pick(el.get("text"), ctx.language)
        if not lines:
            continue
        start = len(ctx.msp)
        if el.get("leader_to"):
            leader_label(ctx, lines, at, Vec2(el["leader_to"]), height)
        else:
            stack_text(ctx, lines, at, height, el.get("align", "left"), float(el.get("rotation") or 0))
        _attach_span(ctx, item, start)


def _place_one(ctx, item, texts, segments, circles, leaders, dim_lines, gap_text, gap_line, bounds,
               content) -> None:
    el = item["el"]
    lines, height, at, align, explicit, target, rotation = _item_placement(ctx, item)
    if not lines:
        return
    anchor_box = _anchor_box(item["anchor"], item["radius"])
    pinned = bool(el.get("label_pinned")) or item.get("kind") == "text"
    if pinned:
        start = len(ctx.msp)
        if item.get("kind") == "text":
            _draw(ctx, lines, at, align, explicit, target, height, None, rotation)
        else:
            draw_label(ctx, el, item["anchor"], item["radius"])
        _attach_span(ctx, item, start)
        box = _box(at, lines, height, align, explicit, target, ctx.k)
        texts.append(box)
        if explicit:
            _remember_leader(leaders, at, lines, height, target, ctx.k, None)
        return
    chosen = None
    home = _box(at, lines, height, align, explicit, target, ctx.k)
    home_c = Vec2((home[0] + home[2]) / 2, (home[1] + home[3]) / 2)
    move_cap = ctx.paper(MAX_MOVE_MM)
    leader_cap = ctx.paper(MAX_LEADER_MM)

    def consider(options) -> bool:
        nonlocal chosen
        for candidate, cand_align, cand_leader, elbow in options:
            box = _box(candidate, lines, height, cand_align, cand_leader, target, ctx.k)
            center = Vec2((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            if center.distance(home_c) > move_cap + 1e-6:
                continue
            pointer = _pointer(candidate, lines, height, target, ctx.k, elbow) if cand_leader else None
            # The spec's own leader, from the authored point to the near end, is kept at any length.
            # A leader we would add by moving, or the far end of the shelf, stays within the cap.
            near = _elbow_name(at, lines, height, target, ctx.k, "near") if explicit else None
            authored_near = candidate.distance(at) <= 1e-6 and elbow == near
            if pointer and not authored_near and pointer[0].distance(pointer[1]) > leader_cap + 1e-6:
                continue
            shelf = _shelf(candidate, lines, height, target, ctx.k) if cand_leader else None
            if not _in_limits(box, bounds, content):
                continue
            parts = []
            if pointer:
                parts.append(pointer)
            if shelf:
                parts.append(shelf)
            if any(not _in_limits((p.x, p.y, p.x, p.y), bounds, content) for a, b in parts for p in (a, b)):
                continue
            text_hits = _hits_text(box, texts, gap_text)
            leader_hits = crossings = 0
            for start, end in parts:
                leader_hits += _pointer_hits(start, end, texts, gap_text)
                crossings += _line_crosses(start, end, leaders, dim_lines)
            if text_hits or leader_hits or crossings:
                continue
            geom = _hits_geometry(box, segments, circles, gap_line)
            anchor_hit = 1 if anchor_box and _overlaps(box, anchor_box, 0) else 0
            leader_block = _hits_geometry(box, leaders, [], gap_text)
            if geom == anchor_hit == leader_block == 0:
                chosen = (candidate, cand_align, cand_leader, elbow, box)
                return True
        return False

    consider(_options(ctx, at, align, lines, height, explicit, target))
    if chosen is None:
        # Never worse than the input, and never omitted: the point and the leader main would draw.
        ctx.warnings.append(f"ERROR: label {el.get('id', '?')!r} has no clear position")
        use_leader = bool(explicit)
        elbow = _elbow_name(at, lines, height, target, ctx.k, "near") if use_leader else None
        home = _box(at, lines, height, align, use_leader, target, ctx.k)
        chosen = (at, align, use_leader, elbow, home)
    candidate, cand_align, cand_leader, elbow, box = chosen
    start = len(ctx.msp)
    _draw(ctx, lines, candidate, cand_align, cand_leader, target, height, elbow, rotation)
    _attach_span(ctx, item, start)
    texts.append(box)
    if cand_leader:
        _remember_leader(leaders, candidate, lines, height, target, ctx.k, elbow)


def place_labels(ctx: DrawContext, bounds=None, content=None) -> None:
    """Draw queued labels. `bounds` is the drawing area in model space. Free text is not moved."""
    items = [item for item in ctx.pending if item.get("kind") != "text"]
    ctx.pending = [item for item in ctx.pending if item.get("kind") == "text"]
    if not items:
        return
    texts, segments, circles = _collect(ctx.msp)
    leaders: list = []
    dim_lines = _dimension_lines(ctx.msp)
    gap_text, gap_line = ctx.paper(GAP_TEXT_MM), ctx.paper(GAP_LINE_MM)
    pinned = [item for item in items if item["el"].get("label_pinned")]
    free = [item for item in items if not item["el"].get("label_pinned")]
    for item in pinned + free:
        _place_one(ctx, item, texts, segments, circles, leaders, dim_lines, gap_text, gap_line, bounds, content)


def _same_box(a, b, tol: float = 1e-3) -> bool:
    return all(abs(a[i] - b[i]) <= tol for i in range(4))


def _dim_frame(dim):
    """(origin, direction, normal, along-span) of a linear dimension line."""
    angle = math.radians(float(dim.dxf.angle or 0))
    direction = Vec2(math.cos(angle), math.sin(angle))
    normal = Vec2(-direction.y, direction.x)
    origin = Vec2(dim.dxf.defpoint)
    ends = []
    for name in ("defpoint2", "defpoint3"):
        if dim.dxf.hasattr(name):
            ends.append((Vec2(getattr(dim.dxf, name)) - origin).dot(direction))
    if len(ends) == 2:
        span = (min(ends), max(ends))
    else:
        span = None
    return origin, direction, normal, span


def nudge_dimensions(ctx: DrawContext, bounds=None) -> None:
    """Slide dimension text along its dimension line, or flip it to the other side.

    The text keeps its distance to the line. It is never moved off the line.
    """
    dims = list(ctx.msp.query("DIMENSION"))
    if not dims:
        return
    texts, segments, circles = _collect(ctx.msp)
    gap_text, gap_line = ctx.paper(GAP_TEXT_MM), ctx.paper(GAP_LINE_MM)
    for dim in dims:
        try:
            block = dim.get_geometry_block()
        except (AttributeError, KeyError, ValueError):
            continue
        origin, direction, normal, span = _dim_frame(dim)
        for entity in block:
            if entity.dxftype() not in ("TEXT", "MTEXT"):
                continue
            box = _entity_box(entity)
            if not box:
                continue
            others = [other for other in texts if not _same_box(other, box)]
            center = Vec2((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            rel = center - origin
            along0 = rel.dot(direction)
            across0 = rel.dot(normal)
            window = span if span is not None else (along0 - ctx.paper(20), along0 + ctx.paper(20))

            def candidate_at(along, across):
                delta = direction * (along - along0) + normal * (across - across0)
                return delta, (box[0] + delta.x, box[1] + delta.y, box[2] + delta.x, box[3] + delta.y)

            def acceptable(candidate) -> bool:
                if not _box_inside(candidate, bounds):
                    return False
                return _hits_text(candidate, others, gap_text) == 0

            home_ok = acceptable(box) and _hits_geometry(box, segments, circles, gap_line) == 0
            if home_ok:
                continue
            moved = None
            fallback = None
            for flip in (1.0, -1.0):
                across = across0 * flip
                for step in SLIDE_MM:
                    signs = (0,) if step == 0 else (1, -1)
                    for sign in signs:
                        along = along0 + sign * ctx.paper(step)
                        if not (window[0] - 1e-6 <= along <= window[1] + 1e-6):
                            continue
                        delta, candidate = candidate_at(along, across)
                        if not acceptable(candidate):
                            continue
                        geom = _hits_geometry(candidate, segments, circles, gap_line)
                        if geom == 0:
                            moved = (delta, candidate)
                            break
                        if fallback is None or geom < fallback[0]:
                            fallback = (geom, (delta, candidate))
                    if moved is not None:
                        break
                if moved is not None:
                    break
            if moved is None and fallback is not None and _hits_text(box, others, gap_text):
                # The authored spot sits on other text. A slide that misses the text is better,
                # even when it still meets linework.
                moved = fallback[1]
            elif moved is None and _hits_text(box, others, gap_text):
                ctx.warnings.append(
                    "ERROR: dimension text has no position along its dimension line and was left in place")
                continue
            elif moved is None:
                continue
            delta, candidate = moved
            if abs(delta.x) + abs(delta.y) < 1e-9:
                continue
            entity.translate(delta.x, delta.y, 0)
            texts = [other for other in texts if not _area_overlap(other, box)]
            texts.append(candidate)


def linework_hits_text(layout) -> list[tuple[int, int]]:
    """Linework that passes through a text box. Dimension and frame geometry is ignored."""
    boxes = text_boxes(layout)
    segments = []
    for entity in layout:
        for item in _segments_of(entity):
            if item[0] != "seg":
                continue
            start, end = item[1], item[2]
            segments.append((start, end))
    hits = []
    for i, (start, end) in enumerate(segments):
        for j, box in enumerate(boxes):
            inset = min(box[2] - box[0], box[3] - box[1]) * 0.08
            if inset <= 1e-9:
                continue
            if _seg_hits_rect(start, end, box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset):
                hits.append((i, j))
    return hits


def leader_crosses_text(layout) -> list[tuple[int, int]]:
    """TEXT-layer line segments that pass through a text box (a shelf beside its own label does not)."""
    boxes = text_boxes(layout)
    hits = []
    lines = [entity for entity in layout.query("LINE") if _layer(entity) == "TEXT"]
    for i, line in enumerate(lines):
        start, end = Vec2(line.dxf.start), Vec2(line.dxf.end)
        for j, box in enumerate(boxes):
            inset = min(box[2] - box[0], box[3] - box[1]) * 0.04
            if inset <= 1e-9:
                continue
            if _seg_hits_rect(start, end, box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset):
                hits.append((i, j))
    return hits


def leader_crossings(layout) -> list[tuple]:
    """Leaders that cross another leader, or a dimension line. Endpoint touches do not count."""
    lines = [entity for entity in layout.query("LINE") if _layer(entity) == "TEXT"]
    dim_lines = _dimension_lines(layout)
    hits = []
    for i, left in enumerate(lines):
        a, b = Vec2(left.dxf.start), Vec2(left.dxf.end)
        for j in range(i + 1, len(lines)):
            right = lines[j]
            if _segments_cross(a, b, Vec2(right.dxf.start), Vec2(right.dxf.end)):
                hits.append(("leader", i, j))
        for k, (c, d) in enumerate(dim_lines):
            if _segments_cross(a, b, c, d):
                hits.append(("dimension", i, k))
    return hits


def dimension_text_gap_mm(layout, k: float) -> list[float]:
    """Paper millimetres from each dimension-text box to its dimension line."""
    if k <= 0:
        return []
    gaps = []
    for dim in layout.query("DIMENSION"):
        origin, _direction, normal, _span = _dim_frame(dim)
        for entity in _dimension_texts(dim):
            box = _entity_box(entity)
            if not box:
                continue
            corners = (Vec2(box[0], box[1]), Vec2(box[2], box[1]), Vec2(box[2], box[3]), Vec2(box[0], box[3]))
            signed = [(corner - origin).dot(normal) for corner in corners]
            if min(signed) <= 0 <= max(signed):
                gaps.append(0.0)
            else:
                gaps.append(min(abs(value) for value in signed) / k)
    return gaps
