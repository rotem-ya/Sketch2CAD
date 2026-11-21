"""Prototype bridge to Pascal Editor (MIT, https://github.com/pascalorg/editor).

Pascal is a browser building editor: a scan can sit under editable walls, doors,
windows, slabs and ceilings, and the same model can leave as IFC. This module does
not start Pascal and does not call its network. It writes the scene graph Pascal's
own schemas describe (metres; plan axes are Pascal's x and z), plus the IFC file
from ``spec_to_ifc``, and a connection file whose commands are the public CLI.
No API key, bearer token or password is written.

A scan path is recorded only as a path. ``scan.url`` is set when the string is one
Pascal's asset allowlist accepts (``asset://``, ``artifact://``, ``blob:``,
``data:image/``, an app-relative ``/path``, ``https://``, or ``http://localhost``).
``file:`` and ``javascript:`` are refused.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from .spec2ifc import spec_to_ifc

PASCAL_REPO = "https://github.com/pascalorg/editor"
PASCAL_LICENSE = "MIT"
# Public commands from Pascal's local-editor guide. Nothing here is a secret.
EDITOR_COMMAND = ["npx", "@pascal-app/cli", "editor", "--no-open"]
MCP_COMMAND = ["npx", "--yes", "--package=@pascal-app/cli", "pascal", "mcp", "connect"]

_TO_M = {"mm": 0.001, "m": 1.0}
_WALL_HEIGHT_MM = 3000.0
_WALL_THICKNESS_MM = 200.0
_OPENING_WIDTH_MM = 900.0
_OPENING_HEIGHT_MM = 2100.0
_FLOOR_THICKNESS_MM = 200.0
_CEILING_THICKNESS_MM = 20.0


def scan_url_allowed(url: str) -> bool:
    """True when Pascal's asset allowlist would accept `url` as a scan reference."""
    if not isinstance(url, str):
        return False
    text = url.strip()
    if not text or text.startswith(("javascript:", "file:", "ftp:", "data:text")):
        return False
    if text.startswith(("//", "\\\\")):
        return False
    if text.startswith("asset://"):
        return True
    if text.startswith("artifact://"):
        digest = text[len("artifact://"):]
        return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    if text.startswith("blob:") or text.startswith("data:image/"):
        return True
    if text.startswith("/") and not text.startswith("//"):
        return True
    parsed = urlparse(text)
    if parsed.username or parsed.password:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return True
    return False


def scene_from_spec(spec: dict, *, scan=None) -> tuple[dict, list[str]]:
    """Build a Pascal scene (metres) from a Sketch2CAD spec. Returns (scene, warnings)."""
    warnings: list[str] = []
    units = spec.get("units") or "mm"
    if units not in _TO_M:
        warnings.append(f"Pascal: units {units!r} are not mm or m - using mm")
        units = "mm"
    to_m = _TO_M[units]
    elements = [el for el in (spec.get("elements") or []) if isinstance(el, dict)]
    ids = _Ids()
    levels = _levels(elements, to_m, ids)
    nodes: dict[str, dict] = {}
    site_id = ids.take("site", "sketch2cad", "site")
    building_id = ids.take("building", "sketch2cad", "building")
    title = _title(spec)
    nodes[site_id] = {
        "object": "node", "id": site_id, "type": "site", "name": title,
        "parentId": None, "visible": True,
        "metadata": {"sketch2cad": {"units": units, "title": title}},
    }
    level_ids = [level["id"] for level in levels]
    nodes[building_id] = {
        "object": "node", "id": building_id, "type": "building", "name": title,
        "parentId": site_id, "visible": True, "children": level_ids,
        "position": [0, 0, 0], "rotation": [0, 0, 0],
        "metadata": {},
    }
    for level in levels:
        nodes[level["id"]] = {
            "object": "node", "id": level["id"], "type": "level", "name": level["name"],
            "parentId": building_id, "visible": True, "children": [],
            "level": level["index"], "baseElevation": level["base"], "height": level["height"],
            "metadata": {"sketch2cad": {"elevation": level["absolute"]}},
        }
    by_level = {level["name"]: level for level in levels}
    walls: dict[str, list[dict]] = {}
    for el in elements:
        kind = el.get("type")
        if kind == "wall":
            for run in _wall_nodes(el, by_level, to_m, warnings, ids):
                nodes[run["id"]] = run
                walls.setdefault(str(el.get("id") or run["id"]), []).append(run)
                nodes[run["parentId"]]["children"].append(run["id"])
        elif kind == "floor":
            node = _slab_node(el, by_level, to_m, warnings, ids)
            if node:
                nodes[node["id"]] = node
                nodes[node["parentId"]]["children"].append(node["id"])
        elif kind == "ceiling":
            node = _ceiling_node(el, by_level, to_m, warnings, ids)
            if node:
                nodes[node["id"]] = node
                nodes[node["parentId"]]["children"].append(node["id"])
    for el in elements:
        if el.get("type") != "opening":
            continue
        node = _opening_node(el, walls, to_m, warnings, ids)
        if node is None:
            continue
        nodes[node["id"]] = node
        host = nodes.get(node["parentId"])
        if host is not None:
            host.setdefault("children", []).append(node["id"])
    for item in _scan_items(scan):
        node, note = _scan_node(item, by_level, ids)
        if note:
            warnings.append(note)
        if node is None:
            continue
        nodes[node["id"]] = node
        nodes[node["parentId"]]["children"].append(node["id"])
    scene = {
        "pascal": "sketch2cad-prototype-1",
        "source": PASCAL_REPO,
        "license": PASCAL_LICENSE,
        "units": "m",
        "notes": (
            "Plan coordinates are Pascal's [x, z]. Y is up. "
            "Level baseElevation is the offset above the stack that starts at zero, so the lowest storey keeps its datum. "
            "Load this graph in Pascal, or open model.ifc with @pascal-app/ifc-converter."
        ),
        "nodes": nodes,
    }
    return scene, warnings


def spec_from_scene(scene: dict) -> tuple[dict, dict]:
    """Read a scene this module wrote back into a spec, plus scans and warnings.

    Distances come back in the units recorded on the site (millimetres when none were recorded).
    A level's datum is its baseElevation above the stack of lower levels, starting at zero,
    so an edit to a height or a base offset is kept. The elevation saved at export is not reused.
    """
    warnings: list[str] = []
    nodes = scene.get("nodes") or {}
    site = next((n for n in nodes.values() if n.get("type") == "site"), {})
    units = ((site.get("metadata") or {}).get("sketch2cad") or {}).get("units") or "mm"
    if units not in _TO_M:
        warnings.append(f"Pascal: scene units {units!r} - writing mm")
        units = "mm"
    scale = 1.0 / _TO_M[units]  # metres -> drawing units
    levels = {n["id"]: n for n in nodes.values() if n.get("type") == "level"}
    datums = _level_datums(levels)
    walls_by_id: dict[str, list[dict]] = {}
    for node in nodes.values():
        if node.get("type") != "wall":
            continue
        element_id = _element_id(node)
        walls_by_id.setdefault(element_id, []).append(node)
    elements = []
    for element_id, runs in walls_by_id.items():
        runs = sorted(runs, key=lambda n: int((n.get("metadata") or {}).get("sketch2cad", {}).get("segment") or 0))
        points = [[runs[0]["start"][0] * scale, runs[0]["start"][1] * scale]]
        for run in runs:
            points.append([run["end"][0] * scale, run["end"][1] * scale])
        first = runs[0]
        meta = (first.get("metadata") or {}).get("sketch2cad") or {}
        level = levels.get(first.get("parentId")) or {}
        elements.append({
            "id": element_id,
            "type": "wall",
            "status": meta.get("status") or "new",
            "storey": level.get("name") or "Ground Floor",
            "points": points,
            "height": float(first.get("height") or 3) * scale,
            "thickness": float(first.get("thickness") or 0.2) * scale,
            "z": datums.get(level.get("id"), 0.0) * scale,
        })
    wall_index = {n["id"]: n for runs in walls_by_id.values() for n in runs}
    for node in nodes.values():
        if node.get("type") not in ("door", "window"):
            continue
        host = wall_index.get(node.get("parentId") or "") or wall_index.get(node.get("wallId") or "")
        if host is None:
            warnings.append(f"Pascal: {node.get('type')} {node.get('id')} has no wall - skipped")
            continue
        start = np.array(host["start"], dtype=float)
        end = np.array(host["end"], dtype=float)
        direction = end - start
        length = float(np.linalg.norm(direction))
        if length < 1e-9:
            continue
        direction = direction / length
        along = float((node.get("position") or [0, 0, 0])[0])
        centre = start + direction * along
        height = float(node.get("height") or (2.1 if node["type"] == "door" else 1.5))
        centre_y = float((node.get("position") or [0, 0, 0])[1])
        meta = (node.get("metadata") or {}).get("sketch2cad") or {}
        elements.append({
            "id": _element_id(node),
            "type": "opening",
            "kind": meta.get("kind") or ("door" if node["type"] == "door" else "window"),
            "host": _element_id(host),
            "at": [float(centre[0]) * scale, float(centre[1]) * scale],
            "width": float(node.get("width") or 0.9) * scale,
            "height": height * scale,
            "sill": (centre_y - height / 2) * scale,
            "status": meta.get("status") or "new",
        })
    for node in nodes.values():
        if node.get("type") == "slab":
            elements.append(_surface_element(node, "floor", scale, levels, datums))
        elif node.get("type") == "ceiling":
            elements.append(_surface_element(node, "ceiling", scale, levels, datums))
    scans = []
    for node in nodes.values():
        if node.get("type") != "scan":
            continue
        meta = (node.get("metadata") or {}).get("sketch2cad") or {}
        scans.append({
            "id": node.get("id"),
            "url": node.get("url"),
            "sourcePath": meta.get("sourcePath"),
            "storey": (levels.get(node.get("parentId")) or {}).get("name"),
        })
    spec = {
        "units": units,
        "elements": [el for el in elements if el],
        "dimensions": [],
        "sheet": {"fields": {"title_en": ((site.get("metadata") or {}).get("sketch2cad") or {}).get("title") or ""}},
    }
    return spec, {"scans": scans, "warnings": warnings}


def write_package(spec: dict, folder: Path | str, *, scan=None, project_name: str | None = None) -> dict:
    """Write scene.json, model.ifc, spec.json and connection.json. Returns their paths and warnings."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    scene, scene_warnings = scene_from_spec(spec, scan=scan)
    warnings.extend(scene_warnings)
    ifc_path = folder / "model.ifc"
    spec_to_ifc(spec, ifc_path, project_name=project_name, warnings=warnings)
    scene_path = folder / "scene.json"
    spec_path = folder / "spec.json"
    connection_path = folder / "connection.json"
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    connection = {
        "pascal": PASCAL_REPO,
        "license": PASCAL_LICENSE,
        "editor": EDITOR_COMMAND,
        "mcp": {"mcpServers": {"pascal": {"command": MCP_COMMAND[0], "args": MCP_COMMAND[1:]}}},
        "files": {"scene": scene_path.name, "ifc": ifc_path.name, "spec": spec_path.name},
        "import": (
            "The IFC is the measured model (IfcWall, openings, IfcSlab, IfcCovering ceiling). "
            "Pascal's @pascal-app/ifc-converter turns that IFC into editable nodes. "
            "scene.json is the same building in Pascal's node graph, for a local scene. "
            "A scan that is not an allowed asset URL is metadata.sourcePath only; import the file in the editor."
        ),
        "secrets": "This file has no API key, bearer token or password. Local MCP reads ~/.pascal/run itself.",
    }
    connection_path.write_text(json.dumps(connection, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "scene": scene_path, "ifc": ifc_path, "spec": spec_path, "connection": connection_path,
        "warnings": warnings,
    }


def _title(spec: dict) -> str:
    fields = (spec.get("sheet") or {}).get("fields") or {}
    return str(fields.get("title_en") or fields.get("title_he") or "Sketch2CAD")


def _metres(value, default_mm: float, to_m: float) -> float:
    if value is None:
        return default_mm * 0.001
    return float(value) * to_m


def _xy(points) -> list[np.ndarray]:
    out = []
    for point in points or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            out.append(np.array([float(point[0]), float(point[1])], dtype=float))
    return out


def _outline(el: dict) -> list[np.ndarray]:
    pts = _xy(el.get("points"))
    if len(pts) < 3 and el.get("corners"):
        corners = _xy(el.get("corners"))
        if len(corners) == 2:
            (x0, y0), (x1, y1) = corners
            pts = [np.array(p, dtype=float) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return pts


def _slug(prefix: str, raw, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "", str(raw or "").lower()) or fallback
    return f"{prefix}_{cleaned}"


class _Ids:
    """Pascal ids are `{prefix}_{rest}`. A slug is lossy, so a taken id gains a suffix."""

    def __init__(self):
        self.used: set[str] = set()

    def take(self, prefix: str, raw, fallback: str) -> str:
        base = _slug(prefix, raw, fallback)
        candidate = base
        n = 2
        while candidate in self.used:
            candidate = f"{base}_{n}"
            n += 1
        self.used.add(candidate)
        return candidate


def _element_id(node: dict) -> str:
    meta = (node.get("metadata") or {}).get("sketch2cad") or {}
    return str(meta.get("elementId") or node.get("id"))


def _storey_name(el: dict) -> str:
    return str(el.get("storey") or "Ground Floor")


def _level_datums(levels: dict[str, dict]) -> dict[str, float]:
    """Datum of each level: baseElevation above a stack that starts at zero.

    The cursor after a level is that datum plus its height, which is where the next level sits
    before its own baseElevation. An edit to either value moves every level above it.
    """
    ordered = sorted(levels.values(), key=lambda n: (float(n.get("level") or 0), str(n.get("id") or "")))
    cursor = 0.0
    datums: dict[str, float] = {}
    for level in ordered:
        datum = cursor + float(level.get("baseElevation") or 0)
        datums[level["id"]] = datum
        cursor = datum + float(level.get("height") or 0)
    return datums


def _levels(elements: list[dict], to_m: float, ids: _Ids) -> list[dict]:
    """One Pascal level per storey. Height is the gap to the next storey, else the tallest wall.

    The stack starts at zero, so a basement's baseElevation is its real datum.
    """
    found: dict[str, float] = {}
    heights: dict[str, float] = {}
    for el in elements:
        if el.get("type") not in ("wall", "floor"):
            continue
        name = _storey_name(el)
        z = float(el.get("z") or 0) * to_m
        found[name] = z if name not in found else min(found[name], z)
        if el.get("type") == "wall":
            heights[name] = max(heights.get(name, 0.0), _metres(el.get("height"), _WALL_HEIGHT_MM, to_m))
    for el in elements:
        if el.get("type") == "ceiling" and _storey_name(el) not in found:
            found[_storey_name(el)] = 0.0
    if not found:
        found["Ground Floor"] = 0.0
    ordered = sorted(found, key=lambda name: (found[name], name))
    levels = []
    stacked = 0.0
    for index, name in enumerate(ordered):
        absolute = found[name]
        if index + 1 < len(ordered):
            height = max(found[ordered[index + 1]] - absolute, 0.1)
        else:
            height = max(heights.get(name, 0.0), 3.0)
        base = absolute - stacked
        levels.append({
            "id": ids.take("level", name, f"l{index}"),
            "name": name,
            "index": index,
            "absolute": absolute,
            "base": base,
            "height": height,
        })
        stacked = absolute + height
    return levels


def _level_of(el: dict, levels: dict[str, dict]) -> dict:
    return levels.get(_storey_name(el)) or next(iter(levels.values()))


def _meta(el: dict, **extra) -> dict:
    body = {"elementId": str(el.get("id") or ""), "status": str(el.get("status") or ""), "specType": str(el.get("type") or "")}
    body.update(extra)
    return {"sketch2cad": body}


def _wall_nodes(el: dict, levels: dict[str, dict], to_m: float, warnings: list[str], ids: _Ids) -> list[dict]:
    pts = _xy(el.get("points"))
    if len(pts) < 2:
        warnings.append(f"Pascal: wall {el.get('id', '?')!r} needs at least two points - skipped")
        return []
    level = _level_of(el, levels)
    if abs(float(el.get("z") or 0) * to_m - level["absolute"]) > 1e-6:
        warnings.append(f"Pascal: wall {el.get('id', '?')!r} z is not the storey datum - the level carries the elevation")
    height = _metres(el.get("height"), _WALL_HEIGHT_MM, to_m)
    thickness = _metres(el.get("thickness"), _WALL_THICKNESS_MM, to_m)
    nodes = []
    for index, (a, b) in enumerate(zip(pts, pts[1:])):
        a, b = a * to_m, b * to_m
        if float(np.linalg.norm(b - a)) < 1e-9:
            warnings.append(f"Pascal: wall {el.get('id', '?')!r} has a zero-length segment - skipped")
            continue
        suffix = "" if len(pts) == 2 else f"s{index + 1}"
        nodes.append({
            "object": "node",
            "id": ids.take("wall", f"{el.get('id') or 'w'}{suffix}", f"w{index}"),
            "type": "wall",
            "name": str(el.get("id") or "wall"),
            "parentId": level["id"],
            "visible": True,
            "children": [],
            "start": [float(a[0]), float(a[1])],
            "end": [float(b[0]), float(b[1])],
            "height": height,
            "thickness": thickness,
            "metadata": _meta(el, segment=index),
        })
    return nodes


def _polygon_m(pts, to_m: float) -> list[list[float]]:
    return [[float(p[0]) * to_m, float(p[1]) * to_m] for p in pts]


def _slab_node(el, levels, to_m, warnings, ids: _Ids) -> dict | None:
    pts = _outline(el)
    if len(pts) < 3:
        warnings.append(f"Pascal: floor {el.get('id', '?')!r} needs an outline - skipped")
        return None
    level = _level_of(el, levels)
    top = float(el.get("z") or 0) * to_m
    return {
        "object": "node",
        "id": ids.take("slab", el.get("id"), "floor"),
        "type": "slab",
        "name": str(el.get("id") or "floor"),
        "parentId": level["id"],
        "visible": True,
        "polygon": _polygon_m(pts, to_m),
        "elevation": top - level["absolute"],
        "thickness": _metres(el.get("thickness"), _FLOOR_THICKNESS_MM, to_m),
        "metadata": _meta(el),
    }


def _ceiling_node(el, levels, to_m, warnings, ids: _Ids) -> dict | None:
    pts = _outline(el)
    if len(pts) < 3:
        warnings.append(f"Pascal: ceiling {el.get('id', '?')!r} needs an outline - skipped")
        return None
    level = _level_of(el, levels)
    node = {
        "object": "node",
        "id": ids.take("ceiling", el.get("id"), "ceiling"),
        "type": "ceiling",
        "name": str(el.get("id") or "ceiling"),
        "parentId": level["id"],
        "visible": True,
        "polygon": _polygon_m(pts, to_m),
        "metadata": _meta(el, thickness=_metres(el.get("thickness"), _CEILING_THICKNESS_MM, to_m)),
    }
    if el.get("z") is not None:
        node["height"] = float(el["z"]) * to_m - level["absolute"]
    return node


def _opening_node(el, walls: dict[str, list[dict]], to_m: float, warnings: list[str], ids: _Ids) -> dict | None:
    host_id = str(el.get("host") or "")
    runs = walls.get(host_id) or []
    if not runs:
        warnings.append(f"Pascal: opening {el.get('id', '?')!r} has no host wall - skipped")
        return None
    width = _metres(el.get("width"), _OPENING_WIDTH_MM, to_m)
    height = _metres(el.get("height"), _OPENING_HEIGHT_MM, to_m)
    sill = _metres(el.get("sill"), 0.0, to_m)
    kind = str(el.get("kind") or "opening").lower()
    if kind not in ("opening", "door", "window"):
        warnings.append(f"Pascal: opening {el.get('id', '?')!r} kind {kind!r} - using opening")
        kind = "opening"
    placed = _along_wall(el, runs, to_m, width)
    if placed is None:
        warnings.append(f"Pascal: opening {el.get('id', '?')!r} is not on its host wall - skipped")
        return None
    run, along = placed
    # A plain opening is a door node with no leaf: Pascal cuts the wall either way.
    node_type = "window" if kind == "window" else "door"
    return {
        "object": "node",
        "id": ids.take(node_type, el.get("id"), node_type),
        "type": node_type,
        "name": str(el.get("id") or node_type),
        "parentId": run["id"],
        "wallId": run["id"],
        "visible": True,
        "position": [along, sill + height / 2, 0.0],
        "width": width,
        "height": height,
        "floorThresholdVersion": 1,
        "metadata": _meta(el, kind=kind),
    }


def _along_wall(el, runs: list[dict], to_m: float, width: float):
    if el.get("at") is None:
        return runs[0], float(el.get("along") or 0) * to_m
    at = _xy([el.get("at")])
    if not at:
        return None
    target = at[0] * to_m
    best = None
    for run in runs:
        start = np.array(run["start"], dtype=float)
        end = np.array(run["end"], dtype=float)
        direction = end - start
        length = float(np.linalg.norm(direction))
        if length < 1e-9:
            continue
        direction = direction / length
        along = float(np.dot(target - start, direction))
        across = float(np.linalg.norm((target - start) - direction * along))
        thickness = float(run.get("thickness") or 0.2)
        if -width / 2 - 1e-6 <= along <= length + width / 2 + 1e-6 and across <= thickness + 1e-6:
            rank = (across, 0 if 0 <= along <= length else 1)
            if best is None or rank < best[0]:
                best = (rank, run, along)
    if best is None:
        return None
    return best[1], best[2]


def _scan_items(scan) -> list:
    if scan is None:
        return []
    if isinstance(scan, (str, dict)):
        return [scan]
    return list(scan)


def _scan_node(item, levels: dict[str, dict], ids: _Ids) -> tuple[dict | None, str | None]:
    if isinstance(item, str):
        item = {"path": item}
    level_name = str(item.get("storey") or next(iter(levels)))
    level = levels.get(level_name) or next(iter(levels.values()))
    raw = str(item.get("url") or item.get("path") or "")
    if not raw:
        return None, "Pascal: scan has no path - skipped"
    parsed = urlparse(raw)
    if parsed.username or parsed.password:
        return {
            "object": "node", "id": ids.take("scan", item.get("id") or "scan", "scan"),
            "type": "scan", "name": str(item.get("name") or "as-built scan"),
            "parentId": level["id"], "visible": True, "url": None,
            "position": [0, 0, 0], "rotation": [0, 0, 0], "scale": 1, "opacity": 100,
            "metadata": {"sketch2cad": {"sourcePath": None, "role": "as-built"}},
        }, "Pascal: scan URL contained credentials and was dropped"
    url = raw if scan_url_allowed(raw) else None
    note = None
    if url is None:
        note = "Pascal: scan path is not an allowed asset URL - kept as a path, not as scan.url"
    node = {
        "object": "node",
        "id": ids.take("scan", item.get("id") or Path(str(raw).split("?")[0]).stem, "scan"),
        "type": "scan",
        "name": str(item.get("name") or "as-built scan"),
        "parentId": level["id"],
        "visible": True,
        "url": url,
        "position": [0, 0, 0],
        "rotation": [0, 0, 0],
        "scale": 1,
        "opacity": 100,
        "metadata": {"sketch2cad": {"sourcePath": None if url else str(raw), "role": "as-built"}},
    }
    return node, note


def _surface_element(node: dict, kind: str, scale: float, levels: dict, datums: dict[str, float]) -> dict:
    level = levels.get(node.get("parentId")) or {}
    datum = datums.get(level.get("id"), 0.0)
    meta = (node.get("metadata") or {}).get("sketch2cad") or {}
    polygon = node.get("polygon") or []
    element = {
        "id": _element_id(node),
        "type": kind,
        "status": meta.get("status") or "new",
        "storey": level.get("name") or "Ground Floor",
        "points": [[float(p[0]) * scale, float(p[1]) * scale] for p in polygon],
    }
    if kind == "floor":
        element["thickness"] = float(node.get("thickness") or 0.2) * scale
        element["z"] = (float(node.get("elevation") or 0) + datum) * scale
    else:
        element["thickness"] = float(meta.get("thickness") or 0.02) * scale
        if node.get("height") is not None:
            element["z"] = (float(node["height"]) + datum) * scale
    return element
