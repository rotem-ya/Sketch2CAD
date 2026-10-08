"""Parametric typical details (improvement 7): parameters → spec.json, rendered by sketch2cad.drawing."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .catalog import load_catalog


@dataclass
class Typical:
    name: str
    title: dict
    params: dict                       # key → {"default", "en", "he"}
    build: Callable[[dict, object], dict] = field(repr=False)


def _dim(cat, item_id, group, key, default):
    item = cat.get(item_id) or {}
    return (item.get(group) or {}).get(key, default)


def _camel(p, cat) -> dict:
    """Branch from an existing riser: valve, two LR elbows down to the buried run, transition to PE pipe."""
    od = _dim(cat, p["pipe_item"], "dims", "OD", 168.3)
    a = _dim(cat, p["elbow_item"], "dims", "A", 229)
    vl = _dim(cat, p["valve_item"], "dims", "L", 210)
    ft = _dim(cat, p["valve_item"], "flange", "t", 24)
    cl = _dim(cat, p["connector_item"], "dims", "L", 250)
    pex_od = _dim(cat, p["pe_item"], "dims", "OD", 160)
    by, cover = p["branch_height"], p["cover"]
    yb = -cover - od / 2                           # buried centreline
    xf = 300                                       # branch flange face
    xe = xf + vl + 2 * ft + 400                    # first elbow tangent point
    xv = xe + a                                    # vertical leg
    xb = xe + 2 * a                                # buried run start
    xt = xb + p["buried_run"]                      # transition flange
    xend = xt + cl + p["pe_length"]
    els = [
        {"id": "riser", "type": "pipe", "status": "existing", "points": [[0, -600], [0, by + 600]], "od": od},
        {"id": "ground", "type": "polyline", "style": "ground", "points": [[-600, 0], [xend + 300, 0]]},
        {"id": "spool", "type": "pipe", "status": "new", "item": p["pipe_item"], "points": [[0, by], [xf, by]]},
        {"id": "fl1", "type": "flange", "status": "new", "item": p["flange_item"], "at": [xf, by], "rotation": 0},
        {"id": "valve", "type": "gate_valve", "status": "new", "item": p["valve_item"],
         "at": [xf + ft + vl / 2, by], "rotation": 0, "view": "elevation"},
        {"id": "fl2", "type": "flange", "status": "new", "item": p["flange_item"],
         "at": [xf + 2 * ft + vl, by], "rotation": 0},
        {"id": "pipe1", "type": "pipe", "status": "new", "item": p["pipe_item"],
         "points": [[xf + 2 * ft + vl, by], [xe, by]]},
        {"id": "elbow1", "type": "bend", "status": "new", "item": p["elbow_item"],
         "center": [xe, by - a], "radius": a, "start_angle": 0, "end_angle": 90, "od": od},
        {"id": "pipe2", "type": "pipe", "status": "new", "item": p["pipe_item"],
         "points": [[xv, by - a], [xv, yb + a]], "below_grade": False},
        {"id": "elbow2", "type": "bend", "status": "new", "item": p["elbow_item"], "below_grade": True,
         "center": [xb, yb + a], "radius": a, "start_angle": 180, "end_angle": 270, "od": od},
        {"id": "pipe3", "type": "pipe", "status": "new", "item": p["pipe_item"], "below_grade": True,
         "points": [[xb, yb], [xt, yb]]},
        {"id": "fl3", "type": "flange", "status": "new", "item": p["flange_item"], "below_grade": True,
         "at": [xt, yb], "rotation": 0},
        {"id": "conn", "type": "connector", "status": "new", "item": p["connector_item"], "below_grade": True,
         "at": [xt + ft + cl / 2, yb], "rotation": 0, "length": cl},
        {"id": "pe", "type": "pipe", "status": "new", "item": p["pe_item"], "below_grade": True,
         "points": [[xt + ft + cl, yb], [xend, yb]], "od": pex_od},
        {"id": "sand", "type": "rect", "style": "dashed", "hatch": "sand",
         "corners": [[xv - od / 2 - 200, yb - od / 2 - 200], [xend, yb + od / 2 + 200]]},
        {"id": "w1", "type": "weld", "at": [xe, by], "axis": "v", "od": od},
        {"id": "w2", "type": "weld", "at": [xb, yb], "axis": "v", "od": od},
    ]
    legend = [
        {"no": 1, "targets": [[0, by + 400]], "balloons": [[-400, by + 700]],
         "en": "EXISTING RISER", "he": "רייזר קיים"},
        {"no": 2, "targets": [[xf + ft + vl / 2, by + 150]], "balloons": [[xf + vl / 2, by + 900]],
         "en": "GATE VALVE", "he": "מגוף", "item": p["valve_item"]},
        {"no": 3, "targets": [[xe + a * 0.7, by - a * 0.3]], "balloons": [[xe + 700, by + 500]],
         "en": "LR 90 ELBOWS", "he": "קשתות 90 רדיוס ארוך", "item": p["elbow_item"]},
        {"no": 4, "targets": [[xt + ft + cl / 2, yb]], "balloons": [[xt + 200, yb + 900]],
         "en": "FLANGE CONNECTOR STEEL / PE", "he": "מחבר אוגן פלדה לפוליאתילן", "item": p["connector_item"]},
        {"no": 5, "targets": [[xend - 300, yb]], "balloons": [[xend - 200, yb + 900]],
         "en": "PE PIPE TO NETWORK", "he": "צינור פוליאתילן לרשת", "item": p["pe_item"]},
        {"no": 6, "targets": [[xt - 200, yb - od / 2]], "balloons": [[xt - 600, yb - 700]],
         "en": "SS316 BOLTS + EPDM GASKET", "he": "ברגי נירוסטה 316 ואטם EPDM", "item": p["bolt_item"]},
    ]
    return {
        "version": 1, "units": "mm", "scale": p["scale"], "paper": "A3", "language": p["language"],
        "view": "elevation", "sheet": {"template": "a3_legend", "fields": {
            "title_en": "TYPICAL - CONNECTION TO EXISTING RISER", "title_he": "פרט טיפוסי - התחברות לרייזר קיים"}},
        "elements": els,
        "dimensions": [
            {"type": "chain", "axis": "v", "base": xend + 250, "points": [[xend, 0], [xend, yb + od / 2]],
             "texts": ["COVER <>"]},
            {"type": "chain", "axis": "h", "base": by + 450,
             "points": [[xf, by], [xf + 2 * ft + vl, by]], "texts": [""]},
        ],
        "legend": legend,
        "notes": {"en": [f"MIN COVER {cover:g} mm TO PIPE CROWN, SAND 200 mm ALL ROUND.",
                         "BURIED STEEL: 3 LAYERS VINYL TAPE. THRUST BLOCK AT BURIED ELBOW."],
                  "he": [f"כיסוי מינימלי {cover:g} מ\"מ מעל קודקוד הצינור, ריפוד חול 200 מ\"מ מסביב.",
                         "פלדה טמונה: 3 שכבות סרט ויניל. גוש עיגון בקשת הטמונה."]},
        "params": {"cover_min": p["cover_min"], "grade_y": 0},
    }


def _trench_mh(p, cat) -> dict:
    """Plan: trench drain run discharging straight into a manhole with a grated (or closed) cover."""
    do = _dim(cat, p["manhole_item"], "dims", "Do", 1550) / 1000
    run, off = p["run_length"], p["offset"]
    x_td = do / 2 + off
    cover_item = "grate_e600" if p["cover"] == "grate" else "cover_closed_d400"
    return {
        "version": 1, "units": "m", "scale": p["scale"], "paper": "A3", "language": p["language"],
        "view": "plan", "sheet": {"template": "a3_legend", "fields": {
            "title_en": "TYPICAL - TRENCH DRAIN INTO MANHOLE", "title_he": "פרט טיפוסי - חיבור תעלה לשוחה"}},
        "elements": [
            {"id": "mh", "type": "manhole", "status": "existing", "item": p["manhole_item"], "at": [0, 0],
             "cover": p["cover"]},
            {"id": "cover", "type": "text", "at": [-do / 2, -do / 2 - 0.8], "height": 2.5,
             "text": {"en": "MH COVER", "he": "מכסה שוחה"}, "item": cover_item, "status": "proposed"},
            {"id": "td", "type": "trench_drain", "status": "existing", "item": p["trench_item"],
             "points": [[x_td, run], [x_td, 0.6]], "width": 0.32},
            {"id": "conn", "type": "pipe", "status": "proposed", "item": p["pipe_item"],
             "points": [[x_td, 0.6], [x_td, 0], [do / 2, 0]], "od": 0.2},
            {"id": "flow", "type": "flow_arrow", "at": [x_td + 0.6, run / 2], "angle": 270, "length": 1.5},
            {"id": "north", "type": "north_arrow", "at": [x_td + 4, run - 1], "size": 1.5},
        ],
        "dimensions": [{"type": "chain", "axis": "h", "base": -2.0, "points": [[0, 0], [x_td, 0]],
                        "texts": [""]}],
        "legend": [
            {"no": 1, "targets": [[0, do / 2]], "balloons": [[-2.5, 2.0]],
             "en": "EXISTING MANHOLE", "he": "שוחה קיימת", "item": p["manhole_item"]},
            {"no": 2, "targets": [[0.2, 0.2]], "balloons": [[-2.5, -2.5]],
             "en": "GRATED COVER" if p["cover"] == "grate" else "CLOSED COVER",
             "he": "מכסה רשת" if p["cover"] == "grate" else "מכסה אטום", "item": cover_item},
            {"no": 3, "targets": [[x_td, run * 0.6]], "balloons": [[x_td + 3, run * 0.7]],
             "en": "TRENCH DRAIN", "he": "תעלת ניקוז", "item": p["trench_item"]},
            {"no": 4, "targets": [[(do / 2 + x_td) / 2, 0]], "balloons": [[x_td + 3, -1.5]],
             "en": "PROPOSED PIPE INTO MANHOLE WALL", "he": "מוצע: צינור לדופן השוחה", "item": p["pipe_item"]},
        ],
        "notes": {"en": ["CORE-DRILL MANHOLE WALL, RUBBER SEAL; RESTORE BENCHING."],
                  "he": ["קידוח ליבה בדופן השוחה ואטם גומי, שיקום מתעל בתחתית השוחה."]},
        "params": {"max_discharge_distance": p["max_discharge_distance"]},
    }


def _valve_line(p, cat) -> dict:
    """Plan: pipe – flange – gate valve – flange – pipe, with face-to-face dimensions."""
    od = _dim(cat, p["pipe_item"], "dims", "OD", 168.3)
    vl = _dim(cat, p["valve_item"], "dims", "L", 210)
    ft = _dim(cat, p["valve_item"], "flange", "t", 24)
    lp = p["pipe_length"]
    x1, x2 = lp, lp + 2 * ft + vl
    return {
        "version": 1, "units": "mm", "scale": p["scale"], "paper": "A3", "language": p["language"],
        "view": "plan", "sheet": {"template": "a3_simple", "fields": {
            "title_en": "TYPICAL - FLANGED GATE VALVE IN LINE", "title_he": "פרט טיפוסי - מגוף מאוגן בקו"}},
        "elements": [
            {"id": "p1", "type": "pipe", "status": "new", "item": p["pipe_item"], "points": [[0, 0], [x1, 0]]},
            {"id": "f1", "type": "flange", "status": "new", "item": p["flange_item"], "at": [x1, 0], "rotation": 0},
            {"id": "v", "type": "gate_valve", "status": "new", "item": p["valve_item"], "at": [x1 + ft + vl / 2, 0],
             "rotation": 0, "view": "plan"},
            {"id": "f2", "type": "flange", "status": "new", "item": p["flange_item"], "at": [x2, 0], "rotation": 0},
            {"id": "p2", "type": "pipe", "status": "new", "item": p["pipe_item"], "points": [[x2, 0], [x2 + lp, 0]]},
            {"id": "flow", "type": "flow_arrow", "at": [lp / 2, od * 2], "angle": 0, "length": 300},
        ],
        "dimensions": [{"type": "chain", "axis": "h", "base": -od * 3,
                        "points": [[0, 0], [x1, 0], [x2, 0], [x2 + lp, 0]], "texts": ["", "", ""]}],
        "legend": [
            {"no": 1, "targets": [[lp / 2, od / 2]], "balloons": [[lp / 2, od * 3]],
             "en": "PIPE", "he": "צינור", "item": p["pipe_item"]},
            {"no": 2, "targets": [[x1 + ft + vl / 2, 0]], "balloons": [[x1 + ft + vl / 2, od * 4]],
             "en": "GATE VALVE", "he": "מגוף", "item": p["valve_item"]},
            {"no": 3, "targets": [[x2, od]], "balloons": [[x2 + lp / 2, od * 3]],
             "en": "FLANGES", "he": "אוגנים", "item": p["flange_item"]},
        ],
        "notes": {"en": [], "he": []},
        "params": {},
    }


def _p(default, en, he):
    return {"default": default, "en": en, "he": he}


_COMMON = {"scale": _p(20, "Scale 1:", "קנה מידה 1:"), "language": _p("both", "Language", "שפה")}

TYPICALS: dict[str, Typical] = {t.name: t for t in (
    Typical("riser_connection", {"en": "Connection to existing riser (camel)", "he": "התחברות לרייזר קיים - גמל"}, {
        **_COMMON, "scale": _p(15, "Scale 1:", "קנה מידה 1:"),
        "branch_height": _p(600, "Branch height above grade (mm)", "גובה ההסתעפות מעל הקרקע - מ\"מ"),
        "cover": _p(1200, "Cover to crown (mm)", "כיסוי לקודקוד - מ\"מ"),
        "cover_min": _p(1200, "Minimum cover (mm)", "כיסוי מינימלי - מ\"מ"),
        "buried_run": _p(800, "Buried steel run (mm)", "אורך פלדה טמונה - מ\"מ"),
        "pe_length": _p(1000, "PE pipe drawn length (mm)", "אורך צינור PE בשרטוט - מ\"מ"),
        "pipe_item": _p("steel_pipe_6in_sch40", "Steel pipe item", "פריט צינור פלדה"),
        "valve_item": _p("avk_0661_dn150", "Valve item", "פריט מגוף"),
        "flange_item": _p("flange_6in_pn16", "Flange item", "פריט אוגן"),
        "elbow_item": _p("elbow_6in_lr90", "Elbow item", "פריט קשת"),
        "connector_item": _p("golan_pex5081_160", "Connector item", "פריט מחבר"),
        "pe_item": _p("pexgol_160_c10", "PE pipe item", "פריט צינור PE"),
        "bolt_item": _p("bolt_m20_ss316", "Bolt item", "פריט ברגים"),
    }, _camel),
    Typical("trench_to_manhole", {"en": "Trench drain into manhole", "he": "חיבור תעלת ניקוז לשוחה"}, {
        **_COMMON, "scale": _p(100, "Scale 1:", "קנה מידה 1:"),
        "run_length": _p(10.0, "Trench run length (m)", "אורך התעלה - מ'"),
        "offset": _p(1.5, "Trench offset from manhole (m)", "מרחק התעלה מהשוחה - מ'"),
        "cover": _p("grate", "Manhole cover (grate/closed)", "מכסה שוחה - grate/closed"),
        "max_discharge_distance": _p(20.0, "Max flow path to outlet (m)", "מרחק זרימה מרבי - מ'"),
        "manhole_item": _p("manhole_conc_125", "Manhole item", "פריט שוחה"),
        "trench_item": _p("trench_drain_generic", "Trench drain item", "פריט תעלה"),
        "pipe_item": _p("pipe_pvc_200", "Connection pipe item", "פריט צינור חיבור"),
    }, _trench_mh),
    Typical("valve_in_line", {"en": "Flanged gate valve in line", "he": "מגוף מאוגן בקו"}, {
        **_COMMON, "pipe_length": _p(600, "Pipe length each side (mm)", "אורך צינור מכל צד - מ\"מ"),
        "pipe_item": _p("steel_pipe_6in_sch40", "Pipe item", "פריט צינור"),
        "valve_item": _p("avk_0661_dn150", "Valve item", "פריט מגוף"),
        "flange_item": _p("flange_6in_pn16", "Flange item", "פריט אוגן"),
    }, _valve_line),
)}


def list_typicals() -> list[Typical]:
    return list(TYPICALS.values())


def generate(name: str, params: dict | None = None, catalog=None) -> dict:
    """Build a spec from a typical; missing params take their defaults."""
    typ = TYPICALS[name]
    values = {k: v["default"] for k, v in typ.params.items()} | (params or {})
    return typ.build(values, catalog if catalog is not None else load_catalog())
