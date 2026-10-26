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


def _suggest_scale(scale: float, fill: float) -> int:
    """Next standard denominator that fits. `fill` is content / free space.

    A view that does not fit needs a smaller scale, which is a larger denominator.
    Never suggest a larger scale (a smaller denominator) than `scale`.
    """
    needed = scale * max(float(fill), 1.0)
    return next((s for s in STANDARD_SCALES if s + 1e-9 >= needed), round(needed))


def _check_fit(ctx: DrawContext, size: tuple[float, float], area: dict, what: str) -> None:
    fill = max(size[0] / (area["w"] * ctx.k), size[1] / (area["h"] * ctx.k))
    if fill <= 1.0 + 1e-6:
        return
    better = _suggest_scale(ctx.scale, fill)
    ctx.warnings.append(
        f"ERROR: {what} ({size[0] / ctx.k:.0f} x {size[1] / ctx.k:.0f} paper mm) does not fit the "
        f"{area['w']:.0f} x {area['h']:.0f} mm area at 1:{ctx.scale:g} - consider 1:{better}")


def _paper_fits(size: tuple[float, float], area: dict, k: float) -> bool:
    return size[0] <= area["w"] * k + 1e-4 and size[1] <= area["h"] * k + 1e-4


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
        # One block at a time. A block that does not fit beside the drawing goes below it.
        # A block that fits neither is left undrawn instead of hanging past the frame.
        for block in side:
            bw, bh = block.width, block.height
            side_size = ((x1 - x0) + g + bw, max(y1 - y0, bh))
            below_size = (max(x1 - x0, bw), (y1 - y0) + g + bh)
            side_ok = _paper_fits(side_size, da, k)
            below_ok = _paper_fits(below_size, da, k)

            def fill(size):
                return max(size[0] / (da["w"] * k), size[1] / (da["h"] * k))

            if below_ok and (not side_ok or fill(below_size) <= fill(side_size)):
                placements.append((block, Vec2(x0, y0 - g)))
                y0 -= g + bh
                x1 = max(x1, x0 + bw)
            elif side_ok:
                placements.append((block, Vec2(x1 + g, y1)))
                x1 += g + bw
                y0 = min(y0, y1 - bh)
            else:
                overflow = min(fill(side_size), fill(below_size))
                better = _suggest_scale(ctx.scale, overflow)
                ctx.warnings.append(
                    f"ERROR: notes ({bw / k:.0f} x {bh / k:.0f} paper mm) do not fit beside or below "
                    f"the drawing in the {da['w']:.0f} x {da['h']:.0f} mm area at 1:{ctx.scale:g} - "
                    f"not drawn off the sheet. Consider 1:{better:g} or a larger sheet")
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
