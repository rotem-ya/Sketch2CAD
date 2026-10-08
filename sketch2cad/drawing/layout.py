"""Sheet layout: centre the drawing in the template's drawing area and place the legend/notes blocks.

Blocks not placed explicitly go into the template's legend_area (stacked) when it has one, otherwise next to
the drawing - to the right (stacked) or below (side by side), whichever needs the smaller sheet.
Nothing is ever scaled: if it does not fit, it is still drawn and a warning suggests a scale.
"""
from __future__ import annotations

from ezdxf import bbox
from ezdxf.math import Vec2

from .context import DrawContext
from .legend import Block

GAP = 6.0                                     # paper mm between drawing and side blocks
STANDARD_SCALES = (1, 2, 5, 10, 15, 20, 25, 50, 75, 100, 125, 150, 200, 250, 400, 500, 750, 1000, 1250,
                   2000, 2500, 5000, 10000)


def content_extents(ctx: DrawContext) -> tuple[float, float, float, float]:
    """Model-space extents of everything drawn so far (0-size box at the origin when empty)."""
    ext = bbox.extents(ctx.msp, fast=True)
    if not ext.has_data:
        return 0.0, 0.0, 0.0, 0.0
    return ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y


def _stack(blocks: list[Block], top_left: Vec2, gap: float, vertical: bool) -> list[tuple[Block, Vec2]]:
    out, p = [], top_left
    for b in blocks:
        out.append((b, p))
        p = p + (Vec2(0, -(b.height + gap)) if vertical else Vec2(b.width + gap, 0))
    return out


def _check_fit(ctx: DrawContext, size: tuple[float, float], area: dict, what: str) -> None:
    fill = max(size[0] / (area["w"] * ctx.k), size[1] / (area["h"] * ctx.k))
    if fill <= 1.0 + 1e-6:
        return
    needed = ctx.scale * fill
    better = next((s for s in STANDARD_SCALES if s >= needed), round(needed))
    ctx.warnings.append(
        f"{what} ({size[0] / ctx.k:.0f} x {size[1] / ctx.k:.0f} paper mm) does not fit the "
        f"{area['w']:.0f} x {area['h']:.0f} mm area at 1:{ctx.scale:g} - consider 1:{better}")


def plan_sheet(ctx: DrawContext, template: dict, side: list[Block], origin=None):
    """Return (frame origin in model space, [(block, top_left)]) for the current drawing content."""
    k, g = ctx.k, GAP * ctx.k
    da = template["drawing_area"]
    x0, y0, x1, y1 = content_extents(ctx)
    la = template.get("legend_area")
    placements: list[tuple[Block, Vec2]] = []
    if side and la:
        size = (max(b.width for b in side), sum(b.height for b in side) + g * (len(side) - 1))
        _check_fit(ctx, size, la, "legend / notes")
    elif side:
        w, h = x1 - x0, y1 - y0
        right = (max(b.width for b in side), sum(b.height for b in side) + g * (len(side) - 1))
        below = (sum(b.width for b in side) + g * (len(side) - 1), max(b.height for b in side))

        def fill(s):
            return max(s[0] / (da["w"] * k), s[1] / (da["h"] * k))

        if fill((max(w, below[0]), h + g + below[1])) <= fill((w + g + right[0], max(h, right[1]))):
            placements = _stack(side, Vec2(x0, y0 - g), g, vertical=False)
            y0 -= g + below[1]
            x1 = max(x1, x0 + below[0])
        else:
            placements = _stack(side, Vec2(x1 + g, y1), g, vertical=True)
            x1 += g + right[0]
            y0 = min(y0, y1 - right[1])
    _check_fit(ctx, (x1 - x0, y1 - y0), da, "drawing")
    if origin is None:
        centre = Vec2((x0 + x1) / 2, (y0 + y1) / 2)
        origin = centre - Vec2(da["x"] + da["w"] / 2, da["y"] + da["h"] / 2) * k
    else:
        origin = Vec2(origin)
        fx1, fy1 = origin.x + template["frame"]["w"] * k, origin.y + template["frame"]["h"] * k
        if x0 < origin.x or y0 < origin.y or x1 > fx1 or y1 > fy1:
            ctx.warnings.append("drawing extends beyond the frame at the given sheet origin")
    if side and la:
        placements = _stack(side, origin + Vec2(la["x"], la["y"] + la["h"]) * k, g, vertical=True)
    return origin, placements
