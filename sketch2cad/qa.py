"""Drawing QA controller: what a checker would reject before a sheet reaches the engineer.

Geometry is measured in paper millimetres (text height is already paper mm; model size is height × scale).
Standards and utility rules come from `QA_DEFAULTS`, overridden by `project.qa`, then `spec.qa` or
`spec.params.qa`. No network and no extra dependencies.

ISO 3098 text series: 2.5 / 3.5 / 5 / 7 mm. General notes and labels use 2.5 mm minimum.
1.8 mm is only for small annotations (dimensions, tables, title-block captions).
ISO 128 line weights: 0.18–0.70 mm, and the thick weight is at least twice the thin weight
(the standard pair 0.18 / 0.35 is accepted).
Utility submission (Israeli water corporations such as מי שמש): warnings, not errors.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .checks import Finding
from .drawing.context import DEFAULT_STYLE, DrawContext
from .drawing.text import LINE_SPACING, pick, text_width

QA_DEFAULTS = {
    "min_text_mm": 2.5,
    "min_annotation_mm": 1.8,
    "text_heights_mm": [2.5, 3.5, 5.0, 7.0],
    "annotation_heights_mm": [1.8],
    "lineweight_min_mm": 0.18,
    "lineweight_max_mm": 0.70,
    "thick_thin_ratio": 2.0,
    "dim_tolerance_mm": 1.0,
    "slope_tolerance_mm": 1.0,
    # smallest listed DN that is still >= the pipe, else the last row
    "min_slopes": [
        {"dn_mm": 150, "slope": 0.015},
        {"dn_mm": 200, "slope": 0.010},
        {"dn_mm": 250, "slope": 0.007},
    ],
    # office starting values for the SN cover hook, millimetres of cover. Not a quoted utility clause.
    "sn_cover_mm": {"SN2": 800, "SN4": 600, "SN8": 450, "SN16": 300},
    "utility_submission": True,
}

ANNOTATION_ROLES = {"dim", "table", "annotation", "title"}
# Sized to a symbol, not to the ISO text series.
SYMBOL_ROLES = {"balloon", "north"}
GENERAL_ROLES = {"label", "text", "notes", "legend"}
NTS_RE = re.compile(r"\bNTS\b|not\s*to\s*scale|לא בקנה", re.IGNORECASE)
INV_RE = re.compile(r"INV\s*[:=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
REF_RE = re.compile(r"(?:item:|#)([A-Za-z][\w.-]*)")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
ELEMENT_ERRORS = (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError, AttributeError)
DIM_GAP_MM = 0.6
OUTSIDE_TOL_MM = 1.5
OVERLAP_EPS_MM = 0.3
RATIO_SLACK = 0.06


def resolve_qa(spec: dict | None, project=None) -> dict:
    """Built-in defaults, then project.qa, then spec.qa, then spec.params.qa."""
    cfg = {key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
           for key, value in QA_DEFAULTS.items()}
    layers = []
    raw = project.get("qa") if isinstance(project, dict) else getattr(project, "qa", None)
    if isinstance(raw, dict):
        layers.append(raw)
    spec = spec or {}
    if isinstance(spec.get("qa"), dict):
        layers.append(spec["qa"])
    params = spec.get("params") or {}
    if isinstance(params.get("qa"), dict):
        layers.append(params["qa"])
    for over in layers:
        for key, value in over.items():
            if key in cfg and isinstance(cfg[key], dict) and isinstance(value, dict):
                cfg[key] = {**cfg[key], **value}
            else:
                cfg[key] = value
    return cfg


@dataclass
class TextBox:
    eid: str
    role: str
    x0: float
    y0: float
    x1: float
    y1: float
    height: float
    nlines: int = 1
    leaders: list = field(default_factory=list)
    in_notes: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Scene:
    boxes: list
    segments: list          # (x0, y0, x1, y1, layer) in paper mm
    points: list            # (element id, x, y) paper mm
    areas: dict
    paper: tuple
    doc: object
    k: float


def drawing_findings(spec: dict, catalog=None, project=None) -> list[Finding]:
    return review(spec, catalog, project).findings


@dataclass
class Review:
    settings: dict
    scene: Scene | None
    findings: list


def review(spec: dict, catalog=None, project=None) -> Review:
    cfg = resolve_qa(spec, project)
    findings = []
    findings += _hygiene(spec, catalog)
    findings += _dimensions(spec, cfg)
    findings += _engineering(spec, catalog, cfg)
    scene = None
    try:
        scene = build_scene(spec, catalog)
    except Exception as exc:  # noqa: BLE001 - a spec that cannot be laid out still keeps the other checks
        findings.append(Finding("warning", "qa_layout", f"Drawing QA layout failed: {exc}",
                                f"פריסת בקרת השרטוט נכשלה: {exc}"))
    else:
        findings += _geometry(scene, cfg)
        findings += _text_standards(scene, cfg)
        findings += _lineweights(scene, cfg)
        findings += _submission(scene.doc, cfg)
    return Review(cfg, scene, findings)


# ---------------------------------------------------------------- sheet model



def _coords(el) -> list:
    pts = []
    for key in ("at", "center", "label_at"):
        if isinstance(el.get(key), (list, tuple)) and len(el[key]) >= 2:
            pts.append(el[key])
    for key in ("points", "corners"):
        for pt in el.get(key) or []:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                pts.append(pt)
    return pts


def _text_boxes(spec, ctx, anchors, paper, language) -> list[TextBox]:
    from ezdxf.math import Vec2
    boxes = []
    style_h = float(ctx.style.get("text_height", DEFAULT_STYLE["text_height"]))
    for index, el in enumerate(spec.get("elements") or []):
        eid = el.get("id") or f"_{index}"
        if el.get("type") == "text":
            lines = pick(el.get("text"), language)
            if not lines or not el.get("at"):
                continue
            h = float(el.get("height") or style_h)
            at = Vec2(el["at"])
            leaders = []
            if el.get("leader_to"):
                leaders = _leader_segments(at, Vec2(el["leader_to"]), lines, h, ctx.k)
            boxes.append(_box(eid, "text", at, lines, h, el.get("align", "left"), paper, leaders, ctx.k))
            continue
        lines = pick(el.get("label"), language)
        if not lines or eid not in anchors:
            continue
        anchor, radius = anchors[eid]
        anchor = Vec2(anchor)
        h = float(el.get("label_height") or style_h)
        leaders = []
        if el.get("label_at"):
            at = Vec2(el["label_at"])
            align = el.get("label_align", "left")
            if el.get("leader") or el.get("leader_to"):
                target = Vec2(el["leader_to"]) if el.get("leader_to") else anchor
                leaders = _leader_segments(at, target, lines, h, ctx.k)
        else:
            n = len(lines)
            at = anchor + Vec2(0, radius + ctx.paper(1.5) + (n - 1) * LINE_SPACING * ctx.paper(h))
            align = "center"
        boxes.append(_box(eid, "label", at, lines, h, align, paper, leaders, ctx.k))
    return boxes


def _leader_segments(at, target, lines, h_mm, k) -> list:
    """Shelf and shaft in model space, matching leader_label."""
    if not lines:
        return []
    h = h_mm * k
    w = max(text_width(s, h) for s in lines)
    if target.y > at.y + h:
        y = at.y + h + 0.8 * k
    else:
        y = at.y - (len(lines) - 1) * LINE_SPACING * h - 0.8 * k
    shelf = ((at.x, y), (at.x + w, y))
    start = (at.x + w, y) if target.x > at.x + w / 2 else (at.x, y)
    shaft = (start, (target.x, target.y))
    return [shelf, shaft]


def _box(eid, role, at, lines, h_mm, align, paper, leaders, k) -> TextBox:
    width = max((text_width(s, h_mm) for s in lines), default=0.0)
    px, py = paper(at.x, at.y)
    top = py + h_mm
    bottom = py - (len(lines) - 1) * LINE_SPACING * h_mm - 0.25 * h_mm
    align = (align or "left").lower()
    if align in ("center", "middle", "middle_center"):
        x0, x1 = px - width / 2, px + width / 2
    elif align == "right":
        x0, x1 = px - width, px
    else:
        x0, x1 = px, px + width
    plead = []
    for (a, b) in leaders:
        plead.append((*paper(a[0], a[1]), *paper(b[0], b[1])))
    return TextBox(eid, role, x0, bottom, x1, top, h_mm, len(lines), plead)


def _dim_boxes(spec, ctx, paper) -> list[TextBox]:
    boxes = []
    h = float(ctx.style.get("dim_text_height", DEFAULT_STYLE["dim_text_height"]))
    units = spec.get("units", "mm")
    for i, dim in enumerate(spec.get("dimensions") or []):
        if dim.get("type", "chain") != "chain":
            continue
        pts = dim.get("points") or []
        axis = dim.get("axis", "h")
        base = float(dim.get("base", 0))
        texts = dim.get("texts") or []
        for j, (p1, p2) in enumerate(zip(pts, pts[1:])):
            measured = abs(p2[0] - p1[0]) if axis == "h" else abs(p2[1] - p1[1])
            if measured < 1e-9:
                continue
            raw = texts[j] if j < len(texts) else ""
            label = _dim_display(raw, measured, units)
            width = text_width(label or "0", h)
            if axis == "h":
                mx = (p1[0] + p2[0]) / 2
                px, py = paper(mx, base)
                py += DIM_GAP_MM
                x0, x1 = px - width / 2, px + width / 2
                y0, y1 = py - 0.25 * h, py + h
            else:
                my = (p1[1] + p2[1]) / 2
                px, py = paper(base, my)
                px += DIM_GAP_MM
                x0, x1 = px - 0.25 * h, px + h
                y0, y1 = py - width / 2, py + width / 2
            boxes.append(TextBox(f"dim:{i}:{j}", "dim", x0, y0, x1, y1, h, 1))
    return boxes


def _symbol_boxes(spec, ctx, paper) -> list[TextBox]:
    boxes = []
    r = float(ctx.style.get("balloon_radius", DEFAULT_STYLE["balloon_radius"]))
    h = 0.78 * r
    for entry in spec.get("legend") or []:
        for b in entry.get("balloons") or []:
            px, py = paper(b[0], b[1])
            boxes.append(TextBox(f"#{entry.get('no')}", "balloon", px - r, py - r, px + r, py + r, h, 1))
    for index, el in enumerate(spec.get("elements") or []):
        if el.get("type") != "north_arrow" or not el.get("at"):
            continue
        size = float(el.get("size", 14))
        nh = 1.6 * size / 5.5
        px, py = paper(el["at"][0], el["at"][1])
        py += (5.2 * size / 5.5)
        w = text_width("N", nh)
        boxes.append(TextBox(el.get("id") or f"north_{index}", "north",
                             px - w / 2, py - 0.25 * nh, px + w / 2, py + nh, nh, 1))
    return boxes


def _title_boxes(template, values, language) -> list[TextBox]:
    from .templates import _format, _pick_field
    boxes = []
    filled = {key: "" if v is None else str(v) for key, v in values.items()}
    fw = template["frame"]["w"]
    for field_def in template.get("fields") or []:
        h = float(field_def.get("h", 2.5))
        langs = _pick_field(field_def, language)
        texts = []
        for lang in langs:
            try:
                s = _format(field_def.get(lang) or "", filled)
            except Exception:  # noqa: BLE001
                s = ""
            if s:
                texts.append(s)
        if not texts:
            continue
        w = float(field_def.get("w", fw - field_def["x"] - 2))
        x = float(field_def["x"])
        y = float(field_def["y"])
        # Bilingual title with he_y is two lines; otherwise one line, possibly shared.
        nlines = 2 if len(texts) == 2 and "he_y" in field_def else 1
        bottom = y - (nlines - 1) * LINE_SPACING * h - 0.25 * h
        boxes.append(TextBox(f"title:{field_def.get('key', 'field')}", "title",
                             x, bottom, x + w, y + h, h, nlines, in_notes=True))
    return boxes


def _paper_segments(ctx, paper) -> list:
    segs = []
    for entity in ctx.msp:
        ents = [entity]
        if entity.dxftype() in ("INSERT", "DIMENSION"):
            try:
                ents = list(entity.virtual_entities())
            except Exception:  # noqa: BLE001
                ents = [entity]
        for item in ents:
            layer = item.dxf.layer if item.dxf.hasattr("layer") else ""
            kind = item.dxftype()
            if kind == "LINE":
                a, b = paper(item.dxf.start.x, item.dxf.start.y), paper(item.dxf.end.x, item.dxf.end.y)
                segs.append((*a, *b, layer))
            elif kind == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in item.get_points("xy")]
                pairs = list(zip(pts, pts[1:]))
                if item.closed and len(pts) > 2:
                    pairs.append((pts[-1], pts[0]))
                for a, b in pairs:
                    pa, pb = paper(*a), paper(*b)
                    segs.append((*pa, *pb, layer))
            elif kind == "CIRCLE":
                segs += _sample_arc(item.dxf.center, item.dxf.radius, 0, 360, paper, layer)
            elif kind == "ARC":
                segs += _sample_arc(item.dxf.center, item.dxf.radius, item.dxf.start_angle,
                                    item.dxf.end_angle, paper, layer)
    return segs


def _sample_arc(center, radius, a0, a1, paper, layer, n=16) -> list:
    if a1 <= a0:
        a1 += 360
    segs = []
    prev = None
    steps = max(4, int(n * (a1 - a0) / 360))
    for i in range(steps + 1):
        ang = math.radians(a0 + (a1 - a0) * i / steps)
        pt = paper(center.x + radius * math.cos(ang), center.y + radius * math.sin(ang))
        if prev is not None:
            segs.append((*prev, *pt, layer))
        prev = pt
    return segs




def _block_text_h(ctx, role: str) -> float:
    key = "table_text_height" if role == "legend" else "notes_text_height"
    return float(ctx.style.get(key, DEFAULT_STYLE[key]))


def _placed_box(block, at, role, paper_fn, k, legend_area, text_h: float) -> TextBox:
    x, top = paper_fn(at.x, at.y)
    w, h = block.width / k, block.height / k
    # The legend table is small annotation text; notes are general text.
    text_role = "table" if role == "legend" else "notes"
    box = TextBox(role, text_role, x, top - h, x + w, top, text_h, 1, in_notes=True)
    if legend_area and _rect_inside(box, legend_area, 2.0):
        box.in_notes = True
    return box


def _dim_display(raw, measured, units) -> str:
    if units == "m":
        shown = f"{measured:.2f}"
    else:
        shown = f"{measured:.0f}" if abs(measured - round(measured)) < 1e-6 else f"{measured:.1f}"
    text = raw or ""
    if not text or text == "<>":
        return shown
    if "<>" in text:
        return text.replace("<>", shown)
    return text


# ---------------------------------------------------------------- spec hygiene and numbers
def _hygiene(spec, catalog) -> list[Finding]:
    out = []
    elements = [e for e in (spec.get("elements") or []) if isinstance(e, dict)]
    seen: dict[str, int] = {}
    for el in elements:
        eid = str(el.get("id") or "")
        if not eid:
            continue
        seen[eid] = seen.get(eid, 0) + 1
    for eid, count in sorted(seen.items()):
        if count > 1:
            out.append(Finding("error", "duplicate_id", f"Id '{eid}' is used {count} times",
                               f"המזהה '{eid}' מופיע {count} פעמים", eid))
    nos: dict[str, int] = {}
    for entry in spec.get("legend") or []:
        if not isinstance(entry, dict) or entry.get("no") is None:
            continue
        key = str(entry.get("no"))
        nos[key] = nos.get(key, 0) + 1
    for key, count in sorted(nos.items()):
        if count > 1:
            out.append(Finding("error", "duplicate_id", f"Legend number {key} is used {count} times",
                               f"מספר מקרא {key} מופיע {count} פעמים", f"#{key}"))
    known = {str(el.get("id")) for el in elements if el.get("id")}
    for el in elements:
        eid = str(el.get("id") or "")
        out += _unknown_refs(_blob(el.get("label")) + " " + _blob(el.get("text")), eid, known, catalog)
        item = (el.get("label") or {}).get("item") if isinstance(el.get("label"), dict) else None
        if item:
            out += _unknown_item_ref(str(item), eid, known, catalog)
    for entry in spec.get("legend") or []:
        if isinstance(entry, dict):
            out += _unknown_refs(_blob(entry), f"#{entry.get('no')}", known, catalog)
    notes = spec.get("notes") or {}
    for lang in ("en", "he"):
        for i, line in enumerate(notes.get(lang) or []):
            out += _unknown_refs(str(line), f"note:{lang}:{i + 1}", known, catalog)
    return out


def _blob(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(str(v) for v in value.values() if isinstance(v, str))
    return ""


def _unknown_refs(text, eid, known, catalog) -> list[Finding]:
    out = []
    for token in REF_RE.findall(text or ""):
        out += _unknown_item_ref(token, eid, known, catalog)
    return out


def _unknown_item_ref(token, eid, known, catalog) -> list[Finding]:
    if token in known:
        return []
    if catalog is not None and catalog.get(token):
        return []
    return [Finding("error", "unknown_ref", f"'{eid}' references unknown item '{token}'",
                    f"'{eid}' מפנה לפריט לא מוכר '{token}'", eid)]


def _dimensions(spec, cfg) -> list[Finding]:
    out = []
    view = str(spec.get("view") or "")
    nts = bool(NTS_RE.search(view))
    units = spec.get("units", "mm")
    tol = _du(cfg["dim_tolerance_mm"], units)
    seen = {}
    for i, dim in enumerate(spec.get("dimensions") or []):
        if not isinstance(dim, dict) or dim.get("type", "chain") != "chain":
            continue
        pts = dim.get("points") or []
        axis = dim.get("axis", "h")
        texts = dim.get("texts") or []
        for j, (p1, p2) in enumerate(zip(pts, pts[1:])):
            eid = f"dim:{i}:{j}"
            measured = abs((p2[0] - p1[0]) if axis == "h" else (p2[1] - p1[1]))
            key = (axis, _q(p1[0]), _q(p1[1]), _q(p2[0]), _q(p2[1]))
            key_rev = (axis, _q(p2[0]), _q(p2[1]), _q(p1[0]), _q(p1[1]))
            prev = seen.get(key) or seen.get(key_rev)
            if prev:
                out.append(Finding("warning", "dim_duplicate",
                                   f"Dimension {eid} repeats {prev}",
                                   f"מידה {eid} חוזרת על {prev}", f"{prev},{eid}"))
            else:
                seen[key] = eid
            if measured <= tol / 10 or measured < 1e-9:
                out.append(Finding("error", "dim_zero", f"Dimension {eid} has zero length",
                                   f"מידה {eid} באורך אפס", eid))
                continue
            raw = texts[j] if j < len(texts) else ""
            if nts or not raw or "<>" in str(raw):
                continue
            nums = _numbers(str(raw))
            if not nums:
                continue
            limit = max(tol, 0.002 * measured)
            if all(abs(n - measured) > limit for n in nums):
                shown = ", ".join(f"{n:g}" for n in nums)
                out.append(Finding(
                    "error", "dim_override",
                    f"Dimension {eid} text {shown} overrides the measured {_fmt_meas(measured, units)}",
                    f"הטקסט של מידה {eid} ({shown}) דורס את הערך המדוד {_fmt_meas(measured, units)}",
                    eid))
    return out


def _numbers(text: str) -> list[float]:
    text = re.sub(r"(\d),(\d)", r"\1.\2", text)
    return [float(n) for n in NUMBER_RE.findall(text)]


def _fmt_meas(value, units) -> str:
    return f"{value:.3f} m" if units == "m" else f"{value:.0f} mm"


def _q(value) -> float:
    return round(float(value), 3)


def _du(mm, units) -> float:
    return float(mm) * (0.001 if units == "m" else 1.0)


def _as_ratio(slope) -> float:
    """Dimensionless fall/run. A value above 0.5 is read as a percent (1.5 -> 1.5%)."""
    slope = float(slope)
    return slope / 100.0 if abs(slope) > 0.5 else slope


def _engineering(spec, catalog, cfg) -> list[Finding]:
    out = []
    units = spec.get("units", "mm")
    tol = _du(cfg["slope_tolerance_mm"], units)
    elements = [e for e in (spec.get("elements") or []) if isinstance(e, dict)]
    inv_texts = _inv_texts(elements)
    bands = cfg.get("min_slopes") or []
    for el in elements:
        if el.get("type") != "pipe":
            continue
        eid = el.get("id") or "pipe"
        length = _length(el.get("points") or [])
        slope = el.get("slope")
        inv = _pipe_inv(el, inv_texts, units)
        if slope is not None and inv is not None and length > 0:
            expected = _as_ratio(slope) * length
            actual = inv[0] - inv[1]
            if abs(actual - expected) > tol:
                out.append(Finding(
                    "error", "slope_mismatch",
                    f"Pipe {eid}: fall {actual:.4g} differs from slope × length {expected:.4g}",
                    f"צינור {eid}: הפרש רומים {actual:.4g} אינו שווה לשיפוע × אורך ({expected:.4g})",
                    eid))
        if slope is not None or inv is not None:
            ratio = _as_ratio(slope) if slope is not None else (abs(inv[0] - inv[1]) / length if length else None)
            dn = _dn_mm(el, catalog, units)
            need = _min_slope(dn, bands) if dn and ratio is not None else None
            if need is not None and ratio + 1e-6 < need:
                out.append(Finding(
                    "warning", "min_slope",
                    f"Pipe {eid}: slope {ratio * 100:.2f}% is below {need * 100:.2f}% for DN {dn:.0f}",
                    f"צינור {eid}: שיפוע {ratio * 100:.2f}% קטן מ-{need * 100:.2f}% לקוטר {dn:.0f}",
                    eid))
        out += _sn_cover(el, spec, catalog, cfg)
    for el in elements:
        if el.get("type") == "connector" and el.get("offset") is not None:
            out += _connector(el, elements, units, cfg)
    return out


def _inv_texts(elements) -> list[dict]:
    found = []
    for el in elements:
        blob = _blob(el.get("text")) + " " + _blob(el.get("label"))
        match = INV_RE.search(re.sub(r"(\d),(\d)", r"\1.\2", blob))
        if not match:
            continue
        at = el.get("at") or el.get("label_at")
        found.append({"pipe": el.get("pipe"), "value": float(match.group(1)), "at": at, "id": el.get("id")})
    return found


def _pipe_inv(el, texts, units):
    inv = el.get("inv")
    if isinstance(inv, (list, tuple)) and len(inv) >= 2:
        return float(inv[0]), float(inv[1])
    eid = el.get("id")
    pts = el.get("points") or []
    if len(pts) < 2:
        return None
    own = [t for t in texts if t.get("pipe") == eid]
    pool = own or texts
    if len(pool) < 2:
        return None
    start, end = pts[0], pts[-1]

    def dist(text, pt):
        if not text.get("at"):
            return math.inf
        return math.dist(text["at"], pt)

    near_start = min(pool, key=lambda t: dist(t, start))
    near_end = min(pool, key=lambda t: dist(t, end))
    if near_start is near_end:
        return None
    # Unlinked texts must actually sit on the ends, otherwise they belong to another pipe.
    limit = _du(500, units)
    if not own and (dist(near_start, start) > limit or dist(near_end, end) > limit):
        return None
    return near_start["value"], near_end["value"]


def _length(points) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.dist(a, b)
    return total


def _dn_mm(el, catalog, units) -> float | None:
    if el.get("dn") is not None:
        dn = float(el["dn"])
        return dn * 1000 if units == "m" and dn < 20 else dn
    item = catalog.get(el.get("item")) if catalog and el.get("item") else None
    if item and isinstance(item.get("dn"), (int, float)):
        return float(item["dn"])
    if el.get("od") is not None:
        od = float(el["od"])
        return od * 1000 if units == "m" else od
    if item:
        od = (item.get("dims") or {}).get("OD")
        if isinstance(od, (int, float)):
            return float(od)
    return None


def _min_slope(dn_mm, bands) -> float | None:
    rows = []
    for row in bands or []:
        try:
            rows.append((float(row["dn_mm"]), _as_ratio(row["slope"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    rows.sort()
    for dn, slope in rows:
        if dn_mm <= dn + 1e-6:
            return slope
    return rows[-1][1]


def _sn_cover(el, spec, catalog, cfg) -> list[Finding]:
    item = catalog.get(el.get("item")) if catalog and el.get("item") else None
    sn = el.get("sn") or (item or {}).get("sn") or ((item or {}).get("dims") or {}).get("SN")
    if not sn:
        return []
    table = {str(k).upper(): v for k, v in (cfg.get("sn_cover_mm") or {}).items()}
    need = table.get(str(sn).upper())
    if need is None:
        return []
    cover = _cover_mm(el, spec, item)
    if cover is None:
        return []
    if cover + 1 < float(need):
        eid = el.get("id") or "pipe"
        return [Finding(
            "warning", "sn_cover",
            f"Pipe {eid}: cover {cover:.0f} mm is below {need:.0f} mm for {sn}",
            f"צינור {eid}: כיסוי {cover:.0f} מ\"מ קטן מ-{need:.0f} מ\"מ לדרג {sn}",
            eid)]
    return []


def _cover_mm(el, spec, item) -> float | None:
    units = spec.get("units", "mm")
    if el.get("cover") is not None:
        return float(el["cover"]) * (1000 if units == "m" else 1)
    view = str(spec.get("view") or "").lower()
    if not any(w in view for w in ("elevation", "section", "חתך")):
        return None
    params = spec.get("params") or {}
    if "grade_y" not in params:
        return None
    pts = el.get("points") or []
    runs = [(a, b) for a, b in zip(pts, pts[1:]) if abs(b[0] - a[0]) >= abs(b[1] - a[1])]
    if not runs:
        return None
    od = el.get("od")
    if od is None and item:
        od = (item.get("dims") or {}).get("OD")
        if isinstance(od, (int, float)) and units == "m":
            od = float(od) / 1000
    od = float(od or 0)
    crown = max(max(a[1], b[1]) for a, b in runs) + od / 2
    return (float(params["grade_y"]) - crown) * (1000 if units == "m" else 1)


def _connector(el, elements, units, cfg) -> list[Finding]:
    if not el.get("at"):
        return []
    ref = el.get("offset_from", [0, 0])
    if isinstance(ref, str):
        other = next((e for e in elements if e.get("id") == ref), None)
        if not other:
            return [Finding("error", "connector_offset",
                            f"Connector {el.get('id', '')}: offset reference '{ref}' was not found",
                            f"מחבר {el.get('id', '')}: ייחוס ההיסט '{ref}' לא נמצא", el.get("id") or "")]
        ref = other.get("at") or ((other.get("points") or [None])[0]) or other.get("center")
    if not isinstance(ref, (list, tuple)) or len(ref) < 2:
        return []
    at = el["at"]
    axis = el.get("offset_axis")
    if axis == "x":
        actual = float(at[0]) - float(ref[0])
    elif axis == "y":
        actual = float(at[1]) - float(ref[1])
    else:
        actual = math.dist(at, ref)
    declared = float(el["offset"])
    tol = _du(cfg["dim_tolerance_mm"], units)
    if abs(actual - declared) > tol:
        eid = el.get("id") or "connector"
        return [Finding("error", "connector_offset",
                        f"Connector {eid}: declared offset {declared:g} but geometry is {actual:.4g}",
                        f"מחבר {eid}: היסט מוצהר {declared:g} והגיאומטריה היא {actual:.4g}", eid)]
    return []


# ---------------------------------------------------------------- paper checks
def _geometry(scene: Scene, cfg) -> list[Finding]:
    out = []
    boxes = scene.boxes
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            if a.role == "title" and b.role == "title":
                continue
            if a.eid == b.eid and a.role == b.role:
                continue
            if _overlap(a, b):
                out.append(Finding(
                    "error", "text_overlap",
                    f"Text '{a.eid}' overlaps '{b.eid}'",
                    f"הטקסט '{a.eid}' חופף את '{b.eid}'",
                    f"{a.eid},{b.eid}"))
    for box in boxes:
        if box.role not in ("label", "text"):
            continue
        for seg in scene.segments:
            if seg[4] in ("FRAME", "HATCH"):
                continue
            if _own_leader(seg, box):
                continue
            if _seg_hits(seg, box):
                out.append(Finding(
                    "error", "text_over_line",
                    f"Text '{box.eid}' crosses linework",
                    f"הטקסט '{box.eid}' חוצה קווי שרטוט",
                    box.eid))
                break
    drawing = scene.areas.get("drawing")
    legend = scene.areas.get("legend")
    blocks = scene.areas.get("blocks") or []
    flagged_out, flagged_title = set(), set()
    for box in boxes:
        if box.role == "title" or box.in_notes:
            continue
        if drawing and not _rect_inside(box, drawing, OUTSIDE_TOL_MM) and not (
                legend and _rect_inside(box, legend, OUTSIDE_TOL_MM)):
            if box.eid not in flagged_out:
                flagged_out.add(box.eid)
                out.append(Finding("error", "outside_sheet",
                                   f"'{box.eid}' is outside the usable drawing area",
                                   f"'{box.eid}' מחוץ לשטח השרטוט השימושי", box.eid))
        if _hits_title(box, blocks, legend):
            if box.eid not in flagged_title:
                flagged_title.add(box.eid)
                out.append(Finding("error", "in_titleblock",
                                   f"'{box.eid}' overlaps the title block or notes area",
                                   f"'{box.eid}' חופף את כותרת הגיליון או אזור ההערות", box.eid))
    for eid, x, y in scene.points:
        if eid in flagged_out:
            continue
        if drawing and not _pt_inside(x, y, drawing, OUTSIDE_TOL_MM) and not (
                legend and _pt_inside(x, y, legend, OUTSIDE_TOL_MM)):
            flagged_out.add(eid)
            out.append(Finding("error", "outside_sheet",
                               f"'{eid}' is outside the usable drawing area",
                               f"'{eid}' מחוץ לשטח השרטוט השימושי", eid))
        if _pt_hits_title(x, y, blocks, legend) and eid not in flagged_title:
            flagged_title.add(eid)
            out.append(Finding("error", "in_titleblock",
                               f"'{eid}' overlaps the title block or notes area",
                               f"'{eid}' חופף את כותרת הגיליון או אזור ההערות", eid))
    return out


def _overlap(a: TextBox, b: TextBox) -> bool:
    ix = min(a.x1, b.x1) - max(a.x0, b.x0)
    iy = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ix > OVERLAP_EPS_MM and iy > OVERLAP_EPS_MM


def _own_leader(seg, box: TextBox) -> bool:
    mx, my = (seg[0] + seg[2]) / 2, (seg[1] + seg[3]) / 2
    for lx0, ly0, lx1, ly1 in box.leaders:
        if _dist_point_seg((seg[0], seg[1]), (lx0, ly0, lx1, ly1)) < 1.0 and \
           _dist_point_seg((seg[2], seg[3]), (lx0, ly0, lx1, ly1)) < 1.0 and \
           _dist_point_seg((mx, my), (lx0, ly0, lx1, ly1)) < 0.8:
            return True
    return False


def _dist_point_seg(p, seg) -> float:
    x, y = p
    x0, y0, x1, y1 = seg
    dx, dy = x1 - x0, y1 - y0
    length = dx * dx + dy * dy
    if length < 1e-12:
        return math.hypot(x - x0, y - y0)
    t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / length))
    return math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))


def _seg_hits(seg, box: TextBox) -> bool:
    x0, y0, x1, y1 = seg[:4]
    # Shrink so a line that only grazes the text border is not a clash.
    a, b = box.x0 + OVERLAP_EPS_MM, box.y0 + OVERLAP_EPS_MM
    c, d = box.x1 - OVERLAP_EPS_MM, box.y1 - OVERLAP_EPS_MM
    if c <= a or d <= b:
        return False

    def inside(x, y):
        return a <= x <= c and b <= y <= d

    if inside(x0, y0) or inside(x1, y1):
        return True

    def crosses(ax, ay, bx, by, cx, cy, dx, dy):
        def orient(px, py, qx, qy, rx, ry):
            return (qy - py) * (rx - qx) - (qx - px) * (ry - qy)
        return orient(ax, ay, bx, by, cx, cy) * orient(ax, ay, bx, by, dx, dy) < 0 and \
            orient(cx, cy, dx, dy, ax, ay) * orient(cx, cy, dx, dy, bx, by) < 0

    return crosses(x0, y0, x1, y1, a, b, c, b) or crosses(x0, y0, x1, y1, c, b, c, d) or \
        crosses(x0, y0, x1, y1, c, d, a, d) or crosses(x0, y0, x1, y1, a, d, a, b)


def _rect_inside(box: TextBox, area: dict, tol: float) -> bool:
    return box.x0 >= area["x"] - tol and box.y0 >= area["y"] - tol and \
        box.x1 <= area["x"] + area["w"] + tol and box.y1 <= area["y"] + area["h"] + tol


def _pt_inside(x, y, area, tol) -> bool:
    return area["x"] - tol <= x <= area["x"] + area["w"] + tol and \
        area["y"] - tol <= y <= area["y"] + area["h"] + tol


def _hits_title(box: TextBox, blocks, legend) -> bool:
    if box.in_notes and legend and _rect_inside(box, legend, 1.0):
        return False
    for block in blocks:
        if _overlap_area(box, block) and not (legend and _center_in(box, legend)):
            return True
    return False


def _pt_hits_title(x, y, blocks, legend) -> bool:
    if legend and _pt_inside(x, y, legend, 0.5):
        return False
    for block in blocks:
        if block["x"] <= x <= block["x"] + block["w"] and block["y"] <= y <= block["y"] + block["h"]:
            return True
    return False


def _center_in(box: TextBox, area) -> bool:
    return _pt_inside(box.cx, box.cy, area, 0.0)


def _overlap_area(box: TextBox, block: dict) -> bool:
    ix = min(box.x1, block["x"] + block["w"]) - max(box.x0, block["x"])
    iy = min(box.y1, block["y"] + block["h"]) - max(box.y0, block["y"])
    return ix > OVERLAP_EPS_MM and iy > OVERLAP_EPS_MM


def _text_standards(scene: Scene, cfg) -> list[Finding]:
    out = []
    off: dict[str, set] = {}
    heights: dict[str, set] = {}
    for box in scene.boxes:
        if box.height <= 0:
            continue
        if box.role in SYMBOL_ROLES:
            if box.height + 1e-6 < float(cfg["min_annotation_mm"]):
                out.append(_too_small(box, cfg["min_annotation_mm"]))
            continue
        annotation = box.role in ANNOTATION_ROLES
        limit = float(cfg["min_annotation_mm"] if annotation else cfg["min_text_mm"])
        if box.height + 1e-6 < limit:
            out.append(_too_small(box, limit))
            continue
        allowed = [float(v) for v in cfg.get("text_heights_mm") or []]
        if annotation:
            allowed += [float(v) for v in cfg.get("annotation_heights_mm") or []]
        if not any(abs(box.height - item) <= 0.05 for item in allowed):
            off.setdefault(box.role, set()).add(round(box.height, 2))
        if box.role not in {"title"} | SYMBOL_ROLES:
            heights.setdefault(box.role, set()).add(round(box.height, 2))
    for role, values in sorted(off.items()):
        listed = ", ".join(f"{v:g}" for v in sorted(values))
        series = "/".join(f"{float(v):g}" for v in cfg.get("text_heights_mm") or [])
        out.append(Finding(
            "warning", "text_height_standard",
            f"{role} text height {listed} mm is outside the ISO 3098 series {series}",
            f"גובה טקסט ({role}) {listed} מ\"מ אינו בסדרת ISO 3098 ‏{series}",
            role))
    for role, values in sorted(heights.items()):
        if len(values) > 1:
            listed = ", ".join(f"{v:g}" for v in sorted(values))
            out.append(Finding(
                "warning", "text_height_inconsistent",
                f"Role '{role}' uses more than one text height: {listed} mm",
                f"לתפקיד '{role}' יותר מגובה טקסט אחד: {listed} מ\"מ",
                role))
    return out


def _too_small(box: TextBox, limit) -> Finding:
    return Finding("error", "text_too_small",
                   f"'{box.eid}' is {box.height:g} mm on paper, below {float(limit):g} mm",
                   f"'{box.eid}' בגובה {box.height:g} מ\"מ על הנייר, מתחת ל-{float(limit):g} מ\"מ",
                   box.eid)


def _lineweights(scene: Scene, cfg) -> list[Finding]:
    lo, hi = float(cfg["lineweight_min_mm"]), float(cfg["lineweight_max_mm"])
    used = []
    for layer in scene.doc.layers:
        weight = _layer_mm(layer)
        if weight is not None:
            used.append((f"layer {layer.dxf.name}", weight))
    for entity in scene.doc.modelspace():
        weight = _entity_mm(entity)
        if weight is not None:
            used.append((entity.dxf.layer, weight))
    outside = [(name, w) for name, w in used if w < lo - 1e-6 or w > hi + 1e-6]
    out = []
    if outside:
        # One finding: list distinct weights, not every entity.
        distinct = sorted({(round(w, 2), name) for name, w in outside})
        shown = ", ".join(f"{name} {w:.2f}" for w, name in distinct[:8])
        out.append(Finding(
            "warning", "lineweight_range",
            f"Lineweights outside ISO 128 {lo:.2f}–{hi:.2f} mm: {shown}",
            f"עובי קו מחוץ ל-ISO 128 ‏{lo:.2f}–{hi:.2f} מ\"מ: {shown}",
            ""))
    in_range = [w for _, w in used if lo - 1e-6 <= w <= hi + 1e-6]
    if in_range:
        thin, thick = min(in_range), max(in_range)
        ratio = float(cfg.get("thick_thin_ratio") or 2)
        if thick > thin + 1e-6 and thick + 1e-6 < thin * (ratio - RATIO_SLACK):
            out.append(Finding(
                "warning", "lineweight_ratio",
                f"Thick line {thick:.2f} mm is less than {ratio:g}× the thin line {thin:.2f} mm",
                f"הקו העבה {thick:.2f} מ\"מ קטן מ-{ratio:g}× הקו הדק {thin:.2f} מ\"מ",
                ""))
    return out


def _layer_mm(layer):
    if not layer.dxf.hasattr("lineweight"):
        return None
    weight = layer.dxf.lineweight
    if weight is None or weight < 0:
        return None
    return weight / 100.0


def _entity_mm(entity):
    if not entity.dxf.hasattr("lineweight"):
        return None
    weight = entity.dxf.lineweight
    if weight is None or weight < 0:
        return None
    return weight / 100.0


def _submission(doc, cfg) -> list[Finding]:
    if not cfg.get("utility_submission", True):
        return []
    return submission_from_doc(doc)


def submission_from_doc(doc) -> list[Finding]:
    """Utility-submission warnings for one DXF document (model space and block definitions)."""
    out = []
    layer0, defpoints, explicit = [], [], []
    for entity in doc.modelspace():
        layer = (entity.dxf.layer or "").upper()
        if layer in ("0", "LAYER0"):
            layer0.append(entity.dxftype())
        elif layer == "DEFPOINTS":
            defpoints.append(entity.dxftype())
        if _explicit_props(entity):
            explicit.append(entity.dxf.layer)
    if layer0:
        out.append(Finding("warning", "layer_0",
                           f"Layer 0 has {len(layer0)} entities; a utility submission leaves it empty",
                           f"שכבה 0 מכילה {len(layer0)} ישויות; בהגשה לתאגיד היא נשארת ריקה", "0"))
    if defpoints:
        out.append(Finding("warning", "defpoints",
                           f"DEFPOINTS has {len(defpoints)} entities; a utility submission leaves it empty",
                           f"שכבת DEFPOINTS מכילה {len(defpoints)} ישויות; בהגשה לתאגיד היא נשארת ריקה",
                           "DEFPOINTS"))
    if explicit:
        out.append(Finding(
            "warning", "not_bylayer",
            f"{len(explicit)} entities set colour, linetype or lineweight (utility drawings use BYLAYER)",
            f"{len(explicit)} ישויות קובעות צבע, סוג קו או עובי (בהגשה לתאגיד: BYLAYER)",
            ""))
    bad_blocks = []
    for block in doc.blocks:
        name = block.name
        if name.startswith("*"):
            continue
        for entity in block:
            layer = entity.dxf.layer if entity.dxf.hasattr("layer") else "0"
            if layer not in ("0", "BYBLOCK"):
                bad_blocks.append(name)
                break
    if bad_blocks:
        listed = ", ".join(bad_blocks[:6])
        out.append(Finding("warning", "block_layer",
                           f"Block definitions not drawn on layer 0: {listed}",
                           f"הגדרות בלוק שאינן בשכבה 0: {listed}", listed))
    return out


def _explicit_props(entity) -> bool:
    if entity.dxf.hasattr("color") and entity.dxf.color not in (256, 0):
        return True
    if entity.dxf.hasattr("linetype") and str(entity.dxf.linetype).upper() not in ("BYLAYER", "BYBLOCK"):
        return True
    if entity.dxf.hasattr("lineweight") and entity.dxf.lineweight is not None and entity.dxf.lineweight >= 0:
        return True
    return False


# ---------------------------------------------------------------- overlay
def write_overlay(spec: dict, path, catalog=None, project=None) -> str | None:
    """Circle QA problems in red on a paper-space sheet. Returns the PNG path."""
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Rectangle

    result = review(spec, catalog, project)
    scene = result.scene
    if scene is None:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pw, ph = scene.paper
    fig, ax = plt.subplots(figsize=(pw / 25.4, ph / 25.4), dpi=80)
    ax.set_xlim(-5, pw + 5)
    ax.set_ylim(-5, ph + 5)
    ax.set_aspect("equal")
    ax.add_patch(Rectangle((0, 0), pw, ph, fill=False, ec="#666666", lw=0.6))
    area = scene.areas.get("drawing")
    if area:
        ax.add_patch(Rectangle((area["x"], area["y"]), area["w"], area["h"], fill=False, ec="#bbbbbb", lw=0.4))
    for seg in scene.segments:
        if seg[4] == "FRAME":
            continue
        ax.plot([seg[0], seg[2]], [seg[1], seg[3]], color="#335577", lw=0.35, solid_capstyle="round")
    for box in scene.boxes:
        ax.add_patch(Rectangle((box.x0, box.y0), max(0.2, box.x1 - box.x0), max(0.2, box.y1 - box.y0),
                               fill=False, ec="#3d7ea6", lw=0.35))
    sheet_y = ph - 8
    for finding in result.findings:
        targets = _targets(finding, scene)
        if targets:
            for cx, cy, radius in targets:
                ax.add_patch(Circle((cx, cy), radius, fill=False, ec="#d00000", lw=1.1))
                ax.text(cx, cy + radius + 0.6, finding.code, color="#d00000", fontsize=6, ha="center", va="bottom")
        else:
            ax.add_patch(Circle((12, sheet_y), 4.5, fill=False, ec="#d00000", lw=1.0))
            ax.text(18, sheet_y, finding.code, color="#d00000", fontsize=6, va="center")
            sheet_y -= 8
    ax.axis("off")
    fig.savefig(path, dpi=100, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


def _targets(finding: Finding, scene: Scene) -> list[tuple[float, float, float]]:
    ids = [part for part in re.split(r"[,+]", finding.element or "") if part]
    boxes = [b for b in scene.boxes if b.eid in ids or any(b.eid.startswith(i) for i in ids)]
    if not boxes:
        pts = [(x, y) for eid, x, y in scene.points if eid in ids]
        if not pts:
            return []
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        return [(cx, cy, 8.0)]
    out = []
    # One circle around the group so a pair of overlapping texts is marked once.
    x0, y0 = min(b.x0 for b in boxes), min(b.y0 for b in boxes)
    x1, y1 = max(b.x1 for b in boxes), max(b.y1 for b in boxes)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = max(6.0, math.hypot(x1 - x0, y1 - y0) / 2 + 2.0)
    out.append((cx, cy, radius))
    return out




def build_scene(spec: dict, catalog=None) -> Scene:
    """Paper-space text boxes and linework, on the same sheet origin as the renderer."""
    from ezdxf.math import Vec2

    from . import templates
    from .drawing import components, dims, layout, legend

    language = spec.get("language") or "both"
    warnings: list[str] = []
    sheet = spec.get("sheet") or {}
    try:
        template = templates.load_template(sheet.get("template") or "a3_simple")
    except KeyError:
        template = templates.load_template("a3_simple")
    ctx = DrawContext(spec, catalog, language, warnings)
    anchors: dict[str, tuple] = {}
    for index, el in enumerate(spec.get("elements") or []):
        drawer = components.DRAWERS.get(el.get("type"))
        if drawer is None:
            continue
        try:
            anchor, radius = drawer(ctx, el)
        except ELEMENT_ERRORS:
            continue
        anchors[el.get("id") or f"_{index}"] = (Vec2(anchor), float(radius))
        if el.get("type") != "text":
            try:
                components.draw_label(ctx, el, anchor, radius)
            except ELEMENT_ERRORS:
                pass
    try:
        dims.draw_dimensions(ctx, spec.get("dimensions") or [])
    except ELEMENT_ERRORS:
        pass
    try:
        legend.draw_balloons(ctx, spec.get("legend") or [])
    except ELEMENT_ERRORS:
        pass

    la = template.get("legend_area")
    max_w = la["w"] if la else None
    table = legend.legend_table(ctx, spec.get("legend") or [], max_w)
    notes = legend.notes_block(ctx, spec.get("notes"), max_w)
    side = []
    side_roles = []
    explicit = []
    if table is not None:
        if spec.get("legend_at"):
            table.draw(Vec2(spec["legend_at"]))
            explicit.append((table, Vec2(spec["legend_at"]), "legend"))
        else:
            side.append(table)
            side_roles.append("legend")
    if notes is not None:
        if spec.get("notes_at"):
            notes.draw(Vec2(spec["notes_at"]))
            explicit.append((notes, Vec2(spec["notes_at"]), "notes"))
        else:
            side.append(notes)
            side_roles.append("notes")
    origin, placements = layout.plan_sheet(ctx, template, side, sheet.get("origin"))
    for block, top_left in placements:
        block.draw(top_left)
    values = templates.build_values(spec, None, None)
    templates.draw_titleblock(ctx.msp, template, values, ctx.scale, ctx.units, origin=origin,
                              language=language)
    k = ctx.k or 1.0
    ox, oy = float(origin.x), float(origin.y)

    def paper(x, y) -> tuple[float, float]:
        return (float(x) - ox) / k, (float(y) - oy) / k

    boxes = _text_boxes(spec, ctx, anchors, paper, language)
    boxes += _dim_boxes(spec, ctx, paper)
    boxes += _symbol_boxes(spec, ctx, paper)
    for block, at, role in explicit:
        boxes.append(_placed_box(block, at, role, paper, k, la, _block_text_h(ctx, role)))
    for (block, at), role in zip(placements, side_roles):
        boxes.append(_placed_box(block, at, role, paper, k, la, _block_text_h(ctx, role)))
    boxes += _title_boxes(template, values, language)
    segments = _paper_segments(ctx, paper)
    # Centre-lines are what a label sitting in a pipe barrel crosses, even when the walls are drawn apart.
    for el in spec.get("elements") or []:
        if el.get("type") not in ("pipe", "polyline", "trench_drain"):
            continue
        pts = [pt for pt in (el.get("points") or []) if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        for a, b in zip(pts, pts[1:]):
            segments.append((*paper(a[0], a[1]), *paper(b[0], b[1]), "MODEL"))
    points = []
    for el in spec.get("elements") or []:
        eid = el.get("id") or el.get("type") or "?"
        for pt in _coords(el):
            points.append((str(eid), *paper(pt[0], pt[1])))
    for entry in spec.get("legend") or []:
        if not isinstance(entry, dict):
            continue
        for pt in (entry.get("balloons") or []) + (entry.get("targets") or []):
            points.append((f"#{entry.get('no')}", *paper(pt[0], pt[1])))
    areas = {
        "frame": {"x": 0.0, "y": 0.0, "w": float(template["frame"]["w"]), "h": float(template["frame"]["h"])},
        "drawing": template.get("drawing_area"),
        "legend": template.get("legend_area"),
        "blocks": list(template.get("blocks") or []),
    }
    return Scene(boxes, segments, points, areas,
                 (float(template["paper"]["w"]), float(template["paper"]["h"])), ctx.doc, k)
