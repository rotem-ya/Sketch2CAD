"""Drawing QA controller: what a checker would reject before a sheet reaches the engineer.

Geometry is measured in paper millimetres (text height is already paper mm; model size is height × scale).
Standards and utility rules come from `QA_DEFAULTS`, overridden by `project.qa`, then `spec.qa` or
`spec.params.qa`. No network and no extra dependencies.

ISO 3098 text series: 2.5 / 3.5 / 5 / 7 mm. Labels use 2.5 mm minimum.
1.8 mm is for small annotations (dimensions, tables, title-block captions). Notes stay at the
engine default until drawing/standards.py owns the heights: under 1.8 mm is an error, and a
notes height outside the series is a warning, so a sheet the tool itself draws is not blocked.
ISO 128 line weights: 0.18–0.70 mm, and the thick weight is at least twice the thin weight
(the standard pair 0.18 / 0.35 is accepted).
Utility submission (Israeli water corporations such as מי שמש): warnings, not errors.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .checks import Finding
from .drawing.text import LINE_SPACING, has_hebrew, plain, to_visual

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
    # smallest listed DN that is still >= the pipe, else the last row. Slopes are fall/run.
    "min_slopes": [
        {"dn_mm": 150, "slope": 0.015},
        {"dn_mm": 200, "slope": 0.010},
        {"dn_mm": 250, "slope": 0.007},
        {"dn_mm": 300, "slope": 0.005},
        {"dn_mm": 400, "slope": 0.004},
        {"dn_mm": 500, "slope": 0.003},
        {"dn_mm": 600, "slope": 0.0025},
    ],
    # office starting values for the SN cover hook, millimetres of cover. Not a quoted utility clause.
    "sn_cover_mm": {"SN2": 800, "SN4": 600, "SN8": 450, "SN16": 300},
    "utility_submission": True,
}

ANNOTATION_ROLES = {"dim", "table", "annotation", "title"}
# Sized to a symbol, not to the ISO text series.
SYMBOL_ROLES = {"balloon", "north"}
GENERAL_ROLES = {"label", "text", "notes", "legend"}
NTS_RE = re.compile(r"\bN\.?\s*T\.?\s*S\.?\b|not\s*to\s*scale|לא בקנה", re.IGNORECASE)
INV_RE = re.compile(r"INV\.?\s*[:=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
SLOPE_RE = re.compile(r"(?:SLOPE|שיפוע)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*(%|‰)?", re.IGNORECASE)
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
    origin: tuple = (0.0, 0.0)       # model-space frame origin
    paper_rect: tuple = (0.0, 0.0, 0.0, 0.0)  # model-space paper rectangle
    margin: float = 10.0


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
        scene = build_scene(spec, catalog, project)
    except Exception as exc:  # noqa: BLE001 - a spec that cannot be laid out still keeps the other checks
        findings.append(Finding("warning", "qa_layout", f"Drawing QA layout failed: {exc}",
                                f"פריסת בקרת השרטוט נכשלה: {exc}"))
    else:
        findings += _geometry(scene, cfg)
        findings += _text_standards(scene, cfg)
        findings += _lineweights(scene, cfg)
        findings += _submission(scene.doc, cfg)
    findings += _multiview_omissions(spec, catalog, project)
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


def _paper_segments(ctx, paper, spans) -> list:
    """Segments as (x0, y0, x1, y1, layer, owner). Owner is the element id, empty for the sheet."""
    owner_of = {}
    for span in spans:
        for entity in span["entities"]:
            owner_of[id(entity)] = span["eid"]
    segs = []
    for entity in ctx.msp:
        owner = owner_of.get(id(entity), "")
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
                segs.append((*a, *b, layer, owner))
            elif kind == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in item.get_points("xy")]
                pairs = list(zip(pts, pts[1:]))
                if item.closed and len(pts) > 2:
                    pairs.append((pts[-1], pts[0]))
                for a, b in pairs:
                    pa, pb = paper(*a), paper(*b)
                    segs.append((*pa, *pb, layer, owner))
            elif kind == "CIRCLE":
                segs += _sample_arc(item.dxf.center, item.dxf.radius, 0, 360, paper, layer, owner)
            elif kind == "ARC":
                segs += _sample_arc(item.dxf.center, item.dxf.radius, item.dxf.start_angle,
                                    item.dxf.end_angle, paper, layer, owner)
    return segs


def _sample_arc(center, radius, a0, a1, paper, layer, owner="", n=16) -> list:
    if a1 <= a0:
        a1 += 360
    segs = []
    prev = None
    steps = max(4, int(n * (a1 - a0) / 360))
    for i in range(steps + 1):
        ang = math.radians(a0 + (a1 - a0) * i / steps)
        pt = paper(center.x + radius * math.cos(ang), center.y + radius * math.sin(ang))
        if prev is not None:
            segs.append((*prev, *pt, layer, owner))
        prev = pt
    return segs


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


def _multiview_omissions(spec, catalog, project) -> list[Finding]:
    """Unplaced views, and note strings a multi-view sheet did not draw.

    Single-view sheets are checked by the model-space layout and are not rendered again. A
    multi-view sheet is rendered once: each view the sheet did not place is `omitted_view`, and
    every spec note absent from that sheet is `omitted_strings`.
    """
    if not any(isinstance(view, dict) for view in (spec.get("views") or [])):
        return []
    notes = spec.get("notes") or {}
    lines = []
    if isinstance(notes, dict):
        for lang in ("en", "he"):
            for line in notes.get(lang) or []:
                text = str(line).strip()
                if text:
                    lines.append(text)
    import tempfile

    import ezdxf

    from .drawing import render

    with tempfile.TemporaryDirectory() as tmp:
        res = render(spec, tmp, "qa_sheet", formats=("dxf",), catalog=catalog, project=project)
        doc = ezdxf.readfile(res["dxf"])
    findings = []
    seen = set()
    for warning in res.get("warnings") or []:
        if "not shown on this sheet" not in warning:
            continue
        match = re.search(r"view (['\"])(.+?)\1", warning)
        key = match.group(2) if match else ""
        if key in seen:
            continue
        seen.add(key)
        findings.append(Finding(
            "error", "omitted_view",
            f"View {key} is not shown on this sheet. Use a larger sheet (A2/A1).",
            f"מבט {key} לא מוצג בגיליון. צריך גיליון גדול יותר (A2/A1).",
            key))
    if not lines:
        return findings
    chunks = []
    layouts = [doc.modelspace()]
    sheet = doc.layouts.get("SHEET") if "SHEET" in doc.layouts else None
    if sheet is not None:
        layouts.append(sheet)
    for layout in layouts:
        for entity in layout.query("TEXT MTEXT"):
            chunks.append(entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text)
    blob = "\n".join(chunks).replace("\\P", " ")
    missing = [line for line in lines if line not in blob]
    if missing:
        shown = "; ".join(missing[:8])
        findings.append(Finding("error", "omitted_strings",
                                 f"Notes omitted from the sheet: {shown}",
                                 f"הערות חסרות בגיליון: {shown}", "notes"))
    return findings


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


def _is_nts(spec) -> bool:
    """Not-to-scale: spec.nts, or N.T.S. / NTS in the view, views, sheet scale, notes or text."""
    if spec.get("nts"):
        return True
    blobs = [str(spec.get("view") or "")]
    if isinstance(spec.get("scale"), str):
        blobs.append(spec["scale"])
    for view in spec.get("views") or []:
        if isinstance(view, dict):
            blobs.extend(str(view.get(key) or "") for key in ("view", "name", "scale", "title", "en", "he"))
        else:
            blobs.append(str(view))
    fields = (spec.get("sheet") or {}).get("fields") or {}
    if isinstance(fields, dict):
        blobs.extend(str(value) for value in fields.values() if isinstance(value, str))
    for el in spec.get("elements") or []:
        if isinstance(el, dict):
            blobs.append(_blob(el.get("text")))
            blobs.append(_blob(el.get("label")))
    notes = spec.get("notes") or {}
    if isinstance(notes, dict):
        for lang in ("en", "he"):
            blobs.extend(str(line) for line in notes.get(lang) or [])
    for dim in spec.get("dimensions") or []:
        if isinstance(dim, dict):
            blobs.extend(str(text) for text in dim.get("texts") or [])
    return any(NTS_RE.search(blob) for blob in blobs if blob)


def _dimensions(spec, cfg) -> list[Finding]:
    out = []
    nts = _is_nts(spec)
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


def _as_ratio(slope, unit=None) -> float:
    """Fall/run. `unit` is ratio, percent or permille. A bare number is a ratio, not a guess."""
    slope = float(slope)
    unit = (unit or "ratio").lower()
    if unit in ("percent", "%", "pct"):
        return slope / 100.0
    if unit in ("permille", "‰"):
        return slope / 1000.0
    return slope


def _slope_of(el) -> tuple[float, str] | None:
    """(value, unit) from slope/slope_unit, otherwise from a SLOPE / שיפוע label."""
    if el.get("slope") is not None:
        return float(el["slope"]), str(el.get("slope_unit") or "ratio")
    match = SLOPE_RE.search(_blob(el.get("label")) + " " + _blob(el.get("text")))
    if not match:
        return None
    mark = match.group(2) or ""
    unit = "percent" if mark == "%" else ("permille" if mark == "‰" else "ratio")
    return float(match.group(1)), unit


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
        parsed = _slope_of(el)
        ratio = _as_ratio(*parsed) if parsed else None
        inv = _pipe_inv(el, _inv_for_pipe(el, elements, inv_texts, spec), spec)
        checked_fall = False
        checked_min = False
        if ratio is not None and inv is not None and inv[2] > 0:
            expected = ratio * inv[2]
            actual = inv[0] - inv[1]
            checked_fall = True
            if abs(actual - expected) > tol:
                out.append(Finding(
                    "error", "slope_mismatch",
                    f"Pipe {eid}: fall {actual:.4g} differs from slope × length {expected:.4g}",
                    f"צינור {eid}: הפרש רומים {actual:.4g} אינו שווה לשיפוע × אורך ({expected:.4g})",
                    eid))
        used = ratio
        if used is None and inv is not None and inv[2] > 0:
            used = abs(inv[0] - inv[1]) / inv[2]
        dn = _dn_mm(el, catalog, units)
        need = _min_slope(dn, bands) if dn and used is not None else None
        if need is not None:
            checked_min = True
            if used + 1e-6 < need:
                out.append(Finding(
                    "warning", "min_slope",
                    f"Pipe {eid}: slope {used * 100:.2f}% is below {need * 100:.2f}% for DN {dn:.0f}",
                    f"צינור {eid}: שיפוע {used * 100:.2f}% קטן מ-{need * 100:.2f}% לקוטר {dn:.0f}",
                    eid))
        if not checked_fall or not checked_min:
            missing_en, missing_he = [], []
            if not checked_fall:
                missing_en.append("no slope with an invert pair")
                missing_he.append("אין שיפוע עם זוג רומים")
            if not checked_min:
                missing_en.append("no diameter or no slope")
                missing_he.append("אין קוטר או אין שיפוע")
            out.append(Finding(
                "info", "slope_unchecked",
                f"Pipe {eid} slope was not fully checked: {'; '.join(missing_en)}",
                f"שיפוע הצינור {eid} לא נבדק עד הסוף: {'; '.join(missing_he)}",
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


def _metric_elevations(a, b, length, units):
    """Invert numbers written in metres on an mm drawing (225.00 along a 40 m run)."""
    if units != "mm" or length <= 0:
        return a, b
    fall = abs(a - b)
    if max(abs(a), abs(b)) <= 5000 and fall * 1000 <= length * 0.2 and fall < length * 0.002:
        return a * 1000.0, b * 1000.0
    return a, b


def _numeric_scale(spec) -> float:
    """Sheet denominator as a number. ``auto`` and ``NTS`` use the scale the sheet is drawn at."""
    raw = spec.get("scale", 1) if isinstance(spec, dict) else 1
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    try:
        from .drawing import sheet_scale_number
        return float(sheet_scale_number(spec if isinstance(spec, dict) else {}))
    except (TypeError, ValueError, KeyError):
        return 20.0


def _model_tol(paper_mm, spec) -> float:
    """Paper millimetres as drawing units. 10 mm on a 1:400 sheet is 4000 model-mm."""
    scale = _numeric_scale(spec)
    units = spec.get("units", "mm") if isinstance(spec, dict) else "mm"
    return float(paper_mm) * scale * (0.001 if units == "m" else 1.0)


def _project(points, pt):
    """(station along the polyline, perpendicular distance) of the closest point on it."""
    if not points or not isinstance(pt, (list, tuple)) or len(pt) < 2:
        return None
    px, py = float(pt[0]), float(pt[1])
    best = None
    station = 0.0
    for a, b in zip(points, points[1:]):
        ax, ay = float(a[0]), float(a[1])
        bx, by = float(b[0]), float(b[1])
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        if seg <= 1e-9:
            cand = (math.hypot(px - ax, py - ay), station)
        else:
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (seg * seg)))
            cand = (math.hypot(px - (ax + t * dx), py - (ay + t * dy)), station + seg * t)
        if best is None or cand[0] < best[0]:
            best = cand
        station += seg
    if best is None:
        return None
    return best[1], best[0]


def _stated_run(el, points):
    """Run between explicit invert stations or inv_at points. None when the pipe does not say where."""
    stations = el.get("inv_stations")
    if isinstance(stations, (list, tuple)) and len(stations) >= 2:
        try:
            return abs(float(stations[0]) - float(stations[1]))
        except (TypeError, ValueError):
            return None
    at = el.get("inv_at")
    if isinstance(at, (list, tuple)) and len(at) >= 2 and _project([at[0], at[1]], at[0]) is not None:
        if len(points) >= 2:
            p0, p1 = _project(points, at[0]), _project(points, at[1])
            if p0 and p1:
                return abs(p0[0] - p1[0])
        try:
            return math.dist(at[0], at[1])
        except (TypeError, ValueError):
            return None
    return None


def _inv_for_pipe(el, elements, texts, spec) -> list:
    """INV texts for this pipe.

    An explicit `pipe` id always counts. An unlinked text belongs to every pipe within
    10 paper-mm. When one of those pipes states a slope, only the sloped pipes keep it,
    so a short connector nearer the text does not take it away from the main run.
    """
    eid = el.get("id")
    pts = el.get("points") or []
    limit = _model_tol(10.0, spec)
    pipes = [e for e in elements if e.get("type") == "pipe" and len(e.get("points") or []) >= 2]
    owned = []
    for text in texts:
        if not text.get("at"):
            continue
        if text.get("pipe"):
            if text.get("pipe") == eid:
                proj = _project(pts, text["at"])
                if proj:
                    owned.append({**text, "station": proj[0]})
            continue
        hits = []
        for pipe in pipes:
            proj = _project(pipe.get("points") or [], text["at"])
            if proj and proj[1] <= limit:
                hits.append((pipe, proj))
        if any(_slope_of(pipe) for pipe, _proj in hits):
            hits = [(pipe, proj) for pipe, proj in hits if _slope_of(pipe)]
        for pipe, proj in hits:
            if pipe.get("id") == eid:
                owned.append({**text, "station": proj[0]})
    return owned


def _pipe_inv(el, texts, spec):
    """(start, end, length). Length is the run between the two inverts, never the whole pipe by default."""
    units = spec.get("units", "mm")
    inv = el.get("inv")
    if isinstance(inv, (list, tuple)) and len(inv) >= 2:
        length = _stated_run(el, el.get("points") or [])
        if length is None or length <= 0:
            return None
        a, b = _metric_elevations(float(inv[0]), float(inv[1]), length, units)
        return a, b, length
    if len(texts) < 2:
        return None
    ordered = sorted(texts, key=lambda item: item.get("station", 0.0))
    start, end = ordered[0], ordered[-1]
    length = abs(float(end["station"]) - float(start["station"]))
    if length <= 0:
        return None
    a, b = _metric_elevations(start["value"], end["value"], length, units)
    return a, b, length


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
            rows.append((float(row["dn_mm"]), _as_ratio(row["slope"], row.get("slope_unit"))))
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
    """The label's own leader is not linework. Another element's pipe along that leader is."""
    if len(seg) > 5 and seg[5] != box.eid:
        return False
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
    """True when the segment overlaps the text box, including a run along its edge.

    A connector that follows the label's insert (the left edge of left-aligned text) used
    to miss: the old test shrank the box and rejected a collinear edge. A mere corner touch
    is still too short to count.
    """
    return _clip_length(seg[0], seg[1], seg[2], seg[3], box.x0, box.y0, box.x1, box.y1) > 0.15


def _clip_length(x0, y0, x1, y1, left, bottom, right, top) -> float:
    """Length of the segment inside the closed rectangle (Liang-Barsky)."""
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - bottom), (dy, top - y0)):
        if abs(p) <= 1e-12:
            if q < -1e-9:
                return 0.0
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return 0.0
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return 0.0
            if r < t1:
                t1 = r
    if t0 >= t1:
        return 0.0
    return (t1 - t0) * math.hypot(dx, dy)


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
        # A section title at 3.5 mm is a title, not a second height of the captions around it.
        # Off-series title-block heights still warn until drawing/standards.py owns them.
        if box.role == "title":
            below = box.height + 1e-6 < float(cfg["min_annotation_mm"])
            if below or _outside_series(box, cfg):
                off.setdefault(box.role, set()).add(round(box.height, 2))
            continue
        # Notes use the engine's 2.0 mm default until standards.py. Below the annotation
        # minimum is still an error; 2.0 mm is reported as off-series, not a blocking error.
        annotation = box.role in ANNOTATION_ROLES or box.role == "notes"
        limit = float(cfg["min_annotation_mm"] if annotation else cfg["min_text_mm"])
        if box.height + 1e-6 < limit:
            out.append(_too_small(box, limit))
            continue
        if _outside_series(box, cfg):
            off.setdefault(box.role, set()).add(round(box.height, 2))
        if box.role not in SYMBOL_ROLES:
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


def _series_heights(cfg, annotation: bool) -> list[float]:
    allowed = [float(v) for v in cfg.get("text_heights_mm") or []]
    if annotation:
        allowed += [float(v) for v in cfg.get("annotation_heights_mm") or []]
    return allowed


def _outside_series(box: TextBox, cfg) -> bool:
    """True when the box is large enough to read but not on the ISO series for its role."""
    if box.height <= 0 or box.role in SYMBOL_ROLES:
        return False
    annotation = box.role in ANNOTATION_ROLES or box.role == "notes"
    limit = float(cfg["min_annotation_mm"] if annotation else cfg["min_text_mm"])
    if box.height + 1e-6 < limit:
        return False
    return not any(abs(box.height - item) <= 0.05 for item in _series_heights(cfg, annotation))


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
# A finding with no geometry (sheet hygiene, line weights, a failed layout) is a table row only.
SHEET_CODES = frozenset({
    "lineweight_range", "lineweight_ratio", "layer_0", "defpoints",
    "not_bylayer", "block_layer", "qa_layout",
})
# Info with no geometry to circle, plus sheet-wide hygiene. A table row only.
NO_MARKER_CODES = SHEET_CODES | {"slope_unchecked"}
PAD_MM = 1.5
MAX_MARKER_MM = 28.0
MIN_MARKER_MM = 5.0
MAX_GROUP_MARKERS = 3
OVERLAY_DPI = 150
TABLE_HEAD_MM = 12.0
TABLE_ROW_MM = 11.2
PANEL_W_MM = 228.0
SEVERITY_COLOR = {"error": "#d00000", "warning": "#e67e00", "info": "#1d6aab"}
SEVERITY_HE = {"error": "שגיאה", "warning": "אזהרה", "info": "הערה"}


@dataclass
class OverlayMarker:
    """One numbered rectangle on the sheet. The label rectangle is in paper mm."""
    number: int
    severity: str
    code: str
    element: str
    x0: float
    y0: float
    x1: float
    y1: float
    thin: bool = False
    lx: float = 0.0
    ly: float = 0.0
    lw: float = 0.0
    lh: float = 0.0

    def label_rect(self) -> tuple[float, float, float, float]:
        return (self.lx, self.ly, self.lx + self.lw, self.ly + self.lh)


@dataclass
class OverlayRow:
    number: int
    severity: str
    code: str
    element: str
    he: str
    on_sheet: bool


def overlay_plan(spec: dict, catalog=None, project=None) -> tuple[list[OverlayMarker], list[OverlayRow]]:
    """Numbered markers and the findings table. Marker labels are placed so they do not overlap."""
    return _plan_overlay(review(spec, catalog, project))


def write_overlay(spec: dict, path, catalog=None, project=None) -> str | None:
    """Draw numbered QA markers on a faded trial preview, with a findings table beside the sheet.

    Errors are red, warnings orange. A finding with no location is a table row only.
    Returns the PNG path, or None when the sheet cannot be laid out.
    """
    import matplotlib
    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Rectangle

    result = review(spec, catalog, project)
    scene = result.scene
    if scene is None:
        return None
    markers, rows = _plan_overlay(result)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    left, bottom, right, top, panel_x, panel_w = _overlay_limits(scene, markers, rows)
    fig, ax = plt.subplots(figsize=((right - left) / 25.4, (top - bottom) / 25.4), dpi=OVERLAY_DPI)
    _draw_underlay(ax, scene)
    ax.set_xlim(left, right)
    ax.set_ylim(bottom, top)
    ax.set_aspect("equal", adjustable="box")
    m = scene.margin
    pw, ph = scene.paper
    ax.add_patch(Rectangle((-m, -m), pw, ph, fill=False, ec="#9aa3ad", lw=0.6, zorder=2))
    font = font_manager.FontProperties(family="DejaVu Sans")
    for marker in markers:
        color = SEVERITY_COLOR.get(marker.severity, SEVERITY_COLOR["info"])
        ax.add_patch(Rectangle((marker.x0, marker.y0), marker.x1 - marker.x0, marker.y1 - marker.y0,
                               fill=False, ec=color, lw=0.55 if marker.thin else 1.15, zorder=3))
        tick = _tick(marker)
        if tick is not None:
            ax.plot([tick[0], tick[2]], [tick[1], tick[3]], color=color, lw=0.7, zorder=4,
                    solid_capstyle="round")
        ax.add_patch(Rectangle((marker.lx, marker.ly), marker.lw, marker.lh,
                               fc="white", ec=color, lw=0.8, zorder=4))
        ax.text(marker.lx + marker.lw / 2, marker.ly + marker.lh / 2, str(marker.number),
                color=color, fontsize=7, ha="center", va="center", zorder=5,
                fontproperties=font, fontweight="bold")
    _draw_table(ax, rows, panel_x, top - 2, panel_w, font)
    ax.axis("off")
    fig.savefig(path, dpi=OVERLAY_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(path)


def _plan_overlay(result: Review) -> tuple[list[OverlayMarker], list[OverlayRow]]:
    """One table row per finding. Several elements of one role share that row and its number.

    The first few of the group are marked. `slope_unchecked` and other sheet-wide codes stay in the table.
    """
    markers: list[OverlayMarker] = []
    rows: list[OverlayRow] = []
    number = 0
    scene = result.scene
    for finding in result.findings:
        places = [] if finding.code in NO_MARKER_CODES else _places(finding, scene, result.settings)
        number += 1
        if not places:
            rows.append(OverlayRow(number, finding.severity, finding.code,
                                   finding.element or "—", finding.he, False))
            continue
        ids = list(dict.fromkeys(element for element, _rect in places))
        rows.append(OverlayRow(number, finding.severity, finding.code, _group_label(ids),
                               finding.he, True))
        for element, rect in places[:MAX_GROUP_MARKERS]:
            markers.append(OverlayMarker(number, finding.severity, finding.code, element, *rect))
    _place_labels(markers)
    return markers, rows


def _group_label(ids: list[str]) -> str:
    """Count plus the ids, so one role does not become a row per cell."""
    if len(ids) == 1:
        return ids[0]
    shown = ids[:8]
    extra = f" +{len(ids) - len(shown)}" if len(ids) > len(shown) else ""
    return f"×{len(ids)}: {', '.join(shown)}{extra}"


def _places(finding: Finding, scene: Scene | None, cfg) -> list[tuple[str, tuple]]:
    """One capped rectangle per affected element. Sheet-wide codes stay off the drawing."""
    if scene is None or finding.code in SHEET_CODES:
        return []
    ids = [part.strip() for part in re.split(r"[,+]", finding.element or "") if part.strip()]
    if not ids:
        return []
    seen: set[int] = set()
    boxes = []
    for box in scene.boxes:
        if not (box.eid in ids or box.role in ids or any(box.eid.startswith(i + ":") for i in ids)):
            continue
        if id(box) in seen:
            continue
        seen.add(id(box))
        boxes.append(box)
    if finding.code == "text_height_standard":
        boxes = [box for box in boxes if _outside_series(box, cfg)]
    if boxes:
        return [(box.eid, _outline(box.x0, box.y0, box.x1, box.y1)) for box in boxes]
    out = []
    for eid in ids:
        pts = [(x, y) for peid, x, y in scene.points if peid == eid]
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append((eid, _outline(min(xs), min(ys), max(xs), max(ys))))
    return out


def _outline(x0: float, y0: float, x1: float, y1: float) -> tuple:
    """Padded rectangle. A target larger than the cap keeps its full extent and is drawn thin."""
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x0 -= PAD_MM
    y0 -= PAD_MM
    x1 += PAD_MM
    y1 += PAD_MM
    thin = (x1 - x0) > MAX_MARKER_MM or (y1 - y0) > MAX_MARKER_MM
    if thin:
        return x0, y0, x1, y1, True
    if x1 - x0 < MIN_MARKER_MM:
        cx = (x0 + x1) / 2
        x0, x1 = cx - MIN_MARKER_MM / 2, cx + MIN_MARKER_MM / 2
    if y1 - y0 < MIN_MARKER_MM:
        cy = (y0 + y1) / 2
        y0, y1 = cy - MIN_MARKER_MM / 2, cy + MIN_MARKER_MM / 2
    return x0, y0, x1, y1, False


def _label_size(number: int) -> tuple[float, float]:
    return (4.4 if number < 10 else 6.4), 3.8


def _labels_hit(a, b, gap: float = 0.6) -> bool:
    return not (a[2] + gap <= b[0] or b[2] + gap <= a[0] or a[3] + gap <= b[1] or b[3] + gap <= a[1])


def _place_labels(markers: list[OverlayMarker]) -> None:
    """Put each badge outside the flagged box. A short tick is drawn from the badge back to it."""
    placed: list[tuple[float, float, float, float]] = []
    gap = 1.4
    for marker in markers:
        w, h = _label_size(marker.number)
        box = (marker.x0, marker.y0, marker.x1, marker.y1)
        cx = (marker.x0 + marker.x1) / 2
        cy = (marker.y0 + marker.y1) / 2
        seeds = [
            (cx - w / 2, marker.y1 + gap),
            (cx - w / 2, marker.y0 - gap - h),
            (marker.x1 + gap, cy - h / 2),
            (marker.x0 - gap - w, cy - h / 2),
            (marker.x1 + gap, marker.y1 + gap),
            (marker.x0 - gap - w, marker.y1 + gap),
        ]
        for radius in (10.0, 18.0, 28.0, 40.0):
            for step in range(8):
                angle = math.radians(step * 45 + 20)
                seeds.append((cx + radius * math.cos(angle) - w / 2,
                              cy + radius * math.sin(angle) - h / 2))
        chosen = None
        for x, y in seeds:
            rect = (x, y, x + w, y + h)
            if _labels_hit(rect, box, gap=0.4):
                continue
            if any(_labels_hit(rect, other) for other in placed):
                continue
            chosen = rect
            break
        if chosen is None:
            x, y = marker.x1 + gap, marker.y1 + gap
            rect = (x, y, x + w, y + h)
            while _labels_hit(rect, box, 0.4) or any(_labels_hit(rect, other) for other in placed):
                y += h + 0.8
                rect = (x, y, x + w, y + h)
            chosen = rect
        placed.append(chosen)
        marker.lx, marker.ly, marker.lw, marker.lh = chosen[0], chosen[1], w, h


def _rect_hit(rect, ux, uy, cx, cy) -> tuple[float, float]:
    """Where a ray from the rectangle centre along (ux, uy) leaves the rectangle."""
    x0, y0, x1, y1 = rect
    times = []
    if abs(ux) > 1e-9:
        times.append(((x1 if ux > 0 else x0) - cx) / ux)
    if abs(uy) > 1e-9:
        times.append(((y1 if uy > 0 else y0) - cy) / uy)
    positive = [t for t in times if t > 1e-6]
    t = min(positive) if positive else 0.0
    return cx + ux * t, cy + uy * t


def _tick(marker: OverlayMarker):
    """A short stroke from the badge toward the flagged box, so the number is not sitting on the text."""
    bcx, bcy = marker.lx + marker.lw / 2, marker.ly + marker.lh / 2
    tcx, tcy = (marker.x0 + marker.x1) / 2, (marker.y0 + marker.y1) / 2
    dx, dy = tcx - bcx, tcy - bcy
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return None
    ux, uy = dx / dist, dy / dist
    sx, sy = _rect_hit((marker.lx, marker.ly, marker.lx + marker.lw, marker.ly + marker.lh), ux, uy, bcx, bcy)
    ex, ey = _rect_hit((marker.x0, marker.y0, marker.x1, marker.y1), -ux, -uy, tcx, tcy)
    gap = math.hypot(ex - sx, ey - sy)
    if gap < 0.4:
        return None
    length = min(3.2, gap)
    return sx, sy, sx + (ex - sx) / gap * length, sy + (ey - sy) / gap * length


def _overlay_limits(scene: Scene, markers, rows):
    m = scene.margin
    pw, ph = scene.paper
    left, bottom = -m - 3, -m - 3
    sheet_right, top = pw - m + 3, ph - m + 3
    right = sheet_right
    for marker in markers:
        left = min(left, marker.x0 - 1, marker.lx - 1)
        right = max(right, marker.x1 + 1, marker.lx + marker.lw + 1)
        bottom = min(bottom, marker.y0 - 1, marker.ly - 1)
        top = max(top, marker.y1 + 1, marker.ly + marker.lh + 1)
    panel_w = PANEL_W_MM
    panel_x = right + 8
    table_h = _table_height(len(rows)) + 4
    if top - bottom < table_h:
        bottom = top - table_h
    return left, bottom, panel_x + panel_w, top, panel_x, panel_w


def _table_height(nrows: int) -> float:
    return TABLE_HEAD_MM + TABLE_ROW_MM * max(nrows, 1) + 3


def _draw_underlay(ax, scene: Scene) -> None:
    """Faded trial preview (real linework and text) in the sheet's paper-mm frame."""
    import io
    import os
    import tempfile

    import matplotlib.pyplot as plt

    from .drawing import preview

    fd, name = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    tmp = Path(name)
    fig = None
    try:
        scene.doc.saveas(tmp)
        fig = preview.render_figure(preview.preview_doc(tmp), scene.paper_rect, scene.paper)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=OVERLAY_DPI, facecolor="white")
        buf.seek(0)
        image = plt.imread(buf)
        rgb = image[..., :3]
        faded = rgb * 0.72 + 0.28
        m = scene.margin
        pw, ph = scene.paper
        ax.imshow(faded, extent=(-m, pw - m, -m, ph - m), origin="upper",
                  interpolation="bilinear", zorder=0, aspect="auto")
    except Exception:  # noqa: BLE001 - the markers still stand if the preview cannot be drawn
        return
    finally:
        if fig is not None:
            plt.close(fig)
        tmp.unlink(missing_ok=True)


def _draw_table(ax, rows: list[OverlayRow], x: float, top: float, width: float, font) -> None:
    from matplotlib.patches import FancyBboxPatch, Rectangle

    row_h = TABLE_ROW_MM
    header_h = TABLE_HEAD_MM
    height = _table_height(len(rows))
    y0 = top - height
    ax.add_patch(Rectangle((x, y0), width, height, fc="white", ec="#c5cdd6", lw=0.6, zorder=2))
    ax.text(x + width / 2, top - 4.2, _table_text("ממצאי בקרת שרטוט"), ha="center", va="center",
            fontsize=8, color="#1c2833", fontproperties=font, zorder=3)
    heads = ((4, "מס'"), (16, "חומרה"), (38, "קוד"), (96, "אלמנט"))
    for dx, label in heads:
        ax.text(x + dx, top - header_h + 2.2, _table_text(label), fontsize=5.5, color="#5d6d7e",
                fontproperties=font, zorder=3)
    ax.text(x + 132, top - header_h + 2.2, _table_text("ממצא"), fontsize=5.5, color="#5d6d7e",
            fontproperties=font, zorder=3)
    for index, row in enumerate(rows):
        y = top - header_h - row_h * (index + 0.5)
        color = SEVERITY_COLOR.get(row.severity, SEVERITY_COLOR["info"])
        if index % 2:
            ax.add_patch(Rectangle((x + 0.4, y - row_h / 2), width - 0.8, row_h,
                                   fc="#f7f9fb", ec="none", zorder=2))
        badge_w = 7.2 if row.number < 10 else 9.2
        ax.add_patch(FancyBboxPatch((x + 2.2, y - 1.7), badge_w, 3.4,
                                    boxstyle="round,pad=0.02,rounding_size=0.6",
                                    fc=color, ec=color, lw=0.4, zorder=3))
        ax.text(x + 2.2 + badge_w / 2, y, str(row.number), color="white", fontsize=6,
                ha="center", va="center", fontproperties=font, fontweight="bold", zorder=4)
        ax.text(x + 16, y, _table_text(SEVERITY_HE.get(row.severity, row.severity)), color=color,
                fontsize=5.5, va="center", fontproperties=font, zorder=4)
        ax.text(x + 38, y, row.code, color="#1c2833", fontsize=5.5, va="center",
                fontproperties=font, zorder=4)
        element = _fit_column(row.element or "—", 34.0, 5.5)
        ax.text(x + 96, y, _table_text(element), color="#1c2833", fontsize=5.5, va="center",
                fontproperties=font, zorder=4)
        lines = [_table_text(line) for line in _message_lines(row.he)]
        ys = [y] if len(lines) == 1 else [y + 1.7, y - 1.9]
        for yy, line in zip(ys, lines):
            ax.text(x + 132, yy, line, color="#1c2833", fontsize=6, va="center",
                    ha="left", fontproperties=font, zorder=4)


def _message_lines(text: str, width: int = 50) -> list[str]:
    """Up to two lines, split on words so a Hebrew phrase is not cut mid-word."""
    words = [part for part in " ".join((text or "").split()).split(" ") if part]
    if not words:
        return ["—"]
    lines: list[str] = []
    cur = ""
    index = 0
    while index < len(words) and len(lines) < 2:
        word = words[index]
        cand = f"{cur} {word}".strip()
        if len(cand) <= width or not cur:
            cur = cand
            index += 1
            continue
        lines.append(cur)
        cur = ""
    if cur and len(lines) < 2:
        lines.append(cur)
    if index < len(words) and lines:
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def _span_leaders(entities, paper) -> list:
    leaders = []
    for entity in entities:
        if entity.dxftype() != "LINE":
            continue
        leaders.append((*paper(entity.dxf.start.x, entity.dxf.start.y),
                        *paper(entity.dxf.end.x, entity.dxf.end.y)))
    return leaders


def _text_entities(entity) -> list:
    if entity.dxftype() == "DIMENSION":
        try:
            return [item for item in entity.virtual_entities() if item.dxftype() in ("TEXT", "MTEXT")]
        except Exception:  # noqa: BLE001
            return []
    if entity.dxftype() in ("TEXT", "MTEXT", "ATTRIB"):
        return [entity]
    return []


@lru_cache(maxsize=4)
def _ink_font(hebrew: bool) -> str:
    """Liberation Sans is the Arial-metric face. DejaVu Sans supplies Hebrew glyphs."""
    from matplotlib.font_manager import FontProperties, findfont
    family = "DejaVu Sans" if hebrew else "Liberation Sans"
    return findfont(FontProperties(family=family), fallback_to_default=True)


@lru_cache(maxsize=4)
def _cap_height(fname: str) -> float:
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    height = float(TextPath((0, 0), "H", size=1.0, prop=FontProperties(fname=fname)).get_extents().height)
    return height or 1.0


def _ink_width(text: str, h: float, *, hebrew: bool | None = None) -> float:
    """Width of the drawn glyphs at cap height h, without the layout safety margin."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    raw = plain(text or "")
    if not raw or h <= 0:
        return 0.0
    fname = _ink_font(has_hebrew(raw) if hebrew is None else hebrew)
    size = h / _cap_height(fname)
    return float(TextPath((0, 0), raw, size=size, prop=FontProperties(fname=fname)).get_extents().width)


def _ax_shapes_rtl() -> bool:
    """True when this matplotlib shapes Hebrew in ax.text (it was built with libraqm)."""
    try:
        from matplotlib import ft2font
    except Exception:  # noqa: BLE001
        return False
    return bool(getattr(ft2font, "__libraqm_version__", None))


def _table_text(text: str) -> str:
    """Hebrew as the overlay table should draw it.

    The sheet preview converts to visual order because it paints glyph outlines.
    ax.text does that itself when libraqm is present, and converting again reverses
    the words. Without libraqm, use the same visual order as the preview.
    """
    if not text or _ax_shapes_rtl():
        return text or ""
    return to_visual(text)


def _fit_column(text: str, width_mm: float, fontsize_pt: float) -> str:
    """Keep an element list inside its column. A long '×7: …' list is cut, not spilled."""
    raw = text or "—"
    # fontsize is an em; treating it as cap height overestimates the width, so the cut is early.
    h = fontsize_pt / 72.0 * 25.4
    if _ink_width(raw, h, hebrew=True) <= width_mm:
        return raw
    mark = "…"
    lo, hi = 0, len(raw)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = raw[:mid].rstrip(" ,") + mark
        if _ink_width(cand, h, hebrew=True) <= width_mm:
            lo = mid
        else:
            hi = mid - 1
    if lo <= 0:
        return mark
    return raw[:lo].rstrip(" ,") + mark


def _entity_text_box(entity, paper, k, eid, role, in_notes, leaders) -> TextBox | None:
    """Paper-mm box from the entity's insert, height, width and alignment."""
    kind = entity.dxftype()
    if kind == "MTEXT":
        raw = entity.plain_text() or ""
        h = float(entity.dxf.char_height)
        lines = [ln for ln in raw.splitlines() if ln.strip()] or [raw]
        nlines = max(1, len(lines))
        width = max((_ink_width(line, h) for line in lines), default=0.0)
        column = float(entity.dxf.width or 0)
        if 0 < column < width:
            width = column
        insert = entity.dxf.insert
        attach = int(entity.dxf.attachment_point or 1)
        rotation = float(entity.dxf.rotation or 0)
        row, col = divmod(max(attach, 1) - 1, 3)
        th = h + (nlines - 1) * LINE_SPACING * h
        if row == 0:
            y0, y1 = -th, 0.0
        elif row == 1:
            y0, y1 = -th / 2, th / 2
        else:
            y0, y1 = 0.0, th
        if col == 0:
            x0, x1 = 0.0, width
        elif col == 1:
            x0, x1 = -width / 2, width / 2
        else:
            x0, x1 = -width, 0.0
    else:
        raw = entity.dxf.text or ""
        h = float(entity.dxf.height or 0)
        if h <= 0 or not str(raw).strip():
            return None
        width = _ink_width(raw, h)
        nlines = 1
        halign = int(entity.dxf.halign or 0)
        valign = int(entity.dxf.valign or 0)
        insert = entity.dxf.align_point if (halign or valign) else entity.dxf.insert
        rotation = float(entity.dxf.rotation or 0)
        if halign in (1, 4):
            x0, x1 = -width / 2, width / 2
        elif halign == 2:
            x0, x1 = -width, 0.0
        else:
            x0, x1 = 0.0, width
        if halign == 4 or valign == 2:
            y0, y1 = -h / 2, h / 2
        elif valign == 3:
            y0, y1 = -h, 0.0
        else:
            y0, y1 = -0.2 * h, h
    if not str(raw).strip() or h <= 0:
        return None
    ang = math.radians(rotation)
    cos, sin = math.cos(ang), math.sin(ang)
    xs, ys = [], []
    for lx, ly in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        rx, ry = lx * cos - ly * sin, lx * sin + ly * cos
        px, py = paper(float(insert.x) + rx, float(insert.y) + ry)
        xs.append(px)
        ys.append(py)
    return TextBox(eid, role, min(xs), min(ys), max(xs), max(ys), h / k, nlines, leaders, in_notes)


def _near_boxes(a: TextBox, b: TextBox) -> bool:
    pad = max(a.height, b.height) * LINE_SPACING
    return not (a.x1 < b.x0 - pad or b.x1 < a.x0 - pad or a.y1 < b.y0 - pad or b.y1 < a.y0 - pad)


def _boxes_from_spans(spans, paper, k) -> list[TextBox]:
    boxes = []
    for span in spans:
        leaders = _span_leaders(span["entities"], paper)
        made = []
        for entity in span["entities"]:
            for item in _text_entities(entity):
                box = _entity_text_box(item, paper, k, span["eid"], span["role"], span["in_notes"], leaders)
                if box is not None:
                    made.append(box)
        clusters: list[list[TextBox]] = []
        for box in made:
            for group in clusters:
                if any(_near_boxes(box, other) for other in group):
                    group.append(box)
                    break
            else:
                clusters.append([box])
        for group in clusters:
            if len(group) == 1:
                boxes.append(group[0])
                continue
            boxes.append(TextBox(
                span["eid"], span["role"],
                min(box.x0 for box in group), min(box.y0 for box in group),
                max(box.x1 for box in group), max(box.y1 for box in group),
                max(box.height for box in group), len(group), leaders, span["in_notes"]))
    _split_legend_title(boxes)
    return boxes


def _split_legend_title(boxes: list[TextBox]) -> None:
    """Table cells share one height. The legend title is a different role, so 1.8 mm cells are not
    'inconsistent' with the title line above them."""
    legend = [box for box in boxes if box.eid == "legend" and box.role == "table"]
    if len(legend) < 2:
        return
    counts: dict[float, int] = {}
    for box in legend:
        key = round(box.height, 2)
        counts[key] = counts.get(key, 0) + 1
    cell_h = max(counts, key=counts.get)
    for box in legend:
        if abs(box.height - cell_h) > 0.05:
            box.role = "legend"


def build_scene(spec: dict, catalog=None, project=None, *, place_labels=True) -> Scene:
    """Paper-space boxes measured from the same sheet the renderer draws."""
    from .drawing import draw_sheet

    warnings: list[str] = []
    drawn = draw_sheet(spec, catalog=catalog, project=project, warnings=warnings,
                       place_labels=place_labels)
    ctx, template, origin, rects = drawn.ctx, drawn.template, drawn.origin, drawn.rects
    k = ctx.k or 1.0
    ox, oy = float(origin.x), float(origin.y)

    def paper(x, y) -> tuple[float, float]:
        return (float(x) - ox) / k, (float(y) - oy) / k

    boxes = _boxes_from_spans(drawn.spans, paper, k)
    segments = _paper_segments(ctx, paper, drawn.spans)
    # Centre-lines are what a label sitting in a pipe barrel crosses, even when the walls are drawn apart.
    for el in spec.get("elements") or []:
        if el.get("type") not in ("pipe", "polyline", "trench_drain"):
            continue
        pts = [pt for pt in (el.get("points") or []) if isinstance(pt, (list, tuple)) and len(pt) >= 2]
        owner = str(el.get("id") or "")
        for a, b in zip(pts, pts[1:]):
            segments.append((*paper(a[0], a[1]), *paper(b[0], b[1]), "MODEL", owner))
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
                 (float(template["paper"]["w"]), float(template["paper"]["h"])), ctx.doc, k,
                 (ox, oy), tuple(float(v) for v in rects["paper"]), float(template.get("margin") or 10))
