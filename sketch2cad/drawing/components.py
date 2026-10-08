"""Element drawers (spec section 1.1): one function per element type, plus element labels.

Each drawer adds its entities to model space and returns an anchor point used for the label.
Sizes come from explicit element keys first, then the catalog item, then defaults (ctx.size).
Small symbol sizes (ticks, arrow heads, break marks) are in paper mm so they read the same at any scale.
"""
from __future__ import annotations

import math

from ezdxf.math import Vec2

from .context import DrawContext
from .text import LINE_SPACING, pick, put_text, text_width

IN_BLOCK = {"layer": "0", "linetype": "BYBLOCK"}
HATCHES = {"sand": ("AR-SAND", 0.04), "concrete": ("AR-CONC", 0.03)}   # pattern, scale per paper mm
DEFAULT_OD = 100.0                                                       # mm


# ------------------------------------------------------------------ geometry helpers
def offset_polyline(points, d: float) -> list[Vec2]:
    """Offset an open polyline by d (positive = left of the direction), mitred corners."""
    pts = [Vec2(p) for p in points]
    dirs = [(b - a).normalize() for a, b in zip(pts, pts[1:])]
    normals = [Vec2(-u.y, u.x) for u in dirs]
    out = [pts[0] + normals[0] * d]
    for i in range(1, len(pts) - 1):
        bis = normals[i - 1] + normals[i]
        if bis.magnitude < 1e-9:                  # reversal: no sensible miter
            out.append(pts[i] + normals[i] * d)
            continue
        bis = bis.normalize()
        out.append(pts[i] + bis * (d / bis.dot(normals[i])))
    out.append(pts[-1] + normals[-1] * d)
    return out


def _rect(x0, y0, x1, y1) -> list[tuple]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _ticks(ctx: DrawContext, points, spacing: float, length: float, attribs: dict, slant: float = 0.0):
    """Ticks on the right side of each segment (slant = along-run lean, as a fraction of length)."""
    pts = [Vec2(p) for p in points]
    for a, b in zip(pts, pts[1:]):
        seg = b - a
        if seg.magnitude < spacing:
            continue
        u = seg.normalize()
        right = Vec2(u.y, -u.x)
        for i in range(1, int(seg.magnitude / spacing) + 1):
            p = a + u * (i * spacing - spacing / 2)
            ctx.msp.add_line(p, p + right * length - u * (length * slant), dxfattribs=attribs)


def _hatch(ctx: DrawContext, boundary, kind: str | None) -> None:
    if not kind:
        return
    pattern, factor = HATCHES.get(kind, HATCHES["sand"])
    h = ctx.msp.add_hatch(dxfattribs={"layer": "HATCH"})
    h.set_pattern_fill(pattern, scale=factor * ctx.k)
    h.paths.add_polyline_path(boundary, is_closed=True)


def _od(ctx: DrawContext, el: dict) -> float:
    """Pipe outside diameter: element od -> catalog dims.OD -> catalog dn -> default."""
    item = ctx.item(el.get("item"))
    dn = item.get("dn") if isinstance(item.get("dn"), (int, float)) else DEFAULT_OD
    return ctx.size(el, "od", ("dims", "OD"), dn)


# ------------------------------------------------------------------ linear elements
def draw_pipe(ctx: DrawContext, el: dict):
    """Double-line pipe along a polyline; optional centerline."""
    pts = el["points"]
    r = _od(ctx, el) / 2
    attribs = ctx.attribs(el)
    for side in (r, -r):
        ctx.msp.add_lwpolyline(offset_polyline(pts, side), dxfattribs=attribs)
    if el.get("centerline"):
        ctx.msp.add_lwpolyline(pts, dxfattribs={"layer": "CENTER"})
    longest = max(zip(pts, pts[1:]), key=lambda s: Vec2(s[1]).distance(Vec2(s[0])))
    return Vec2(longest[0]).lerp(Vec2(longest[1])), r


def draw_bend(ctx: DrawContext, el: dict):
    """Pipe bend: two concentric arcs (CCW from start_angle to end_angle)."""
    r = _od(ctx, el) / 2
    radius = ctx.size(el, "radius", ("dims", "A"), 1.5 * 2 * r / ctx.u)
    c, a0, a1 = el["center"], float(el["start_angle"]), float(el["end_angle"])
    attribs = ctx.attribs(el)
    for rr in (radius + r, radius - r):
        if rr > 0:
            ctx.msp.add_arc(c, rr, a0, a1, dxfattribs=attribs)
    if el.get("centerline"):
        ctx.msp.add_arc(c, radius, a0, a1, dxfattribs={"layer": "CENTER"})
    mid = math.radians((a0 + a1 + (360 if a1 < a0 else 0)) / 2)
    return Vec2(c) + Vec2.from_angle(mid, radius), r


def draw_trench_drain(ctx: DrawContext, el: dict):
    """Trench drain: outline along the run, closed ends, grate ticks across."""
    pts = el["points"]
    w = ctx.size(el, "width", ("dims", "width"), 300)
    attribs = ctx.attribs(el)
    left, right = offset_polyline(pts, w / 2), offset_polyline(pts, -w / 2)
    for side in (left, right):
        ctx.msp.add_lwpolyline(side, dxfattribs=attribs)
    ctx.msp.add_line(left[0], right[0], dxfattribs=attribs)
    ctx.msp.add_line(left[-1], right[-1], dxfattribs=attribs)
    spacing = ctx.paper(2.0)
    for a, b in zip(pts, pts[1:]):
        a, b = Vec2(a), Vec2(b)
        u = (b - a).normalize()
        n = Vec2(-u.y, u.x) * (w / 2)
        for i in range(1, int(a.distance(b) / spacing)):
            p = a + u * (i * spacing)
            ctx.msp.add_line(p + n, p - n, dxfattribs=attribs)
    return Vec2(pts[0]).lerp(Vec2(pts[1])), w / 2


def draw_polyline(ctx: DrawContext, el: dict):
    """Polyline: solid | dashed | berm (ticks) | ground (grade line with hatch ticks)."""
    pts = el["points"]
    style = el.get("style", "solid")
    attribs = ctx.attribs(el)
    if style == "dashed":
        attribs["linetype"] = "DASHED"
    if style in ("berm", "ground"):
        attribs["lineweight"] = 50
    closed = bool(el.get("closed"))
    ctx.msp.add_lwpolyline(pts, close=closed, dxfattribs=attribs)
    tick_pts = list(pts) + [pts[0]] if closed else pts
    tick_attribs = {k: v for k, v in attribs.items() if k != "lineweight"}
    if style == "berm":
        _ticks(ctx, tick_pts, ctx.paper(4.0), ctx.paper(2.2), tick_attribs)
    elif style == "ground":
        _ticks(ctx, tick_pts, ctx.paper(10.0), ctx.paper(3.0), tick_attribs, slant=1.0)
    return Vec2(pts[0]).lerp(Vec2(pts[1])), 0.0


def draw_rect(ctx: DrawContext, el: dict):
    """Rectangle (building, thrust block, sand bed) with optional hatch."""
    (x0, y0), (x1, y1) = el["corners"]
    attribs = ctx.attribs(el)
    if el.get("style") == "dashed":
        attribs["linetype"] = "DASHED"
    boundary = _rect(x0, y0, x1, y1)
    _hatch(ctx, boundary, el.get("hatch"))
    ctx.msp.add_lwpolyline(boundary, close=True, dxfattribs=attribs)
    return Vec2((x0 + x1) / 2, max(y0, y1)), 0.0


def draw_circle(ctx: DrawContext, el: dict):
    """Plain circle (small fittings, outlets)."""
    r = float(el["radius"])
    ctx.msp.add_circle(el["at"], r, dxfattribs=ctx.attribs(el))
    return Vec2(el["at"]), r


# ------------------------------------------------------------------ block symbols
def draw_flange(ctx: DrawContext, el: dict):
    """Flange in section: t x D rectangle centred on `at`, thickness along the pipe axis."""
    D = ctx.size(el, "D", ("flange", "D"), 285)
    t = ctx.size(el, "t", ("flange", "t"), 24)
    name = ctx.block(f"S2C_FLANGE_{D:g}x{t:g}", lambda b: b.add_lwpolyline(
        _rect(-t / 2, -D / 2, t / 2, D / 2), close=True, dxfattribs=IN_BLOCK))
    _insert(ctx, el, name)
    return Vec2(el["at"]), D / 2


def draw_gate_valve(ctx: DrawContext, el: dict):
    """Gate valve, elevation (stem towards local +Y, top at H) or plan; `at` = centre on the pipe axis."""
    L = ctx.size(el, "L", ("dims", "L"), 210)
    H = ctx.size(el, "H", ("dims", "H"), 448)
    Dt = ctx.size(el, "Dt", ("dims", "Dt"), 212)
    D = ctx.size(el, "D", ("flange", "D"), 285)
    t = ctx.size(el, "t", ("flange", "t"), 24)
    view = el.get("view", "elevation")

    def build(b):
        h, yb = L / 2, 0.35 * D
        for sx in (-1, 1):                                                   # end flanges
            b.add_lwpolyline(_rect(sx * h, -D / 2, sx * (h - t), D / 2), close=True, dxfattribs=IN_BLOCK)
        xi = h - t                                                           # body (bow-tie)
        b.add_lwpolyline([(-xi, -yb), (xi, yb), (xi, -yb), (-xi, yb)], close=True, dxfattribs=IN_BLOCK)
        cap = 0.047 * Dt
        if view == "plan":
            b.add_circle((0, 0), Dt / 2, dxfattribs=IN_BLOCK)
            b.add_lwpolyline(_rect(-cap, -cap, cap, cap), close=True, dxfattribs=IN_BLOCK)
            return
        b.add_lwpolyline([(-Dt / 2, yb), (Dt / 2, yb), (0.33 * Dt, 0.80 * H), (-0.33 * Dt, 0.80 * H)],
                         close=True, dxfattribs=IN_BLOCK)                    # bonnet
        b.add_lwpolyline(_rect(-0.21 * Dt, 0.80 * H, 0.21 * Dt, 0.88 * H), close=True, dxfattribs=IN_BLOCK)
        b.add_line((0, 0.88 * H), (0, 0.915 * H), dxfattribs=IN_BLOCK)        # stem
        b.add_lwpolyline(_rect(-cap, 0.915 * H, cap, H), close=True, dxfattribs=IN_BLOCK)

    name = ctx.block(f"S2C_GV_{view.upper()}_{L:g}_{H:g}_{Dt:g}_{D:g}_{t:g}", build)
    _insert(ctx, el, name)
    return Vec2(el["at"]), D / 2


def draw_connector(ctx: DrawContext, el: dict):
    """Flange connector (e.g. Golan PEX-steel): flange plate at `at`, clamp body along local +X."""
    D = ctx.size(el, "D", ("flange", "D"), 285)
    t = ctx.size(el, "t", ("flange", "t"), 24)
    L = ctx.size(el, "length", ("dims", "L"), 250)
    Hb = ctx.size(el, "height", ("dims", "H"), 0.8 * D / ctx.u)

    def build(b):
        b.add_lwpolyline(_rect(0, -D / 2, t, D / 2), close=True, dxfattribs=IN_BLOCK)
        b.add_lwpolyline(_rect(t, -Hb / 2, t + L, Hb / 2), close=True, dxfattribs=IN_BLOCK)
        lug_w, lug_h = 0.157 * Hb, 0.109 * Hb
        for f in (0.3, 0.75):                                                # clamp bolt lugs
            xc = t + f * L
            b.add_lwpolyline(_rect(xc - lug_w / 2, Hb / 2, xc + lug_w / 2, Hb / 2 + lug_h), close=True,
                             dxfattribs=IN_BLOCK)

    name = ctx.block(f"S2C_CONN_{D:g}_{t:g}_{L:g}_{Hb:g}", build)
    _insert(ctx, el, name)
    return Vec2(el["at"]) + Vec2.from_deg_angle(float(el.get("rotation", 0)), t + L / 2), Hb / 2


def _insert(ctx: DrawContext, el: dict, name: str) -> None:
    attribs = ctx.attribs(el)
    attribs["rotation"] = float(el.get("rotation", 0))
    ctx.msp.add_blockref(name, el["at"], dxfattribs=attribs)


# ------------------------------------------------------------------ plan symbols
def _grate(ctx: DrawContext, c: Vec2, side: float, attribs: dict) -> None:
    """Square grated cover with bars."""
    half = side / 2
    ctx.msp.add_lwpolyline(_rect(c.x - half, c.y - half, c.x + half, c.y + half), close=True, dxfattribs=attribs)
    for i in range(1, 5):
        x = c.x - half + side * i / 5
        ctx.msp.add_line((x, c.y - half), (x, c.y + half), dxfattribs=attribs)


def draw_manhole(ctx: DrawContext, el: dict):
    """Manhole in plan: outer/inner circles + cover (closed lid or grate, may have its own status/item)."""
    c = Vec2(el["at"])
    di = ctx.size(el, "di", ("dims", "Di"), 1000)
    do = ctx.size(el, "do", ("dims", "Do"), di / ctx.u + 300)
    attribs = ctx.attribs(el)
    for r in (do / 2, di / 2):
        ctx.msp.add_circle(c, r, dxfattribs=attribs)
    cover = el.get("cover", "closed")
    cover_attribs = ctx.attribs(el, el.get("cover_status"))
    if cover == "grate":
        cover_item = ctx.item(el.get("cover_item"))
        side = ctx.size(el, "grate_size", ("dims", "clear"), 0.67 * di / ctx.u, item=cover_item)
        _grate(ctx, c, side, cover_attribs)
    elif cover == "closed":
        ctx.msp.add_circle(c, 0.4 * di, dxfattribs=cover_attribs)
    return c, do / 2


def draw_flow_arrow(ctx: DrawContext, el: dict):
    """Flow direction arrow centred on `at`."""
    c = Vec2(el["at"])
    u = Vec2.from_deg_angle(float(el.get("angle", 0)))
    L = float(el.get("length") or ctx.paper(6.0))
    attribs = ctx.attribs(el)
    tip = c + u * (L / 2)
    ctx.msp.add_line(c - u * (L / 2), tip, dxfattribs=attribs)
    base, n = tip - u * ctx.paper(1.5), Vec2(-u.y, u.x) * ctx.paper(0.6)
    ctx.msp.add_solid([tip, base + n, base - n], dxfattribs=attribs)
    return c, 0.0


def draw_north_arrow(ctx: DrawContext, el: dict):
    """North arrow (half-filled) with 'N'; `size` = overall height in paper mm."""
    c = Vec2(el["at"])
    s = ctx.paper(float(el.get("size", 14))) / 5.5
    attribs = {"layer": "TEXT"}
    tip, left, notch, right = (c + Vec2(dx, dy) * s for dx, dy in ((0, 4), (-1.4, -1.5), (0, -0.6), (1.4, -1.5)))
    ctx.msp.add_lwpolyline([tip, left, notch, right], close=True, dxfattribs=attribs)
    ctx.msp.add_solid([tip, notch, right], dxfattribs=attribs)
    put_text(ctx.msp, "N", c + Vec2(0, 5.2 * s), 1.6 * s, align="MIDDLE_CENTER")
    return c, 0.0


def draw_weld(ctx: DrawContext, el: dict):
    """Weld / joint mark: a line across the pipe."""
    x, y = el["at"]
    r = _od(ctx, el) / 2
    p1, p2 = ((x - r, y), (x + r, y)) if el.get("axis", "v") == "v" else ((x, y - r), (x, y + r))
    ctx.msp.add_line(p1, p2, dxfattribs=ctx.attribs(el))
    return Vec2(x, y), r


def draw_break(ctx: DrawContext, el: dict):
    """Pipe break mark (zigzag across the pipe end)."""
    x, y = el["at"]
    r = _od(ctx, el) / 2
    e, a, b, z = (ctx.paper(v) for v in (1.0, 0.8, 0.27, 1.47))
    across = [(-r - e, 0), (-a, 0), (-b, z), (b, -z), (a, 0), (r + e, 0)]
    if el.get("axis", "v") == "v":                        # pipe runs vertically: mark is horizontal
        pts = [(x + s, y + t) for s, t in across]
    else:
        pts = [(x + t, y + s) for s, t in across]
    ctx.msp.add_lwpolyline(pts, dxfattribs=ctx.attribs(el))
    return Vec2(x, y), r


def draw_text(ctx: DrawContext, el: dict):
    """Free text (height in paper mm); with `leader_to` it becomes a leader label."""
    h = ctx.paper(float(el.get("height") or ctx.style["text_height"]))
    lines = pick(el.get("text"), ctx.language)
    if el.get("leader_to"):
        leader_label(ctx, lines, Vec2(el["at"]), Vec2(el["leader_to"]), h)
    else:
        stack_text(ctx, lines, Vec2(el["at"]), h, el.get("align", "left"), float(el.get("rotation", 0)))
    return Vec2(el["at"]), 0.0


DRAWERS = {
    "pipe": draw_pipe, "bend": draw_bend, "flange": draw_flange, "gate_valve": draw_gate_valve,
    "connector": draw_connector, "manhole": draw_manhole, "trench_drain": draw_trench_drain,
    "rect": draw_rect, "polyline": draw_polyline, "text": draw_text, "flow_arrow": draw_flow_arrow,
    "north_arrow": draw_north_arrow, "weld": draw_weld, "break": draw_break, "circle": draw_circle,
}


# ------------------------------------------------------------------ labels
def stack_text(ctx: DrawContext, lines: list[str], at: Vec2, h: float, align="left", rotation=0.0) -> None:
    """Lines stacked downwards from `at` (first baseline at `at`)."""
    down = Vec2.from_deg_angle(rotation - 90, LINE_SPACING * h)
    for i, s in enumerate(lines):
        put_text(ctx.msp, s, at + down * i, h, align=align, rotation=rotation)


def leader_label(ctx: DrawContext, lines: list[str], at: Vec2, target: Vec2, h: float) -> None:
    """Reference-style label: left-aligned lines on a shelf line, leader from the shelf's nearer end to a dot.

    The shelf runs under the text, or over it when the target is above, so the leader never crosses the text.
    """
    if not lines:
        return
    stack_text(ctx, lines, at, h)
    w = max(text_width(s, h) for s in lines)
    if target.y > at.y + h:
        y = at.y + h + ctx.paper(0.8)
    else:
        y = at.y - (len(lines) - 1) * LINE_SPACING * h - ctx.paper(0.8)
    start = Vec2(at.x + w, y) if target.x > at.x + w / 2 else Vec2(at.x, y)
    attribs = {"layer": "TEXT"}
    ctx.msp.add_line((at.x, y), (at.x + w, y), dxfattribs=attribs)
    ctx.msp.add_line(start, target, dxfattribs=attribs)
    ctx.msp.add_circle(target, ctx.paper(0.4), dxfattribs=attribs)


def draw_label(ctx: DrawContext, el: dict, anchor: Vec2, radius: float) -> None:
    """Element label: at `label_at` (leader if `leader` or `leader_to`), else just above the anchor."""
    lines = pick(el.get("label"), ctx.language)
    if not lines:
        return
    h = ctx.text_h("text_height")
    if el.get("label_at"):
        at = Vec2(el["label_at"])
        if el.get("leader") or el.get("leader_to"):
            leader_label(ctx, lines, at, Vec2(el.get("leader_to") or anchor), h)
        else:
            stack_text(ctx, lines, at, h, el.get("label_align", "left"))
        return
    first = anchor + Vec2(0, radius + ctx.paper(1.5) + (len(lines) - 1) * LINE_SPACING * h)
    stack_text(ctx, lines, first, h, "center")


def draw_element(ctx: DrawContext, el: dict) -> None:
    """Draw one element and its label; unknown types are reported, never fatal."""
    drawer = DRAWERS.get(el.get("type"))
    if drawer is None:
        ctx.warnings.append(f"element '{el.get('id', '?')}': unknown type {el.get('type')!r} - skipped")
        return
    anchor, radius = drawer(ctx, el)
    if el.get("type") != "text":
        draw_label(ctx, el, anchor, radius)
