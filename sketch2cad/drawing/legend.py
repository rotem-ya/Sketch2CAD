"""Legend (spec section 1.3): numbered balloons with leaders, the bilingual legend table and the notes block.

The table and the notes are measured first (Block: size in drawing units + a draw(top_left) callback)
so the layout can place them before anything is drawn.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ezdxf.math import Vec2

from .context import DrawContext
from .text import LINE_SPACING, pick, put_text, text_width, wrap

PAD_X, PAD_Y = 1.5, 1.0            # cell padding, paper mm
GAP = 1.5                         # title -> table gap, paper mm
COL_CAP = {"en": 110.0, "he": 95.0}
HEADERS = {"no": {"en": "No.", "he": "מס'"}, "en": "DESCRIPTION", "he": "תיאור"}
TITLES = {"legend": {"en": "LEGEND", "he": "מקרא"}, "notes": {"en": "NOTES:", "he": "הערות:"}}


@dataclass
class Block:
    """A measured, not yet placed group of entities (sizes in drawing units)."""
    width: float
    height: float
    draw: Callable[[Vec2], None]          # draws with the block's top-left corner at the given point


# ------------------------------------------------------------------ balloons
def draw_balloons(ctx: DrawContext, legend: list[dict]) -> None:
    """Balloon circles with numbers; leader i runs from balloon i (or the last balloon) to target i."""
    r = ctx.paper(ctx.style["balloon_radius"])
    attribs = {"layer": "LEGEND"}
    for entry in legend:
        balloons = [Vec2(b) for b in entry.get("balloons") or []]
        for b in balloons:
            ctx.msp.add_circle(b, r, dxfattribs=attribs)
            put_text(ctx.msp, str(entry.get("no", "")), b, 0.78 * r, layer="LEGEND", align="MIDDLE_CENTER")
        if not balloons:
            continue
        for i, t in enumerate(entry.get("targets") or []):
            b, t = balloons[min(i, len(balloons) - 1)], Vec2(t)
            if b.distance(t) <= r:
                continue
            ctx.msp.add_line(b + (t - b).normalize(r), t, dxfattribs=attribs)
            ctx.msp.add_circle(t, ctx.paper(0.45), dxfattribs=attribs)


# ------------------------------------------------------------------ legend table
def legend_texts(ctx: DrawContext, entry: dict) -> dict:
    """Entry description per language; falls back to the catalog item's name."""
    name = ctx.item(entry.get("item")).get("name") or {}
    return {lang: entry.get(lang) or name.get(lang) or "" for lang in ("en", "he")}


def legend_table(ctx: DrawContext, legend: list[dict], max_width: float | None = None) -> Block | None:
    """No | DESCRIPTION | תיאור (columns per language mode; Hebrew-only puts the number on the right)."""
    if not legend:
        return None
    th = ctx.style["table_text_height"]                                   # paper mm until the end
    langs = {"en": ["en"], "he": ["he"]}.get(ctx.language, ["en", "he"])
    rows = [{"no": str(e.get("no", "")), **legend_texts(ctx, e)} for e in legend]
    no_label = HEADERS["no"]["he" if langs == ["he"] else "en"]
    header = {"no": no_label, "en": HEADERS["en"], "he": HEADERS["he"]}

    no_w = max(text_width(s, th) for s in [no_label] + [r["no"] for r in rows]) + 2 * PAD_X
    widths = {lang: min(COL_CAP[lang], max(text_width(r[lang], th) for r in rows + [header]) + 2 * PAD_X)
              for lang in langs}
    if max_width and no_w + sum(widths.values()) > max_width:
        f = (max_width - no_w) / sum(widths.values())
        widths = {lang: w * f for lang, w in widths.items()}
    cols = [("no", no_w)] + [(lang, widths[lang]) for lang in langs]
    if langs == ["he"]:
        cols.reverse()

    def cell_lines(key: str, s: str, w: float) -> list[str]:
        return [s] if key == "no" else wrap(s, th, w - 2 * PAD_X) or [""]

    table = [{key: cell_lines(key, row[key], w) for key, w in cols} for row in [header] + rows]
    line = LINE_SPACING * th
    heights = [max(len(c) for c in r.values()) * line + 2 * PAD_Y for r in table]
    title_h = ctx.style["legend_title_height"]
    title = " / ".join(pick(TITLES["legend"], ctx.language))
    k = ctx.k
    width, height = sum(w for _, w in cols), title_h + GAP + sum(heights)

    def draw(top_left: Vec2) -> None:
        x0, y = top_left.x, top_left.y - (title_h + GAP) * k
        rtl = langs == ["he"]                                            # Hebrew-only: title on the right
        title_at = Vec2(x0 + width * k if rtl else x0, top_left.y - title_h * k)
        put_text(ctx.msp, title, title_at, title_h * k, layer="LEGEND", align="right" if rtl else "left")
        xs = [x0]
        for _, w in cols:
            xs.append(xs[-1] + w * k)
        attribs = {"layer": "LEGEND"}
        y_top = y
        for i, (row, h) in enumerate(zip(table, heights)):
            ctx.msp.add_line((xs[0], y), (xs[-1], y), dxfattribs=attribs)
            ym = y - h * k / 2
            for (key, _), xl, xr in zip(cols, xs, xs[1:]):
                s = "\\P".join(row[key])
                if key == "no":
                    put_text(ctx.msp, s, ((xl + xr) / 2, ym), th * k, layer="LEGEND", align="MIDDLE_CENTER")
                elif key == "he":
                    put_text(ctx.msp, s, (xr - PAD_X * k, ym), th * k, layer="LEGEND", align="MIDDLE_RIGHT")
                else:
                    put_text(ctx.msp, s, (xl + PAD_X * k, ym), th * k, layer="LEGEND", align="MIDDLE_LEFT")
            y -= h * k
            if i == 0:                                                   # double line under the header
                ctx.msp.add_line((xs[0], y - 0.5 * k), (xs[-1], y - 0.5 * k), dxfattribs=attribs)
        ctx.msp.add_line((xs[0], y), (xs[-1], y), dxfattribs=attribs)
        for x in xs:
            ctx.msp.add_line((x, y_top), (x, y), dxfattribs=attribs)

    return Block(width * k, height * k, draw)


# ------------------------------------------------------------------ notes
def notes_block(ctx: DrawContext, notes: dict | None, width: float | None = None) -> Block | None:
    """Numbered general notes; English block left-aligned, Hebrew block right-aligned below it."""
    notes = notes or {}
    th = ctx.style["notes_text_height"]
    width = width or ctx.style["notes_width"]
    langs = {"en": ["en", "he"], "he": ["he", "en"]}.get(ctx.language)
    if langs:                                    # single language: the other one only as a fallback
        langs = [next((lang for lang in langs if notes.get(lang)), langs[0])]
    else:
        langs = [lang for lang in ("en", "he") if notes.get(lang)]
    parts = []
    for lang in langs:
        items = notes.get(lang) or []
        if not items:
            continue
        lines = [TITLES["notes"][lang]]
        for i, note in enumerate(items, 1):
            lines += wrap(f"{i}. {note}", th, width)
        parts.append((lang, lines))
    if not parts:
        return None
    line = LINE_SPACING * th
    gap = line
    height = sum(len(lines) for _, lines in parts) * line + gap * (len(parts) - 1)
    k = ctx.k

    def draw(top_left: Vec2) -> None:
        y = top_left.y
        for lang, lines in parts:
            s = "\\P".join(lines)
            if lang == "he":
                put_text(ctx.msp, s, (top_left.x + width * k, y), th * k, align="TOP_RIGHT")
            else:
                put_text(ctx.msp, s, (top_left.x, y), th * k, align="TOP_LEFT")
            y -= (len(lines) * line + gap) * k

    return Block(width * k, height * k, draw)
