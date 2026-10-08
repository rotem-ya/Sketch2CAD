"""IFC (e.g. exported from Revit) -> DXF plan cut, using IfcOpenShell.

For each storey the model is cut by a horizontal plane at storey elevation + cut_height: the
triangles of every element are intersected with the plane and the resulting segments go to
layers IFC_<storey>_<IfcClass>. Elements of the storey entirely below the plane (slabs, floor
pipes...) are drawn as dashed footprints. All storeys are drawn on top of each other in plan;
switch storeys by layer. Output in mm.
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
import numpy as np

SKIP_CLASSES = ("IfcOpeningElement", "IfcSpace", "IfcVirtualElement", "IfcAnnotation")
CLASS_COLORS = {"IfcWall": 7, "IfcWallStandardCase": 7, "IfcSlab": 8, "IfcColumn": 1, "IfcBeam": 1,
                "IfcDoor": 3, "IfcWindow": 4, "IfcStair": 6, "IfcRailing": 6, "IfcPipeSegment": 5,
                "IfcPipeFitting": 5, "IfcFlowSegment": 5, "IfcFlowFitting": 5, "IfcDuctSegment": 30}
ROUND = 1                      # mm, endpoint snapping when merging segments
LTSCALE = 100                  # dashes readable when plotted at about 1:100


def require_ifcopenshell():
    try:
        import ifcopenshell
        import ifcopenshell.geom
        import ifcopenshell.util.element
        import ifcopenshell.util.placement
        import ifcopenshell.util.unit
    except ImportError as exc:
        raise ImportError("Reading IFC needs IfcOpenShell (free, LGPL): pip install ifcopenshell") from exc
    return ifcopenshell


def _layer_name(storey: str | None, ifc_class: str) -> str:
    name = f"IFC_{storey}_{ifc_class}" if storey else f"IFC_{ifc_class}"
    return re.sub(r'[<>/\\":;?*|=`,]', "-", name)


def plane_cut(verts: np.ndarray, faces: np.ndarray, z: float) -> np.ndarray:
    """Segments (n, 2, 2) where the triangles cross the plane at height z."""
    d = verts[:, 2] - z
    d[np.abs(d) < 1e-9] = 1e-9                               # vertices on the plane count as above
    tri_d = d[faces]
    above = tri_d > 0
    crossing = above.any(axis=1) & ~above.all(axis=1)
    tris, tri_d, above = faces[crossing], tri_d[crossing], above[crossing]
    points = []
    for i, j in ((0, 1), (1, 2), (2, 0)):
        hit = above[:, i] != above[:, j]
        a, b = verts[tris[:, i]], verts[tris[:, j]]
        t = (tri_d[:, i] / np.where(hit, tri_d[:, i] - tri_d[:, j], 1.0))[:, None]
        p = a + (b - a) * t
        points.append(np.where(hit[:, None], p[:, :2], np.nan))
    pts = np.stack(points, axis=1)                           # (n, 3 edges, 2); exactly 2 are hits
    order = np.argsort(np.isnan(pts[:, :, 0]), axis=1, kind="stable")[:, :2]
    return np.take_along_axis(pts, order[:, :, None], axis=1)


def footprint(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Plan outline (n, 2, 2): boundary edges of the upward-facing triangles."""
    tri = verts[faces]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(normal, axis=1)
    up = faces[(norm > 0) & (normal[:, 2] > 0.5 * norm)]
    count: Counter = Counter()
    for a, b, c in np.round(verts[up][:, :, :2], 6).tolist():
        for e in ((a, b), (b, c), (c, a)):
            count[tuple(sorted(map(tuple, e)))] += 1
    edges = [e for e, n in count.items() if n == 1]
    return np.array(edges, dtype=float).reshape(-1, 2, 2)


def merge_segments(segments) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Snap endpoints, drop duplicates and join collinear segments that continue each other."""
    def snap(p) -> tuple[float, float]:
        return round(p[0] / ROUND) * ROUND, round(p[1] / ROUND) * ROUND

    segs = set()
    for p1, p2 in segments:
        a, b = snap(p1), snap(p2)
        if a != b:
            segs.add(tuple(sorted((a, b))))
    ends = defaultdict(set)
    for s in segs:
        ends[s[0]].add(s)
        ends[s[1]].add(s)
    for p in list(ends):
        if len(ends[p]) != 2:
            continue
        s1, s2 = ends[p]
        a = s1[0] if s1[1] == p else s1[1]
        b = s2[0] if s2[1] == p else s2[1]
        cross = (p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])
        if abs(cross) > ROUND * max(abs(b[0] - a[0]), abs(b[1] - a[1]), ROUND):
            continue
        new = tuple(sorted((a, b)))
        for s, q in ((s1, a), (s2, b)):
            segs.discard(s)
            ends[q].discard(s)
        segs.add(new)
        ends[a].add(new)
        ends[b].add(new)
        ends[p].clear()
    return sorted(segs)


def ifc_to_dxf(ifc: Path | str, out: Path | str, cut_height: float = 1.2, mode: str = "plan") -> Path:
    """Plan cut of an IFC model at cut_height (m) above each storey -> DXF in mm."""
    if mode != "plan":
        raise ValueError(f"unsupported mode: {mode}")
    ifcopenshell = require_ifcopenshell()
    model = ifcopenshell.open(str(ifc))
    unit = ifcopenshell.util.unit.calculate_unit_scale(model)       # file units -> m

    def elevation(storey) -> float:
        if storey.ObjectPlacement:
            return float(ifcopenshell.util.placement.get_local_placement(storey.ObjectPlacement)[2, 3]) * unit
        return float(storey.Elevation or 0) * unit

    elevations = {s.id(): elevation(s) for s in model.by_type("IfcBuildingStorey")}
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)                           # geometry comes out in metres
    iterator = ifcopenshell.geom.iterator(settings, model, os.cpu_count() or 1)

    doc = ezdxf.new("R2018", setup=True, units=4)
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = LTSCALE
    if iterator.initialize():
        while True:
            shape = iterator.get()
            element = model.by_id(shape.id)
            if not any(element.is_a(c) for c in SKIP_CLASSES):
                storey = ifcopenshell.util.element.get_container(element, ifc_class="IfcBuildingStorey")
                z = (elevations[storey.id()] if storey else 0.0) + cut_height
                _draw_element(doc, shape, _layer_name(storey.Name if storey else None, element.is_a()),
                              CLASS_COLORS.get(element.is_a(), 7), z, cut_height)
            if not iterator.next():
                break
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out)
    return Path(out)


def _draw_element(doc, shape, layer: str, color: int, z: float, cut_height: float) -> None:
    """Cut segments if the element crosses the plane at z, dashed footprint if it lies just below."""
    verts = np.array(shape.geometry.verts, dtype=float).reshape(-1, 3)
    faces = np.array(shape.geometry.faces, dtype=int).reshape(-1, 3)
    if not len(faces):
        return
    zmin, zmax = verts[:, 2].min(), verts[:, 2].max()
    if zmin < z < zmax:
        segments, attribs = plane_cut(verts, faces, z), {"layer": layer}
    elif zmax <= z and zmin >= z - cut_height - 0.5:                # this storey, below the cut
        segments, attribs = footprint(verts, faces), {"layer": layer, "linetype": "DASHED"}
    else:
        return
    if layer not in doc.layers:
        doc.layers.add(layer, color=color)
    for a, b in merge_segments(segments * 1000):
        doc.modelspace().add_line(a, b, dxfattribs=attribs)
