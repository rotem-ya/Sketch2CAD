"""Sketch2CAD spec -> IFC4, using IfcOpenShell (LGPL-3.0).

Walls, openings (and the door or window that fills them), floors and chain
dimensions become IFC entities so the drawing can go to an open BIM chain
(quantities, as-made edits, another tool) without Revit. Geometry passed to
IfcOpenShell is in metres. The file's length unit is the spec's unit (mm or m);
length quantities use that unit, and area and volume quantities are m² and m³.
Only the root `elements` and `dimensions`
are the model: a view is another drawing of the sheet, not a second building.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

from .ifc2dxf import require_ifcopenshell

_TO_M = {"mm": 0.001, "m": 1.0}
_WALL_HEIGHT_MM = 3000.0
_WALL_THICKNESS_MM = 200.0
_OPENING_WIDTH_MM = 900.0
_OPENING_HEIGHT_MM = 2100.0
_FLOOR_THICKNESS_MM = 200.0
_VOID_CLEARANCE_M = 0.05
_SLAB_TYPES = {"FLOOR", "ROOF", "LANDING", "BASESLAB"}


def validate_ifc(path: Path | str) -> dict:
    """Reopen an IFC file and report its schema, length unit and entity counts."""
    ifcopenshell = require_ifcopenshell()
    import ifcopenshell.util.unit

    model = ifcopenshell.open(str(path))
    length = None
    for unit in model.by_type("IfcSIUnit"):
        if unit.UnitType == "LENGTHUNIT":
            length = {"prefix": str(unit.Prefix), "name": str(unit.Name)}
    classes = (
        "IfcProject", "IfcSite", "IfcBuilding", "IfcBuildingStorey",
        "IfcWall", "IfcOpeningElement", "IfcDoor", "IfcWindow", "IfcSlab",
        "IfcAnnotation", "IfcRelVoidsElement", "IfcRelFillsElement",
        "IfcElementQuantity", "IfcPropertySet",
    )
    return {
        "schema": model.schema,
        "unit_scale_m": float(ifcopenshell.util.unit.calculate_unit_scale(model)),
        "length_unit": length,
        "counts": {name: len(model.by_type(name)) for name in classes},
    }


def spec_to_ifc(spec: dict, out: Path | str, *, project_name: str | None = None,
                warnings: list[str] | None = None) -> Path:
    """Write `spec` to an IFC4 file. Problems are collected in `warnings` and do not stop the file."""
    warnings = warnings if warnings is not None else []
    ifcopenshell = require_ifcopenshell()
    import ifcopenshell.api.aggregate
    import ifcopenshell.api.context
    import ifcopenshell.api.feature
    import ifcopenshell.api.geometry
    import ifcopenshell.api.project
    import ifcopenshell.api.pset
    import ifcopenshell.api.root
    import ifcopenshell.api.spatial
    import ifcopenshell.api.unit
    import ifcopenshell.util.unit

    api = ifcopenshell.api
    units = spec.get("units") or "mm"
    if units not in _TO_M:
        warnings.append(f"IFC: units {units!r} are not mm or m - using mm")
        units = "mm"
    to_m = _TO_M[units]

    model = api.project.create_file(version="IFC4")
    title = project_name or _project_title(spec)
    project = api.root.create_entity(model, ifc_class="IfcProject", name=title)
    _assign_units(api, model, units)
    unit_scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    body_ctx = api.context.add_context(model, context_type="Model")
    body = api.context.add_context(model, context_type="Model", context_identifier="Body",
                                   target_view="MODEL_VIEW", parent=body_ctx)
    plan = api.context.add_context(model, context_type="Plan")
    annotation = api.context.add_context(model, context_type="Plan", context_identifier="Annotation",
                                         target_view="PLAN_VIEW", parent=plan)

    site = api.root.create_entity(model, ifc_class="IfcSite", name="Site")
    building = api.root.create_entity(model, ifc_class="IfcBuilding", name=title)
    api.aggregate.assign_object(model, relating_object=project, products=[site])
    api.aggregate.assign_object(model, relating_object=site, products=[building])

    elements = [el for el in (spec.get("elements") or []) if isinstance(el, dict)]
    storey_z = _storey_elevations(elements, to_m)
    if not storey_z:
        storey_z["Ground Floor"] = 0.0
    storeys = {}
    for name, elevation in storey_z.items():
        storey = api.root.create_entity(model, ifc_class="IfcBuildingStorey", name=name)
        api.aggregate.assign_object(model, relating_object=building, products=[storey])
        matrix = np.eye(4)
        matrix[2, 3] = elevation
        api.geometry.edit_object_placement(model, product=storey, matrix=matrix)
        storey.Elevation = _project(elevation, unit_scale)
        storeys[name] = storey

    ctx = _Build(model, api, body, annotation, storeys, storey_z, to_m, unit_scale, warnings)
    runs: list[_WallRun] = []
    for el in elements:
        kind = el.get("type")
        if kind == "wall":
            runs.extend(_add_wall(ctx, el))
        elif kind == "floor":
            _add_floor(ctx, el)
    by_id: dict[str, list[_WallRun]] = defaultdict(list)
    for run in runs:
        by_id[run.spec_id].append(run)
    for el in elements:
        if el.get("type") == "opening":
            _add_opening(ctx, el, by_id)
    for run in runs:
        _wall_quantities(ctx, run)
    for index, dim in enumerate(spec.get("dimensions") or []):
        if isinstance(dim, dict):
            _add_dimension(ctx, dim, index)

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(path))
    return path


class _Build:
    def __init__(self, model, api, body, annotation, storeys, storey_z, to_m, unit_scale, warnings):
        self.model = model
        self.api = api
        self.body = body
        self.annotation = annotation
        self.storeys = storeys
        self.storey_z = storey_z
        self.to_m = to_m
        self.unit_scale = unit_scale
        self.warnings = warnings

    def metres(self, value, default_mm: float) -> float:
        if value is None:
            return default_mm * 0.001
        return float(value) * self.to_m

    def project(self, metres: float, power: int = 1) -> float:
        return metres / (self.unit_scale ** power)


class _WallRun:
    def __init__(self, spec_id, product, p1, p2, z, height, thickness):
        self.spec_id = spec_id
        self.product = product
        self.p1 = p1
        self.p2 = p2
        self.z = z
        self.height = height
        self.thickness = thickness
        self.openings: list[tuple[float, float]] = []  # width, height in metres

    @property
    def length(self) -> float:
        return float(np.linalg.norm(self.p2 - self.p1))


def _project_title(spec: dict) -> str:
    fields = (spec.get("sheet") or {}).get("fields") or {}
    return str(fields.get("title_en") or fields.get("title_he") or "Sketch2CAD")


def _assign_units(api, model, units: str) -> None:
    """Length follows the drawing. Area and volume stay in m² and m³ (a prefix on a square unit is ambiguous)."""
    length = {"is_metric": True, "raw": "METERS" if units == "m" else "MILLIMETERS"}
    metric = {"is_metric": True, "raw": "METERS"}
    api.unit.assign_unit(model, length=length, area=metric, volume=metric)


def _project(metres: float, unit_scale: float, power: int = 1) -> float:
    return metres / (unit_scale ** power)


def _storey_name(el: dict) -> str:
    return str(el.get("storey") or "Ground Floor")


def _storey_elevations(elements: list[dict], to_m: float) -> dict[str, float]:
    """Datum (m) of each storey: the lowest wall base or floor top named on it.

    A floor's body hangs below this datum by its thickness, so the top stays the walking surface.
    """
    found: dict[str, float] = {}
    for el in elements:
        if el.get("type") not in ("wall", "floor"):
            continue
        name = _storey_name(el)
        z = float(el.get("z") or 0) * to_m
        found[name] = z if name not in found else min(found[name], z)
    return found


def _xy(points) -> list[np.ndarray]:
    out = []
    for point in points or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append(np.array([float(point[0]), float(point[1])], dtype=float))
    return out


def _axis_matrix(origin_xy: np.ndarray, direction: np.ndarray, z: float) -> np.ndarray:
    """Placement whose local X follows `direction` and whose local Y is to the left."""
    matrix = np.eye(4)
    matrix[0, 0], matrix[1, 0] = direction[0], direction[1]
    matrix[0, 1], matrix[1, 1] = -direction[1], direction[0]
    matrix[0, 3], matrix[1, 3], matrix[2, 3] = float(origin_xy[0]), float(origin_xy[1]), float(z)
    return matrix


def _trace(ctx: _Build, product, element: dict) -> None:
    pset = ctx.api.pset.add_pset(ctx.model, product=product, name="Sketch2CAD")
    ctx.api.pset.edit_pset(ctx.model, pset=pset, properties={
        "ElementId": str(element.get("id") or ""),
        "Status": str(element.get("status") or ""),
        "SpecType": str(element.get("type") or ""),
    })


def _qto(ctx: _Build, product, name: str, quantities: dict[str, tuple[float, str]]) -> None:
    """`quantities` maps a Qto name to (SI value, 'length' | 'area' | 'volume').

    Length is written in the project length unit. Area and volume stay in m² and m³.
    """
    values = {}
    for key, (value, kind) in quantities.items():
        values[key] = ctx.project(value) if kind == "length" else value
    qto = ctx.api.pset.add_qto(ctx.model, product=product, name=name)
    ctx.api.pset.edit_qto(ctx.model, qto=qto, properties=values)


def _contain(ctx: _Build, product, storey_name: str) -> None:
    ctx.api.spatial.assign_container(ctx.model, relating_structure=ctx.storeys[storey_name], products=[product])


def _add_wall(ctx: _Build, el: dict) -> list[_WallRun]:
    pts = _xy(el.get("points"))
    if len(pts) < 2:
        ctx.warnings.append(f"IFC: wall {el.get('id', '?')!r} needs at least two points - skipped")
        return []
    height = ctx.metres(el.get("height"), _WALL_HEIGHT_MM)
    thickness = ctx.metres(el.get("thickness"), _WALL_THICKNESS_MM)
    if height <= 0 or thickness <= 0:
        ctx.warnings.append(f"IFC: wall {el.get('id', '?')!r} has no positive height and thickness - skipped")
        return []
    base = float(el.get("z") or 0) * ctx.to_m
    storey = _storey_name(el)
    runs = []
    for index, (p1, p2) in enumerate(zip(pts, pts[1:])):
        p1 = p1 * ctx.to_m
        p2 = p2 * ctx.to_m
        direction = p2 - p1
        length = float(np.linalg.norm(direction))
        if length < 1e-9:
            ctx.warnings.append(f"IFC: wall {el.get('id', '?')!r} has a zero-length segment - skipped")
            continue
        direction = direction / length
        name = str(el.get("id") or "wall")
        if len(pts) > 2:
            name = f"{name}/{index + 1}"
        wall = ctx.api.root.create_entity(ctx.model, ifc_class="IfcWall", name=name)
        rep = ctx.api.geometry.add_wall_representation(
            ctx.model, context=ctx.body, length=length, height=height, thickness=thickness,
            offset=-thickness / 2)
        ctx.api.geometry.assign_representation(ctx.model, product=wall, representation=rep)
        _contain(ctx, wall, storey)
        ctx.api.geometry.edit_object_placement(
            ctx.model, product=wall, matrix=_axis_matrix(p1, direction, base))
        _trace(ctx, wall, el)
        runs.append(_WallRun(str(el.get("id") or name), wall, p1, p2, base, height, thickness))
    return runs


def _wall_quantities(ctx: _Build, run: _WallRun) -> None:
    gross_area = run.length * run.height
    opening_area = sum(width * height for width, height in run.openings)
    net_area = max(gross_area - opening_area, 0.0)
    gross_volume = gross_area * run.thickness
    net_volume = max(gross_volume - opening_area * run.thickness, 0.0)
    _qto(ctx, run.product, "Qto_WallBaseQuantities", {
        "Length": (run.length, "length"),
        "Width": (run.thickness, "length"),
        "Height": (run.height, "length"),
        "GrossSideArea": (gross_area, "area"),
        "NetSideArea": (net_area, "area"),
        "GrossVolume": (gross_volume, "volume"),
        "NetVolume": (net_volume, "volume"),
    })


def _add_floor(ctx: _Build, el: dict) -> None:
    pts = _xy(el.get("points"))
    if len(pts) < 3 and el.get("corners"):
        corners = _xy(el.get("corners"))
        if len(corners) == 2:
            (x0, y0), (x1, y1) = corners
            pts = [np.array(p, dtype=float) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    if len(pts) < 3:
        ctx.warnings.append(f"IFC: floor {el.get('id', '?')!r} needs an outline - skipped")
        return
    thickness = ctx.metres(el.get("thickness"), _FLOOR_THICKNESS_MM)
    if thickness <= 0:
        ctx.warnings.append(f"IFC: floor {el.get('id', '?')!r} has no positive thickness - skipped")
        return
    top = float(el.get("z") or 0) * ctx.to_m
    kind = str(el.get("predefined_type") or "FLOOR").upper()
    if kind not in _SLAB_TYPES:
        ctx.warnings.append(f"IFC: floor {el.get('id', '?')!r} predefined type {kind!r} - using FLOOR")
        kind = "FLOOR"
    slab = ctx.api.root.create_entity(ctx.model, ifc_class="IfcSlab", name=str(el.get("id") or "floor"),
                                      predefined_type=kind)
    polyline = [(float(p[0]) * ctx.to_m, float(p[1]) * ctx.to_m) for p in pts]
    rep = ctx.api.geometry.add_slab_representation(ctx.model, context=ctx.body, depth=thickness, polyline=polyline)
    ctx.api.geometry.assign_representation(ctx.model, product=slab, representation=rep)
    storey = _storey_name(el)
    _contain(ctx, slab, storey)
    matrix = np.eye(4)
    matrix[2, 3] = top - thickness  # extrusion grows upward; z is the walking surface
    ctx.api.geometry.edit_object_placement(ctx.model, product=slab, matrix=matrix)
    _trace(ctx, slab, el)
    area = _polygon_area(polyline)
    perimeter = _perimeter(polyline)
    _qto(ctx, slab, "Qto_SlabBaseQuantities", {
        "Depth": (thickness, "length"),
        "GrossArea": (area, "area"),
        "NetArea": (area, "area"),
        "GrossVolume": (area * thickness, "volume"),
        "NetVolume": (area * thickness, "volume"),
        "Perimeter": (perimeter, "length"),
    })


def _polygon_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def _perimeter(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += float(np.hypot(x2 - x1, y2 - y1))
    return total


def _add_opening(ctx: _Build, el: dict, walls: dict[str, list[_WallRun]]) -> None:
    host_id = str(el.get("host") or "")
    runs = walls.get(host_id) or []
    width = ctx.metres(el.get("width"), _OPENING_WIDTH_MM)
    height = ctx.metres(el.get("height"), _OPENING_HEIGHT_MM)
    sill = ctx.metres(el.get("sill"), 0.0)
    if width <= 0 or height <= 0:
        ctx.warnings.append(f"IFC: opening {el.get('id', '?')!r} has no positive size - skipped")
        return
    placed = _place_opening(el, runs, ctx.to_m, width)
    if placed is None:
        if runs:
            ctx.warnings.append(f"IFC: opening {el.get('id', '?')!r} is not on its host wall - created without a void")
        else:
            ctx.warnings.append(f"IFC: opening {el.get('id', '?')!r} has no host wall - created without a void")
        direction = np.array([1.0, 0.0])
        origin = _xy([el.get("at")])
        origin_xy = origin[0] * ctx.to_m if origin else np.zeros(2)
        base = float(el.get("z") or 0) * ctx.to_m + sill
        thickness = ctx.metres(el.get("thickness"), _WALL_THICKNESS_MM)
        storey = _storey_name(el)
        run = None
    else:
        run, origin_xy, direction, base, thickness, storey = placed
        if run is not None:
            run.openings.append((width, height))
    kind = str(el.get("kind") or "opening").lower()
    if kind not in ("opening", "door", "window"):
        ctx.warnings.append(f"IFC: opening {el.get('id', '?')!r} kind {kind!r} - using opening")
        kind = "opening"
    void_thickness = thickness + _VOID_CLEARANCE_M
    opening = ctx.api.root.create_entity(ctx.model, ifc_class="IfcOpeningElement",
                                         name=str(el.get("id") or "opening"), predefined_type="OPENING")
    rep = ctx.api.geometry.add_wall_representation(
        ctx.model, context=ctx.body, length=width, height=height, thickness=void_thickness,
        offset=-void_thickness / 2)
    ctx.api.geometry.assign_representation(ctx.model, product=opening, representation=rep)
    if run is not None:
        ctx.api.feature.add_feature(ctx.model, feature=opening, element=run.product)
    else:
        if storey not in ctx.storeys:
            fallback = "Ground Floor" if "Ground Floor" in ctx.storeys else next(iter(ctx.storeys))
            ctx.warnings.append(
                f"IFC: opening {el.get('id', '?')!r} storey {storey!r} does not exist - placed on {fallback!r}")
            storey = fallback
        _contain(ctx, opening, storey)
    ctx.api.geometry.edit_object_placement(
        ctx.model, product=opening, matrix=_axis_matrix(origin_xy, direction, base))
    _trace(ctx, opening, el)
    _qto(ctx, opening, "Qto_OpeningElementBaseQuantities", {
        "Width": (width, "length"),
        "Height": (height, "length"),
        "Depth": (thickness, "length"),
        "Area": (width * height, "area"),
    })
    if kind in ("door", "window"):
        _add_filling(ctx, el, opening, kind, origin_xy, direction, base, width, height, thickness, storey)


def _place_opening(el, runs: list[_WallRun], to_m: float, width: float):
    if not runs:
        return None
    if el.get("at") is not None:
        at = _xy([el.get("at")])
        if not at:
            return None
        target = at[0] * to_m
        best = None
        for run in runs:
            direction = run.p2 - run.p1
            length = run.length
            if length < 1e-9:
                continue
            direction = direction / length
            along = float(np.dot(target - run.p1, direction))
            across = float(np.linalg.norm((target - run.p1) - direction * along))
            if -width / 2 - 1e-6 <= along <= length + width / 2 + 1e-6 and across <= run.thickness + 1e-6:
                rank = (across, 0 if 0 <= along <= length else 1)
                if best is None or rank < best[0]:
                    best = (rank, run, along, direction)
        if best is None:
            return None
        _, run, along, direction = best
    else:
        run = runs[0]
        direction = (run.p2 - run.p1) / run.length
        along = float(el.get("along") or 0) * to_m
    origin = run.p1 + direction * along - direction * (width / 2)
    storey = _storey_name(el) if el.get("storey") else _storey_of_wall(run)
    return run, origin, direction, run.z + float(el.get("sill") or 0) * to_m, run.thickness, storey


def _storey_of_wall(run: _WallRun) -> str:
    container = None
    for rel in run.product.ContainedInStructure or []:
        container = rel.RelatingStructure
    return str(container.Name) if container is not None else "Ground Floor"


def _add_filling(ctx, el, opening, kind: str, origin, direction, base, width, height, thickness, storey) -> None:
    ifc_class = "IfcDoor" if kind == "door" else "IfcWindow"
    predefined = "DOOR" if kind == "door" else "WINDOW"
    filling = ctx.api.root.create_entity(ctx.model, ifc_class=ifc_class, name=str(el.get("id") or kind),
                                         predefined_type=predefined)
    filling.OverallWidth = ctx.project(width)
    filling.OverallHeight = ctx.project(height)
    try:
        if kind == "door":
            rep = ctx.api.geometry.add_door_representation(
                ctx.model, context=ctx.body, overall_height=height, overall_width=width)
        else:
            rep = ctx.api.geometry.add_window_representation(
                ctx.model, context=ctx.body, overall_height=height, overall_width=width)
        ctx.api.geometry.assign_representation(ctx.model, product=filling, representation=rep)
    except Exception as exc:  # a filling without a mesh still carries the relation and the size
        ctx.warnings.append(f"IFC: {kind} {el.get('id', '?')!r} has no mesh ({exc})")
    ctx.api.feature.add_filling(ctx.model, opening=opening, element=filling)
    if storey in ctx.storeys:
        _contain(ctx, filling, storey)
    ctx.api.geometry.edit_object_placement(
        ctx.model, product=filling, matrix=_axis_matrix(origin, direction, base))
    _trace(ctx, filling, el)
    qto_name = "Qto_DoorBaseQuantities" if kind == "door" else "Qto_WindowBaseQuantities"
    _qto(ctx, filling, qto_name, {
        "Width": (width, "length"),
        "Height": (height, "length"),
        "Area": (width * height, "area"),
    })


def _add_dimension(ctx: _Build, dim: dict, index: int) -> None:
    if dim.get("type") not in (None, "chain"):
        ctx.warnings.append(f"IFC: dimension {index} type {dim.get('type')!r} is not a chain - skipped")
        return
    pts = _xy(dim.get("points"))
    if len(pts) < 2:
        ctx.warnings.append(f"IFC: dimension {index} needs at least two points - skipped")
        return
    axis = dim.get("axis") or "h"
    if axis not in ("h", "v"):
        ctx.warnings.append(f"IFC: dimension {index} axis {axis!r} - using h")
        axis = "h"
    base = float(dim.get("base") or 0) * ctx.to_m
    texts = dim.get("texts") or []
    storey_name = str(dim.get("storey") or "Ground Floor")
    if storey_name not in ctx.storeys:
        storey_name = next(iter(ctx.storeys))
    storey_z = ctx.storey_z.get(storey_name, 0.0)
    # World coordinates: the annotation keeps an identity placement, so a storey above ground
    # must not drop to Z=0. A dimension without its own z sits at its storey datum.
    z = float(dim["z"]) * ctx.to_m if dim.get("z") is not None else storey_z
    for segment, (a, b) in enumerate(zip(pts, pts[1:])):
        a = a * ctx.to_m
        b = b * ctx.to_m
        if axis == "h":
            p1, p2 = (float(a[0]), base, z), (float(b[0]), base, z)
            measured = abs(float(b[0] - a[0]))
        else:
            p1, p2 = (base, float(a[1]), z), (base, float(b[1]), z)
            measured = abs(float(b[1] - a[1]))
        if measured < 1e-9:
            continue
        name = str(dim.get("id") or f"DIM-{index + 1}")
        ann = ctx.api.root.create_entity(ctx.model, ifc_class="IfcAnnotation", name=f"{name}/{segment + 1}")
        ann.ObjectType = "DIMENSION"
        local = [(x, y, zz) for x, y, zz in (p1, p2)]
        curve = ctx.model.createIfcPolyline([
            ctx.model.createIfcCartesianPoint(tuple(ctx.project(c) for c in point)) for point in local
        ])
        rep = ctx.model.createIfcShapeRepresentation(ctx.annotation, "Annotation", "Curve3D", [curve])
        ctx.api.geometry.assign_representation(ctx.model, product=ann, representation=rep)
        _contain(ctx, ann, storey_name)
        ctx.api.geometry.edit_object_placement(ctx.model, product=ann)
        text = texts[segment] if segment < len(texts) else ""
        pset = ctx.api.pset.add_pset(ctx.model, product=ann, name="Sketch2CAD_Dimension")
        ctx.api.pset.edit_pset(ctx.model, pset=pset, properties={
            "MeasuredLength": ctx.project(measured),
            "Axis": axis,
            "Text": str(text or ""),
        })
