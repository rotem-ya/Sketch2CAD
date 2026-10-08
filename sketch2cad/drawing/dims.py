"""Chain dimensions (spec section 1.2): horizontal / vertical, per-segment text overrides."""
from __future__ import annotations

from .context import DIMSTYLE, DrawContext


def draw_chain(ctx: DrawContext, dim: dict) -> None:
    """One linear dimension per consecutive point pair, all on the line at `base`.

    texts[i] overrides segment i: "<>" = measured value, "" = default (measured value).
    """
    pts = [tuple(p) for p in dim["points"]]
    axis = dim.get("axis", "h")
    base = float(dim["base"])
    texts = dim.get("texts") or []
    override = {"dimdec": int(dim["decimals"])} if "decimals" in dim else None
    for i, (p1, p2) in enumerate(zip(pts, pts[1:])):
        if axis == "h":
            measured, base_pt, angle = abs(p2[0] - p1[0]), (p1[0], base), 0
        else:
            measured, base_pt, angle = abs(p2[1] - p1[1]), (base, p1[1]), 90
        if measured < 1e-9:
            continue
        text = (texts[i] if i < len(texts) else "") or "<>"
        ctx.msp.add_linear_dim(base=base_pt, p1=p1, p2=p2, angle=angle, text=text, dimstyle=DIMSTYLE,
                               override=override, dxfattribs={"layer": "DIM"}).render()


def draw_dimensions(ctx: DrawContext, dims: list[dict]) -> None:
    for i, dim in enumerate(dims):
        if dim.get("type", "chain") != "chain":
            ctx.warnings.append(f"dimension {i + 1}: unknown type {dim.get('type')!r} - skipped")
            continue
        draw_chain(ctx, dim)
