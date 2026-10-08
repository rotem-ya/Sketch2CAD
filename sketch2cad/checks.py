"""Automatic engineering checks on a drawing spec (improvement 2).

All geometry is in drawing units (spec.units); catalog dimensions are always mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict

SEVERITIES = ("error", "warning", "info")


@dataclass
class Finding:
    severity: str          # error | warning | info
    code: str
    en: str
    he: str
    element: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _mm(spec) -> float:
    """Drawing units per mm."""
    return 0.001 if spec.get("units") == "m" else 1.0


def _items_used(spec) -> list[tuple[str, str]]:
    """(element id or legend no, item id) for every catalog reference in the spec."""
    refs = [(e.get("id", e.get("type", "?")), e["item"]) for e in spec.get("elements", []) if e.get("item")]
    refs += [(f"#{l.get('no')}", l["item"]) for l in spec.get("legend", []) if l.get("item")]
    return refs


def check_catalog(spec, catalog) -> list[Finding]:
    out, seen = [], set()
    for ref, item_id in _items_used(spec):
        item = catalog.get(item_id) if catalog else None
        if item is None:
            out.append(Finding("error", "unknown_item", f"Item '{item_id}' is not in the catalog",
                               f"הפריט '{item_id}' לא קיים בקטלוג", ref))
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        missing = catalog.missing_source(item)
        if missing == ["source"]:
            out.append(Finding("warning", "no_source", f"'{item_id}': no submittal or document source",
                               f"'{item_id}': אין מקור - הגשה או מסמך", ref))
        elif missing:
            keys = ", ".join(missing)
            out.append(Finding("warning", "unsourced_dims", f"'{item_id}': dimensions without source: {keys}",
                               f"'{item_id}': מידות ללא מקור: {keys}", ref))
        if "estimate" in str(item.get("notes", "")).lower():
            out.append(Finding("info", "estimate", f"'{item_id}': {item['notes']}",
                               f"'{item_id}': כולל מידה משוערת - לאמת", ref))
    return out


def _pipe_od(el, catalog, k) -> float:
    if el.get("od"):
        return el["od"]
    item = catalog.get(el.get("item")) if catalog else None
    return ((item or {}).get("dims", {}).get("OD", 0)) * k


def check_cover(spec, catalog) -> list[Finding]:
    """Buried pipes in elevation/section: crown must be at least cover_min below grade_y."""
    if not any(w in str(spec.get("view", "")).lower() for w in ("elevation", "section", "חתך")):
        return []
    k = _mm(spec)
    params = spec.get("params", {})
    grade = params.get("grade_y", 0)
    cover_min = params.get("cover_min", 1200 * k)
    out = []
    for el in spec.get("elements", []):
        if el.get("type") != "pipe" or not el.get("below_grade"):
            continue
        crown = max(p[1] for p in el["points"]) + _pipe_od(el, catalog, k) / 2
        cover = grade - crown
        if cover < cover_min - 1e-9:
            c, m = round(cover / k), round(cover_min / k)
            out.append(Finding("error", "cover", f"Cover {c} mm < minimum {m} mm",
                               f"כיסוי {c} מ\"מ קטן מהמינימום {m} מ\"מ", el.get("id", "")))
    return out


def _chainage(points):
    """Cumulative length along a polyline."""
    ch = [0.0]
    for a, b in zip(points, points[1:]):
        ch.append(ch[-1] + math.dist(a, b))
    return ch


def _project(points, ch, p):
    """(distance to polyline, chainage of the closest point)."""
    best = (math.inf, 0.0)
    for i, (a, b) in enumerate(zip(points, points[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg = dx * dx + dy * dy
        t = 0.0 if seg == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg))
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.dist(p, q)
        if d < best[0]:
            best = (d, ch[i] + t * math.sqrt(seg))
    return best


def check_discharge(spec, catalog) -> list[Finding]:
    """Trench drains: the farthest point along the run from an outlet must be <= max_discharge_distance.

    Outlets are manholes and pipe end points lying within `outlet_tolerance` of the run.
    """
    params = spec.get("params", {})
    limit = params.get("max_discharge_distance")
    k = _mm(spec)
    tol = params.get("outlet_tolerance", 2000 * k)
    outlets = [e["at"] for e in spec.get("elements", []) if e.get("type") == "manhole"]
    for e in spec.get("elements", []):
        if e.get("type") == "pipe" and e.get("points"):
            outlets += [e["points"][0], e["points"][-1]]
    out = []
    for td in (e for e in spec.get("elements", []) if e.get("type") == "trench_drain"):
        pts = td["points"]
        ch = _chainage(pts)
        hits = sorted(c for d, c in (_project(pts, ch, o) for o in outlets) if d <= tol)
        tid = td.get("id", "")
        if not hits:
            out.append(Finding("error", "no_outlet", "Trench drain has no outlet (manhole or pipe)",
                               "לתעלה אין נקודת יציאה - שוחה או צינור", tid))
            continue
        if limit is None:
            continue
        worst = max([hits[0], ch[-1] - hits[-1]] + [(b - a) / 2 for a, b in zip(hits, hits[1:])])
        if worst > limit + 1e-9:
            w, m = _fmt_len(worst, spec), _fmt_len(limit, spec)
            out.append(Finding("warning", "discharge_distance",
                               f"Max flow path to outlet {w} > allowed {m}",
                               f"מרחק זרימה מרבי לנקודת יציאה {w} גדול מהמותר {m}", tid))
    return out


def _fmt_len(v, spec) -> str:
    return f"{v:.1f} m" if spec.get("units") == "m" else f"{v / 1000:.1f} m"


def _flange_std(item) -> str:
    return str((item or {}).get("flange", {}).get("std", "")).upper()


def check_flanges(spec, catalog) -> list[Finding]:
    """ASA/ANSI drilling and PN drilling in the same drawing must be confirmed compatible."""
    if not catalog:
        return []
    asa, pn = [], []
    for ref, item_id in _items_used(spec):
        std = _flange_std(catalog.get(item_id))
        if "ASA" in std or "ANSI" in std or "CLASS" in std:
            asa.append(item_id)
        elif std.startswith("PN"):
            pn.append(item_id)
    if asa and pn:
        a, p = ", ".join(sorted(set(asa))), ", ".join(sorted(set(pn)))
        return [Finding("warning", "flange_drilling", f"Flange drilling mismatch: ASA ({a}) vs PN ({p}) - verify",
                        f"אי התאמת קידוח אוגנים: ASA - {a} מול PN - {p}. לאמת", "")]
    return []


def check_buried_bolts(spec, catalog) -> list[Finding]:
    """Buried flanged joints need SS316 bolts."""
    buried = [e for e in spec.get("elements", [])
              if e.get("below_grade") and e.get("type") in ("flange", "gate_valve", "connector")]
    if not buried:
        return []
    bolts = [catalog.get(i) for _, i in _items_used(spec)
             if catalog and (catalog.get(i) or {}).get("category") == "bolt"]
    if not bolts:
        return [Finding("warning", "bolts_missing", "Buried flanged joint - specify SS316 bolts",
                        "חיבור אוגנים טמון - יש להגדיר ברגי נירוסטה 316", buried[0].get("id", ""))]
    bad = [b["id"] for b in bolts if "316" not in str(b.get("material", ""))]
    if bad:
        return [Finding("error", "bolts_material", f"Buried bolts must be SS316: {', '.join(bad)}",
                        f"ברגים טמונים חייבים להיות נירוסטה 316: {', '.join(bad)}", "")]
    return []


CHECKS = (check_catalog, check_cover, check_discharge, check_flanges, check_buried_bolts)


def run_checks(spec: dict, catalog=None) -> list[Finding]:
    findings = []
    for check in CHECKS:
        findings += check(spec, catalog)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(findings, key=lambda f: order[f.severity])
