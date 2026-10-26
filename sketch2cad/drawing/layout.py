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


def content_extents(ctx: DrawContext, ignore=()) -> tuple[float, float, float, float]:
    """Model-space extents of everything drawn so far (0-size box at the origin when empty).

    Labels, free text and dimension text are part of the box the sheet is centred on.
    `ignore` leaves entities out of that box.
    """
    skip = set(ignore)
    entities = [entity for entity in ctx.msp if entity not in skip]
    ext = bbox.extents(entities, fast=True)
    xs, ys = [], []
    if ext.has_data:
        xs.extend((ext.extmin.x, ext.extmax.x))
        ys.extend((ext.extmin.y, ext.extmax.y))
    # Dimension text lives in the anonymous block. Union it in so a recentre cannot
    # push it through the frame when the dimension entity's own box stops at the line.
    from .place import _dimension_texts, _entity_box
    for entity in entities:
        if entity.dxftype() != "DIMENSION":
            continue
        for text in _dimension_texts(entity):
            box = _entity_box(text)
            if not box:
                continue
            xs.extend((box[0], box[2]))
            ys.extend((box[1], box[3]))
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


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


def notes_fit(ctx: DrawContext, block: Block, extents, template: dict) -> bool:
    """True when `block` fits beside or below `extents`, or in the legend column."""
    k, g = ctx.k, GAP * ctx.k
    la = template.get("legend_area")
    if la:
        return _paper_fits((block.width, block.height), la, k)
    da = template["drawing_area"]
    x0, y0, x1, y1 = extents
    bw, bh = block.width, block.height
    side_size = ((x1 - x0) + g + bw, max(y1 - y0, bh))
    below_size = (max(x1 - x0, bw), (y1 - y0) + g + bh)
    return _paper_fits(side_size, da, k) or _paper_fits(below_size, da, k)


def _annex(ctx: DrawContext, extents, block: Block, template: dict):
    """Extents after `block` is placed beside the drawing, or below it when that fits better."""
    if template.get("legend_area"):
        return extents
    k, g = ctx.k, GAP * ctx.k
    da = template["drawing_area"]
    x0, y0, x1, y1 = extents
    bw, bh = block.width, block.height
    side_size = ((x1 - x0) + g + bw, max(y1 - y0, bh))
    below_size = (max(x1 - x0, bw), (y1 - y0) + g + bh)
    side_ok = _paper_fits(side_size, da, k)
    below_ok = _paper_fits(below_size, da, k)

    def fill(size):
        return max(size[0] / (da["w"] * k), size[1] / (da["h"] * k))

    if below_ok and (not side_ok or fill(below_size) <= fill(side_size)) or not side_ok:
        y0 -= g + bh
        x1 = max(x1, x0 + bw)
    else:
        x1 += g + bw
        y0 = min(y0, y1 - bh)
    return x0, y0, x1, y1


def choose_notes_block(ctx: DrawContext, notes, template: dict, extents) -> Block | None:
    """Notes that fit. Try the ISO height, then 2.0 mm, then EN and HE side by side.

    The last candidate is returned even when it still overflows: the sheet draws it below
    the view instead of dropping the text.
    """
    from .legend import notes_block

    # Stacked at the style height, stacked at 2.0 mm, then the same two heights in two columns.
    options = ({}, {"height": 2.0}, {"columns": True}, {"height": 2.0, "columns": True})
    chosen = None
    for option in options:
        block = notes_block(ctx, notes, **option)
        if block is None:
            return None
        chosen = block
        if notes_fit(ctx, block, extents, template):
            return block
    return chosen


def refit_notes(ctx: DrawContext, notes, template: dict, side: list[Block], roles: list[str],
                extents) -> list[Block]:
    """Replace a side notes block with one that fits next to the drawing already measured."""
    if "notes" not in roles:
        return side
    box = extents
    out = []
    for block, role in zip(side, roles):
        if role == "notes":
            block = choose_notes_block(ctx, notes, template, box) or block
        out.append(block)
        box = _annex(ctx, box, block, template)
    return out


def plan_sheet(ctx: DrawContext, template: dict, side: list[Block], origin=None, ignore=()):
    """Return (frame origin in model space, [(block, top_left)]) for the current drawing content."""
    k, g = ctx.k, GAP * ctx.k
    da = template["drawing_area"]
    x0, y0, x1, y1 = content_extents(ctx, ignore)
    la = template.get("legend_area")
    placements: list[tuple[Block, Vec2]] = []
    if side and la:
        size = (max(b.width for b in side), sum(b.height for b in side) + g * (len(side) - 1))
        _check_fit(ctx, size, la, "legend / notes")
    elif side:
        # One block at a time. Beside the drawing, or below it. A block is never dropped.
        for block in side:
            bw, bh = block.width, block.height
            side_size = ((x1 - x0) + g + bw, max(y1 - y0, bh))
            below_size = (max(x1 - x0, bw), (y1 - y0) + g + bh)
            side_ok = _paper_fits(side_size, da, k)
            below_ok = _paper_fits(below_size, da, k)

            def fill(size):
                return max(size[0] / (da["w"] * k), size[1] / (da["h"] * k))

            if below_ok and (not side_ok or fill(below_size) <= fill(side_size)) or not side_ok:
                placements.append((block, Vec2(x0, y0 - g)))
                y0 -= g + bh
                x1 = max(x1, x0 + bw)
            else:
                placements.append((block, Vec2(x1 + g, y1)))
                x1 += g + bw
                y0 = min(y0, y1 - bh)
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
