"""Drawing engine: spec.json (docs/FORMATS.md) -> DXF (R2018), plus PDF/PNG previews and DWG (ODA).

    render(spec, out_dir, basename, project=..., document=..., catalog=..., formats=(...))
      -> {"dxf": Path, "pdf": Path, "png": Path, "dwg": Path, "warnings": [str, ...]}

Problems that should not stop a drawing (missing catalog items, a bad element, no DWG converter,
content larger than the sheet) are reported in "warnings" instead of raising.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ezdxf.math import Vec2

from .. import templates
from . import components, dims, layout, legend
from .context import DrawContext

DEFAULT_TEMPLATE = "a3_simple"
ELEMENT_ERRORS = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError)


def _default_catalog(project, warnings: list[str]):
    """The shared catalog (repo + project), or None when the catalog module/files are unavailable."""
    try:
        from ..catalog import load_catalog
        return load_catalog(project)
    except Exception as exc:                     # any failure here only costs catalog sizes
        warnings.append(f"catalog unavailable ({exc}) - using element values / defaults")
        return None


def _load_template(template_id: str, warnings: list[str]) -> dict:
    try:
        return templates.load_template(template_id)
    except KeyError:
        warnings.append(f"title-block template {template_id!r} not found - using {DEFAULT_TEMPLATE}")
        return templates.load_template(DEFAULT_TEMPLATE)


def _export_dwg(dxf_path: Path, out_dir: Path, warnings: list[str]) -> Path | None:
    """DWG through the ODA File Converter; skipped with a warning when not installed."""
    try:
        from ..io import oda
        return Path(oda.convert(dxf_path, "dwg", out_dir))
    except Exception as exc:                     # converter missing / failed: DWG is optional
        warnings.append(f"DWG skipped: {exc}")
        return None


@dataclass
class DrawnSheet:
    """One composed sheet: the same origin and frame the renderer saves."""
    ctx: DrawContext
    template: dict
    origin: object
    rects: dict
    language: str
    spans: list = field(default_factory=list)   # {eid, role, in_notes, entities}


def _span(ctx, eid, role, start, spans, in_notes=False):
    entities = list(ctx.msp)[start:]
    if entities:
        spans.append({"eid": str(eid), "role": role, "in_notes": in_notes, "entities": entities})


def draw_sheet(spec: dict, *, catalog=None, project=None, document=None, warnings=None) -> DrawnSheet:
    """Draw the spec exactly as `render` does and return the sheet, including per-element entities."""
    warnings = warnings if warnings is not None else []
    value_of = templates.value_of
    language = (spec.get("language") or value_of(document, "language_mode")
                or value_of(project, "language_mode") or "both")
    sheet = spec.get("sheet") or {}
    template = _load_template(sheet.get("template") or value_of(document, "titleblock")
                              or value_of(project, "default_titleblock") or DEFAULT_TEMPLATE, warnings)
    ctx = DrawContext(spec, catalog, language, warnings)
    spans: list = []

    for i, el in enumerate(spec.get("elements", [])):
        start = len(list(ctx.msp))
        try:
            components.draw_element(ctx, el)
        except ELEMENT_ERRORS as exc:
            warnings.append(f"element '{el.get('id', i + 1)}' ({el.get('type')}) skipped: {exc!r}")
            continue
        kind = el.get("type")
        declared = el.get("role")
        if declared in ("title", "text", "label", "notes", "annotation"):
            role = declared
        elif kind == "text":
            role = "text"
        elif kind == "north_arrow":
            role = "north"
        else:
            role = "label"
        _span(ctx, el.get("id") or f"_{i}", role, start, spans)
    start = len(list(ctx.msp))
    dims.draw_dimensions(ctx, spec.get("dimensions", []))
    for j, entity in enumerate(list(ctx.msp)[start:]):
        spans.append({"eid": f"dim:{j}", "role": "dim", "in_notes": False, "entities": [entity]})
    entries = spec.get("legend", [])
    start = len(list(ctx.msp))
    legend.draw_balloons(ctx, entries)
    _span(ctx, "balloon", "balloon", start, spans)

    # legend table / notes: explicit positions are drawn now (they count as content), the rest is laid out
    la = template.get("legend_area")
    max_w = la["w"] if la else None
    side = []
    side_meta = []
    for block, at, role in ((legend.legend_table(ctx, entries, max_w), spec.get("legend_at"), "legend"),
                            (legend.notes_block(ctx, spec.get("notes"), max_w), spec.get("notes_at"), "notes")):
        if block and at:
            start = len(list(ctx.msp))
            block.draw(Vec2(at))
            _span(ctx, role, "table" if role == "legend" else "notes", start, spans, in_notes=True)
        elif block:
            side.append(block)
            side_meta.append(role)
    origin, placements = layout.plan_sheet(ctx, template, side, sheet.get("origin"))
    for (block, top_left), role in zip(placements, side_meta):
        start = len(list(ctx.msp))
        block.draw(top_left)
        _span(ctx, role, "table" if role == "legend" else "notes", start, spans, in_notes=True)

    values = templates.build_values(spec, project, document)
    start = len(list(ctx.msp))
    rects = templates.draw_titleblock(ctx.msp, template, values, ctx.scale, ctx.units, origin=origin,
                                      language=language)
    for j, entity in enumerate(list(ctx.msp)[start:]):
        if entity.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
            spans.append({"eid": f"title:{j}", "role": "title", "in_notes": True, "entities": [entity]})
    return DrawnSheet(ctx, template, origin, rects, language, spans)


def render(spec: dict, out_dir, basename: str, *, project=None, document=None, catalog=None,
           formats=("dxf", "pdf", "dwg", "png")) -> dict:
    """Draw the spec on its title-block sheet and write the requested formats (DXF is always written)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if catalog is None:
        catalog = _default_catalog(project, warnings)
    drawn = draw_sheet(spec, catalog=catalog, project=project, document=document, warnings=warnings)
    ctx = drawn.ctx
    template, rects = drawn.template, drawn.rects

    result: dict = {"dxf": out_dir / f"{basename}.dxf"}
    ctx.doc.saveas(result["dxf"])
    previews = {fmt: out_dir / f"{basename}.{fmt}" for fmt in ("pdf", "png") if fmt in formats}
    if previews:
        from . import preview                    # matplotlib only when a preview is requested
        paper = template["paper"]
        preview.export(result["dxf"], previews, rects["paper"], (paper["w"], paper["h"]))
        result.update(previews)
    if "dwg" in formats:
        dwg = _export_dwg(result["dxf"], out_dir, warnings)
        if dwg:
            result["dwg"] = dwg
    result["warnings"] = warnings
    return result
