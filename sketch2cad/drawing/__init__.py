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


def _names_title(value) -> bool:
    """True when an id or role is a title, including `sec_title` and `section_title`."""
    parts = str(value or "").lower().replace("-", " ").replace("_", " ").split()
    return any(part == "title" or part.endswith("title") for part in parts)


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


def _span(ctx, eid, role, start, spans, in_notes=False, force=False):
    entities = list(ctx.msp)[start:]
    if not entities and not force:
        return None
    span = {"eid": str(eid), "role": role, "in_notes": in_notes, "entities": entities}
    spans.append(span)
    return span


def _resolve_sheet_scale(spec: dict, template: dict, warnings: list[str], *,
                         catalog=None, language: str = "both", place_labels: bool = True) -> dict:
    """Copy `spec` with a numeric `scale`. `"auto"` is the largest standard scale that fits."""
    spec = dict(spec)
    raw = spec.get("scale", 1)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return spec
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        spec["scale"] = _auto_sheet_denominator(
            spec, template, catalog=catalog, language=language, place_labels=place_labels)
        return spec
    if isinstance(raw, str) and "".join(ch for ch in raw.lower() if ch.isalnum()) == "nts":
        spec["scale"] = _auto_sheet_denominator(
            spec, template, catalog=catalog, language=language, place_labels=place_labels)
        spec["nts"] = True
        return spec
    warnings.append(f"ERROR: scale {raw!r} is not a positive number, 'auto' or 'NTS' - using 20")
    spec["scale"] = 20
    return spec


def sheet_scale_number(spec: dict) -> float:
    """The denominator the sheet is drawn at. ``auto`` and ``NTS`` are resolved before any arithmetic."""
    token = spec.get("scale") if isinstance(spec, dict) else None
    cached = getattr(sheet_scale_number, "_cache", None)
    if cached is not None and cached[0] is spec and cached[1] == token:
        return cached[2]
    warnings: list[str] = []
    template_id = (spec.get("sheet") or {}).get("template") or DEFAULT_TEMPLATE if isinstance(spec, dict) else DEFAULT_TEMPLATE
    template = _load_template(template_id, warnings)
    language = (spec.get("language") if isinstance(spec, dict) else None) or "both"
    value = float(_resolve_sheet_scale(spec if isinstance(spec, dict) else {}, template, warnings,
                                        language=language)["scale"])
    sheet_scale_number._cache = (spec, token, value)
    return value


def _content_box(spec: dict, template: dict, catalog, language: str, place_labels: bool):
    """The box `plan_sheet` checks, after labels, dimension text and the fitted notes block.

    `spec["scale"]` is already a number. Notes are annexed only on a single-view sheet; on a
    multi-view sheet they are paper-space text and the caller compares this box with the
    primary viewport.
    """
    from . import place

    warnings: list[str] = []
    ctx = DrawContext(spec, catalog, language, warnings)
    ctx.nudge_labels = place_labels
    for el in spec.get("elements") or []:
        if not isinstance(el, dict):
            continue
        try:
            components.draw_element(ctx, el)
        except ELEMENT_ERRORS:
            continue
    dims.draw_dimensions(ctx, [d for d in (spec.get("dimensions") or []) if isinstance(d, dict)])
    legend.draw_balloons(ctx, [e for e in (spec.get("legend") or []) if isinstance(e, dict)])
    la = template.get("legend_area")
    max_w = la["w"] if la else None
    side, roles = [], []
    multi = any(isinstance(view, dict) for view in (spec.get("views") or []))
    if not multi:
        for block, at, role in (
                (legend.legend_table(ctx, spec.get("legend") or [], max_w), spec.get("legend_at"), "legend"),
                (legend.notes_block(ctx, spec.get("notes"), max_w), spec.get("notes_at"), "notes")):
            if block and at:
                block.draw(Vec2(at))
            elif block:
                side.append(block)
                roles.append(role)
    place.draw_fixed_text(ctx)
    if place_labels:
        place.nudge_dimensions(ctx)
        place.place_labels(ctx)
        place.nudge_dimensions(ctx)
    extents = layout.content_extents(ctx)
    if multi:
        return ctx, extents
    side = layout.refit_notes(ctx, spec.get("notes"), template, side, roles, extents)
    box = extents
    for block in side:
        box = layout._annex(ctx, box, block, template)
    return ctx, box


def _fits_area(ctx, box, area: dict) -> bool:
    """Same comparison as `layout._check_fit`: the box is within `area` at this scale."""
    return layout._paper_fits((box[2] - box[0], box[3] - box[1]), area, ctx.k)


def _auto_sheet_denominator(spec: dict, template: dict, *, catalog=None, language: str = "both",
                            place_labels: bool = True) -> float:
    """Largest standard scale whose drawn sheet passes the render's own fit test.

    Each candidate is drawn (labels, dimension text, and on a single view the notes block)
    and measured with `content_extents`. The first scale that fits is kept. On a multi-view
    sheet the limit is the paper left after the other views, not the whole drawing area.
    """
    from .layout import STANDARD_SCALES
    from .views import primary_free_mm

    free = primary_free_mm(spec, template, language)
    if free is None:
        area = template.get("drawing_area") or {"w": 380, "h": 240}
    else:
        area = {"w": free[0], "h": free[1]}
    if float(area["w"]) <= 1 or float(area["h"]) <= 1:
        return 50.0
    import copy
    probe = copy.deepcopy(spec)
    for scale in STANDARD_SCALES:
        probe["scale"] = float(scale)
        ctx, box = _content_box(probe, template, catalog, language, place_labels)
        if _fits_area(ctx, box, area):
            return float(scale)
    return float(STANDARD_SCALES[-1])


def _link_span(ctx, el, span) -> None:
    if span is None:
        return
    for item in ctx.pending:
        if item.get("el") is el and item.get("span") is None:
            item["span"] = span


def draw_sheet(spec: dict, *, catalog=None, project=None, document=None, warnings=None,
               place_labels=True) -> DrawnSheet:
    """Draw the spec exactly as `render` does and return the sheet, including per-element entities."""
    warnings = warnings if warnings is not None else []
    value_of = templates.value_of
    language = (spec.get("language") or value_of(document, "language_mode")
                or value_of(project, "language_mode") or "both")
    sheet = spec.get("sheet") or {}
    template = _load_template(sheet.get("template") or value_of(document, "titleblock")
                              or value_of(project, "default_titleblock") or DEFAULT_TEMPLATE, warnings)
    spec = _resolve_sheet_scale(spec, template, warnings, catalog=catalog, language=language,
                                place_labels=place_labels)
    ctx = DrawContext(spec, catalog, language, warnings)
    ctx.nudge_labels = place_labels
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
        # A section title is a text element. Its id (`sec_title`) or role names the title even
        # when `role` itself is missing or just "text".
        if kind == "text" and (_names_title(declared) or _names_title(el.get("id"))):
            role = "title"
        elif declared in ("title", "text", "label", "notes", "annotation"):
            role = declared
        elif kind == "text":
            role = "text"
        elif kind == "north_arrow":
            role = "north"
        else:
            role = "label"
        span = _span(ctx, el.get("id") or f"_{i}", role, start, spans, force=(kind == "text"))
        _link_span(ctx, el, span)
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
    # Labels are placed before the notes column is chosen, so that column moves aside instead of
    # covering a label. Dimension text then slides off a label that had to keep its spec point.
    # The content box includes those labels and the dimension text, so the frame is centred on them.
    from . import place
    place.draw_fixed_text(ctx)
    if place_labels:
        place.nudge_dimensions(ctx)
        place.place_labels(ctx)
        place.nudge_dimensions(ctx)
    extents = layout.content_extents(ctx)
    side = layout.refit_notes(ctx, spec.get("notes"), template, side, side_meta, extents)
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


def _draw_views(spec, catalog, project, document, warnings, place_labels):
    """One paperspace sheet. Labels are placed before each viewport is measured."""
    from . import place
    from . import views as sheet_views

    value_of = templates.value_of
    language = (spec.get("language") or value_of(document, "language_mode")
                or value_of(project, "language_mode") or "both")
    sheet = spec.get("sheet") or {}
    template = _load_template(sheet.get("template") or value_of(document, "titleblock")
                              or value_of(project, "default_titleblock") or DEFAULT_TEMPLATE, warnings)
    spec = _resolve_sheet_scale(spec, template, warnings, catalog=catalog, language=language,
                                place_labels=place_labels)
    ctx = DrawContext(spec, catalog, language, warnings)
    ctx.nudge_labels = place_labels
    for i, el in enumerate(spec.get("elements", [])):
        try:
            components.draw_element(ctx, el)
        except ELEMENT_ERRORS as exc:
            warnings.append(f"element '{el.get('id', i + 1)}' ({el.get('type')}) skipped: {exc!r}")
    dims.draw_dimensions(ctx, spec.get("dimensions", []))
    legend.draw_balloons(ctx, spec.get("legend", []))
    values = templates.build_values(spec, project, document)
    place.draw_fixed_text(ctx)
    if place_labels:
        place.nudge_dimensions(ctx)
        place.place_labels(ctx)
        place.nudge_dimensions(ctx)
    primary_bbox = layout.content_extents(ctx)
    rects, sheet_layout = sheet_views.compose(
        ctx, spec, template, values, language, primary_bbox=primary_bbox)
    return ctx, template, rects, sheet_layout


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
    sheet_layout = None
    if spec.get("views"):
        ctx, template, rects, sheet_layout = _draw_views(
            spec, catalog, project, document, warnings, place_labels)
    else:
        drawn = draw_sheet(spec, catalog=catalog, project=project, document=document,
                           warnings=warnings, place_labels=place_labels)
        ctx, template, rects = drawn.ctx, drawn.template, drawn.rects
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
