"""Drawing engine: spec.json (docs/FORMATS.md) -> DXF (R2018), plus PDF/PNG previews and DWG (ODA).

    render(spec, out_dir, basename, project=..., document=..., catalog=..., formats=(...))
      -> {"dxf": Path, "pdf": Path, "png": Path, "dwg": Path, "warnings": [str, ...]}

Problems that should not stop a drawing (missing catalog items, a bad element, no DWG converter,
content larger than the sheet) are reported in "warnings" instead of raising.
"""
from __future__ import annotations

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


def render(spec: dict, out_dir, basename: str, *, project=None, document=None, catalog=None,
           formats=("dxf", "pdf", "dwg", "png"), place_labels=True) -> dict:
    """Draw the spec on its title-block sheet and write the requested formats (DXF is always written).

    `place_labels` nudges element labels off other text and linework. Set it false to keep the
    authored point even when it collides (used to draw the "before" of a placement check).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    if catalog is None:
        catalog = _default_catalog(project, warnings)
    # spec first, then the document's and the project's defaults
    value_of = templates.value_of
    language = (spec.get("language") or value_of(document, "language_mode")
                or value_of(project, "language_mode") or "both")
    sheet = spec.get("sheet") or {}
    template = _load_template(sheet.get("template") or value_of(document, "titleblock")
                              or value_of(project, "default_titleblock") or DEFAULT_TEMPLATE, warnings)
    ctx = DrawContext(spec, catalog, language, warnings)
    ctx.nudge_labels = place_labels
    from . import views as sheet_views

    for i, el in enumerate(spec.get("elements", [])):
        try:
            components.draw_element(ctx, el)
        except ELEMENT_ERRORS as exc:
            warnings.append(f"element '{el.get('id', i + 1)}' ({el.get('type')}) skipped: {exc!r}")
    dims.draw_dimensions(ctx, spec.get("dimensions", []))
    entries = spec.get("legend", [])
    legend.draw_balloons(ctx, entries)

    values = templates.build_values(spec, project, document)
    sheet_layout = None
    if spec.get("views"):
        if place_labels:
            from . import place
            place.nudge_dimensions(ctx)
            place.place_labels(ctx)
        rects, sheet_layout = sheet_views.compose(ctx, spec, template, values, language)
    else:
        # legend table / notes: explicit positions are drawn now (they count as content), the rest is laid out
        la = template.get("legend_area")
        max_w = la["w"] if la else None
        side = []
        for block, at in ((legend.legend_table(ctx, entries, max_w), spec.get("legend_at")),
                          (legend.notes_block(ctx, spec.get("notes"), max_w), spec.get("notes_at"))):
            if block and at:
                block.draw(Vec2(at))
            elif block:
                side.append(block)
        content = layout.content_extents(ctx)
        origin, placements = layout.plan_sheet(ctx, template, side, sheet.get("origin"))
        for block, top_left in placements:
            block.draw(top_left)
        if place_labels:
            from . import place
            area = template["drawing_area"]
            k = ctx.k
            bounds = (origin.x + area["x"] * k, origin.y + area["y"] * k,
                      origin.x + (area["x"] + area["w"]) * k, origin.y + (area["y"] + area["h"]) * k)
            place.nudge_dimensions(ctx, bounds)
            place.place_labels(ctx, bounds, content=content)
        rects = templates.draw_titleblock(ctx.msp, template, values, ctx.scale, ctx.units, origin=origin,
                                          language=language)

    from .standards import apply_submission_standard
    apply_submission_standard(ctx.doc)

    result: dict = {"dxf": out_dir / f"{basename}.dxf"}
    if sheet_layout:
        result["layout"] = sheet_layout
    ctx.doc.saveas(result["dxf"])
    previews = {fmt: out_dir / f"{basename}.{fmt}" for fmt in ("pdf", "png") if fmt in formats}
    if previews:
        from . import preview                    # matplotlib only when a preview is requested
        paper = template["paper"]
        preview.export(result["dxf"], previews, rects["paper"], (paper["w"], paper["h"]), layout=sheet_layout)
        result.update(previews)
    if "dwg" in formats:
        dwg = _export_dwg(result["dxf"], out_dir, warnings)
        if dwg:
            result["dwg"] = dwg
    result["warnings"] = warnings
    return result
