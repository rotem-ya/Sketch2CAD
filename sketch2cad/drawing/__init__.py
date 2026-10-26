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


def _resolve_sheet_scale(spec: dict, template: dict, warnings: list[str]) -> dict:
    """Copy `spec` with a numeric `scale`. `"auto"` is the largest standard scale that fits."""
    spec = dict(spec)
    raw = spec.get("scale", 1)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return spec
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        spec["scale"] = _auto_sheet_denominator(spec, template)
        return spec
    if isinstance(raw, str) and "".join(ch for ch in raw.lower() if ch.isalnum()) == "nts":
        spec["scale"] = _auto_sheet_denominator(spec, template)
        spec["nts"] = True
        return spec
    warnings.append(f"ERROR: scale {raw!r} is not a positive number, 'auto' or 'NTS' - using 20")
    spec["scale"] = 20
    return spec


def sheet_scale_number(spec: dict) -> float:
    """The denominator the sheet is drawn at. ``auto`` and ``NTS`` are resolved before any arithmetic."""
    warnings: list[str] = []
    template_id = (spec.get("sheet") or {}).get("template") or DEFAULT_TEMPLATE
    template = _load_template(template_id, warnings)
    return float(_resolve_sheet_scale(spec, template, warnings)["scale"])


def _point_xy(point, radius=0.0):
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    try:
        x, y = float(point[0]), float(point[1])
        r = float(radius or 0)
    except (TypeError, ValueError):
        return None
    return x - r, y - r, x + r, y + r


def _scale_cloud(spec: dict, *, text: bool):
    """Geometry (and, when `text`, callouts) as two lists of coordinates."""
    xs, ys = [], []

    def add(point, radius=0.0):
        box = _point_xy(point, radius)
        if box is None:
            return
        xs.extend((box[0], box[2]))
        ys.extend((box[1], box[3]))

    for el in spec.get("elements") or []:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "text" and not text:
            continue
        add(el.get("at"), el.get("radius") or 0)
        add(el.get("center"), el.get("radius") or 0)
        if el.get("type") != "text":
            add(el.get("label_at"))
        for point in list(el.get("points") or []) + list(el.get("corners") or []):
            add(point)
    for dim in spec.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        for point in dim.get("points") or []:
            add(point)
    return xs, ys


def _with_nearby_notes(spec: dict, xs: list, ys: list, scale: float, unit: float):
    """Add callouts and dimension-line offsets that sit next to the geometry at this scale."""
    xs, ys = list(xs), list(ys)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny, unit)
    margin = max(span * 0.5, 40.0 * scale * unit)

    def add(point):
        box = _point_xy(point)
        if box is None:
            return
        x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if minx - margin <= x <= maxx + margin and miny - margin <= y <= maxy + margin:
            xs.extend((box[0], box[2]))
            ys.extend((box[1], box[3]))

    for el in spec.get("elements") or []:
        if isinstance(el, dict) and el.get("type") == "text":
            add(el.get("at"))
    for dim in spec.get("dimensions") or []:
        if not isinstance(dim, dict):
            continue
        base = dim.get("base")
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            continue
        mid_x, mid_y = (minx + maxx) / 2, (miny + maxy) / 2
        if str(dim.get("axis")) == "v":
            add((base, mid_y))
        else:
            add((mid_x, base))
    return xs, ys


def _auto_sheet_denominator(spec: dict, template: dict) -> float:
    """Largest standard scale (smallest denominator) whose geometry fits the drawing area.

    A callout or a dimension-line offset far from the geometry does not zoom the sheet out.
    The measured points of a dimension do: they are the thing the viewport has to show.
    """
    from .layout import STANDARD_SCALES

    unit = 0.001 if spec.get("units") == "m" else 1.0
    area = template.get("drawing_area") or {"w": 380, "h": 240}
    free_w, free_h = float(area["w"]), float(area["h"])
    core_x, core_y = _scale_cloud(spec, text=False)
    if not core_x:
        core_x, core_y = _scale_cloud(spec, text=True)
    if not core_x or free_w <= 1 or free_h <= 1:
        return 50.0
    pad = 4.0
    for scale in STANDARD_SCALES:
        xs, ys = _with_nearby_notes(spec, core_x, core_y, float(scale), unit)
        model_w = (max(xs) - min(xs)) / unit
        model_h = (max(ys) - min(ys)) / unit
        if model_w / scale + pad <= free_w and model_h / scale + pad <= free_h:
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
    spec = _resolve_sheet_scale(spec, template, warnings)
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
    spec = _resolve_sheet_scale(spec, template, warnings)
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
