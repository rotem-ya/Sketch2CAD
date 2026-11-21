"""Optional clash and defect check for an IFC file, written out as BCF 2.1.

Clash geometry comes from IfcClash (LGPL-3.0, https://docs.ifcopenshell.org/ifcclash.html).
Sketch2CAD imports that package and does not copy it. The check does not run during
`render`; call `check_model`. IfcClash's own BCF export imports the `bcf` package
(PyPI `bcf-client`, GPL-3.0). This module does not import it. It writes the BCF 2.1
zip from the clash records IfcClash returns, using the buildingSMART schema.

A defect is a model problem a quantity takeoff cannot trust: no body, no quantity
set, a quantity that is not positive, or an opening that voids nothing. Those do
not need IfcClash. They use IfcOpenShell, which the IFC export already uses.
"""
from __future__ import annotations

import copy
import logging
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from .ifc2dxf import require_ifcopenshell

_BODY_CLASSES = ("IfcWall", "IfcSlab", "IfcCovering", "IfcDoor", "IfcWindow", "IfcColumn", "IfcBeam")
_QUANTITY_CLASSES = _BODY_CLASSES
_PAIRS = (
    ("Walls and floors", "IfcWall", "IfcSlab"),
    ("Walls and ceilings", "IfcWall", "IfcCovering"),
    ("Walls", "IfcWall", None),
)
_HIGH_CLASH_M = 0.05


def clash_available() -> bool:
    """True when IfcClash imports. The office install does not require it."""
    try:
        from ifcclash.ifcclash import Clasher, ClashSettings  # noqa: F401
    except ImportError:
        return False
    return True


def default_clash_sets(ifc: Path | str, *, tolerance: float = 0.002, other: Path | str | None = None) -> list[dict]:
    """Intersection sets for one exported model, and optionally against a second file.

    A pair is omitted when that class is not in the file, so an empty ceiling
    set is not sent to IfcClash. `other` is only the opposing group: walls in
    `ifc` against walls, slabs and coverings in `other`.
    """
    ifc = Path(ifc)
    sets = []
    for name, left, right in _PAIRS:
        if not _has_class(ifc, left):
            continue
        item = _intersection(name, tolerance, [_source(ifc, left)])
        if right is not None:
            if not _has_class(ifc, right):
                continue
            item["b"] = [_source(ifc, right)]
        sets.append(item)
    if other is not None:
        other = Path(other)
        opposing = [_source(other, name) for name in ("IfcWall", "IfcSlab", "IfcCovering") if _has_class(other, name)]
        if _has_class(ifc, "IfcWall") and opposing:
            sets.append(_intersection("Model against other", tolerance, [_source(ifc, "IfcWall")], opposing))
    return sets


def find_defects(ifc: Path | str) -> list[dict]:
    """Model problems that are not a geometric clash. Each item is one BCF topic."""
    ifcopenshell = require_ifcopenshell()
    import ifcopenshell.util.unit

    model = ifcopenshell.open(str(ifc))
    scale = float(ifcopenshell.util.unit.calculate_unit_scale(model))
    hosted = set()
    for rel in model.by_type("IfcRelVoidsElement"):
        opening = rel.RelatedOpeningElement
        if opening is not None:
            hosted.add(opening.GlobalId)
    defects = []
    for opening in model.by_type("IfcOpeningElement"):
        if opening.GlobalId not in hosted:
            defects.append(_defect(
                "opening_without_host", opening, scale,
                "This opening does not void a host, so it cannot be subtracted from a quantity.",
            ))
    for ifc_class in _BODY_CLASSES:
        for product in model.by_type(ifc_class):
            if not _has_body(product):
                defects.append(_defect(
                    "no_body", product, scale,
                    "This element has no geometric body, so a viewer and a takeoff have nothing to measure.",
                ))
            quantities = _quantities(product)
            if ifc_class in _QUANTITY_CLASSES and not quantities["present"]:
                defects.append(_defect(
                    "missing_quantity", product, scale,
                    "This element has no base quantity set. A takeoff would have to guess the measure.",
                ))
            for name, value in quantities["values"]:
                if value is not None and float(value) <= 0:
                    defects.append(_defect(
                        "non_positive_quantity", product, scale,
                        f"Quantity {name} is {float(value):.6g}, which is not a positive measure.",
                        priority="High",
                    ))
    return defects


def find_clashes(ifc: Path | str, *, tolerance: float = 0.002, other: Path | str | None = None,
                 clash_sets: list[dict] | None = None, warnings: list[str] | None = None) -> list[dict]:
    """Run IfcClash. Raises ImportError when IfcClash is not installed.

    Two products that carry the same Sketch2CAD `ElementId` are one wall's
    segments, and that pair is not reported. A product is not reported against itself.
    """
    warnings = warnings if warnings is not None else []
    try:
        from ifcclash.ifcclash import Clasher, ClashSettings
    except ImportError as exc:
        raise ImportError(
            "Clash checks need IfcClash (LGPL-3.0): pip install -r requirements-clash.txt"
        ) from exc

    ifcopenshell = require_ifcopenshell()
    sets = copy.deepcopy(clash_sets if clash_sets is not None else default_clash_sets(ifc, tolerance=tolerance, other=other))
    if not sets:
        return []
    settings = ClashSettings()
    settings.output = "clashes.json"
    settings.logger = logging.getLogger("sketch2cad.clash")
    if not settings.logger.handlers:
        settings.logger.addHandler(logging.NullHandler())
    settings.logger.propagate = False
    clasher = Clasher(settings)
    clasher.clash_sets = sets
    found = []
    seen = set()
    for clash_set in sets:
        name = str(clash_set.get("name") or "Clash")
        try:
            clasher.process_clash_set(clash_set)
        except Exception as exc:  # a set with no mesh must not drop the other sets
            warnings.append(f"Clash set {name} skipped: {exc}")
            continue
        products = _products_by_guid(clasher, ifcopenshell)
        for clash in (clash_set.get("clashes") or {}).values():
            issue = _clash_issue(clash, products, seen)
            if issue is not None:
                found.append(issue)
    return found


def check_model(ifc: Path | str, out: Path | str, *, other: Path | str | None = None,
                tolerance: float = 0.002, clashes: bool = True, author: str = "Sketch2CAD",
                when: datetime | None = None) -> dict:
    """Write a BCF 2.1 file of defects and, when IfcClash is installed, clashes.

    `clashes=False` writes defects only. A missing IfcClash install still writes
    the defects and adds a warning; it does not fail the file.
    """
    ifc = Path(ifc)
    out = Path(out)
    warnings: list[str] = []
    defects = find_defects(ifc)
    clash_rows: list[dict] = []
    if clashes:
        if clash_available():
            clash_rows = find_clashes(ifc, tolerance=tolerance, other=other, warnings=warnings)
        else:
            warnings.append("Clash checks skipped: IfcClash is not installed (pip install -r requirements-clash.txt).")
    issues = defects + clash_rows
    ifcopenshell = require_ifcopenshell()
    model = ifcopenshell.open(str(ifc))
    project = model.by_type("IfcProject")
    project_guid = project[0].GlobalId if project else ""
    write_bcf(issues, out, ifc_name=ifc.name, project_guid=project_guid, author=author, when=when)
    return {
        "ifc": str(ifc),
        "bcf": str(out),
        "defects": defects,
        "clashes": clash_rows,
        "warnings": warnings,
    }


def write_bcf(issues: list[dict], out: Path | str, *, ifc_name: str, project_guid: str = "",
              author: str = "Sketch2CAD", when: datetime | None = None) -> Path:
    """Write `issues` as one BCF 2.1 zip. An empty list is a valid file with no topics."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(when)
    with zipfile.ZipFile(out, "w") as archive:
        _zip_text(archive, "bcf.version", _version_xml())
        for index, issue in enumerate(issues, start=1):
            topic_id = str(issue.get("topic_id") or uuid.uuid4())
            view_id = str(issue.get("viewpoint_id") or uuid.uuid4())
            folder = f"{topic_id}/"
            _zip_dir(archive, folder)
            _zip_text(archive, folder + "markup.bcf", _markup_xml(
                issue, topic_id, view_id, index, ifc_name, project_guid, author, stamp))
            _zip_text(archive, folder + "viewpoint.bcfv", _viewpoint_xml(issue, view_id))
    return out


def read_bcf(path: Path | str) -> list[dict]:
    """Read the topics this module writes: type, title, status, description, IfcGuids and camera."""
    import xml.etree.ElementTree as ET

    topics = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        markups = [name for name in names if name.endswith("/markup.bcf")]
        for name in markups:
            root = ET.fromstring(archive.read(name))
            topic = root.find("Topic")
            if topic is None:
                continue
            view = root.find("Viewpoints")
            view_name = ""
            if view is not None and view.find("Viewpoint") is not None:
                view_name = (view.findtext("Viewpoint") or "").strip()
            folder = name.rsplit("/", 1)[0]
            guids, point, segment = [], None, None
            if view_name:
                blob = archive.read(f"{folder}/{view_name}")
                vis = ET.fromstring(blob)
                guids = [node.get("IfcGuid") or "" for node in vis.iter("Component") if node.get("IfcGuid")]
                eye = vis.find("./PerspectiveCamera/CameraViewPoint")
                if eye is not None:
                    point = [float(eye.findtext(axis) or 0) for axis in ("X", "Y", "Z")]
                line = vis.find("./Lines/Line")
                if line is not None:
                    segment = [
                        [float(line.findtext(f"{end}/{axis}") or 0) for axis in ("X", "Y", "Z")]
                        for end in ("StartPoint", "EndPoint")
                    ]
            index_text = topic.findtext("Index") or "0"
            topics.append({
                "topic_id": topic.get("Guid") or "",
                "topic_type": topic.get("TopicType") or "",
                "status": topic.get("TopicStatus") or "",
                "title": topic.findtext("Title") or "",
                "description": topic.findtext("Description") or "",
                "priority": topic.findtext("Priority") or "",
                "index": int(index_text),
                "author": topic.findtext("CreationAuthor") or "",
                "guids": guids,
                "camera": point,
                "segment": segment,
                "ifc_name": (root.findtext("./Header/File/Filename") or ""),
            })
    return sorted(topics, key=lambda item: item["index"])


def _intersection(name: str, tolerance: float, group_a: list[dict], group_b: list[dict] | None = None) -> dict:
    item = {
        "name": name,
        "mode": "intersection",
        "tolerance": float(tolerance),
        "check_all": True,
        "a": group_a,
    }
    if group_b:
        item["b"] = group_b
    return item


def _source(path: Path, ifc_class: str) -> dict:
    return {"file": str(path), "selector": ifc_class, "mode": "i"}


def _has_class(path: Path, ifc_class: str) -> bool:
    ifcopenshell = require_ifcopenshell()
    model = ifcopenshell.open(str(path))
    return bool(model.by_type(ifc_class))


def _has_body(product) -> bool:
    shape = product.Representation
    if shape is None:
        return False
    for rep in shape.Representations or []:
        if rep.Items:
            return True
    return False


def _quantities(product) -> dict:
    present = False
    values = []
    for rel in product.IsDefinedBy or []:
        definition = getattr(rel, "RelatingPropertyDefinition", None)
        if definition is None or not definition.is_a("IfcElementQuantity"):
            continue
        present = True
        for quantity in definition.Quantities or []:
            if quantity.is_a("IfcQuantityLength"):
                values.append((quantity.Name, quantity.LengthValue))
            elif quantity.is_a("IfcQuantityArea"):
                values.append((quantity.Name, quantity.AreaValue))
            elif quantity.is_a("IfcQuantityVolume"):
                values.append((quantity.Name, quantity.VolumeValue))
    return {"present": present, "values": values}


def _spec_id(product) -> str:
    import ifcopenshell.util.element

    try:
        psets = ifcopenshell.util.element.get_psets(product)
    except Exception:
        return ""
    value = (psets.get("Sketch2CAD") or {}).get("ElementId") or ""
    return str(value)


def _placement_m(product, scale: float) -> list[float]:
    placement = getattr(product, "ObjectPlacement", None)
    if placement is not None and placement.is_a("IfcLocalPlacement"):
        relative = placement.RelativePlacement
        location = getattr(relative, "Location", None) if relative is not None else None
        coords = list(getattr(location, "Coordinates", None) or [])
        if coords:
            point = [float(value) * scale for value in coords[:3]]
            while len(point) < 3:
                point.append(0.0)
            return point
    return [0.0, 0.0, 0.0]


def _defect(code: str, product, scale: float, detail: str, *, priority: str = "") -> dict:
    name = str(product.Name or product.is_a())
    if not priority:
        priority = "Medium" if code == "missing_quantity" else "High"
    return {
        "kind": "defect",
        "code": code,
        "topic_type": "Defect",
        "title": f"{code}: {product.is_a()} {name}",
        "description": f"{detail} IfcGuid {product.GlobalId}.",
        "priority": priority,
        "guids": [product.GlobalId],
        "names": [name],
        "point": _placement_m(product, scale),
        "distance": None,
    }


def _products_by_guid(clasher, ifcopenshell) -> dict:
    found = {}
    for model in clasher.ifcs.values():
        if not isinstance(model, ifcopenshell.file):
            continue
        for product in model.by_type("IfcProduct"):
            found[product.GlobalId] = product
    return found


def _clash_issue(clash: dict, products: dict, seen: set) -> dict | None:
    left = str(clash.get("a_global_id") or "")
    right = str(clash.get("b_global_id") or "")
    if not left or not right or left == right:
        return None
    pair = tuple(sorted((left, right)))
    if pair in seen:
        return None
    product_a = products.get(left)
    product_b = products.get(right)
    spec_a = _spec_id(product_a) if product_a is not None else ""
    spec_b = _spec_id(product_b) if product_b is not None else ""
    if spec_a and spec_a == spec_b:
        return None
    seen.add(pair)
    distance = float(clash.get("distance") or 0)
    kind = str(clash.get("type") or "intersection")
    p1 = [float(value) for value in (clash.get("p1") or [0, 0, 0])]
    p2 = [float(value) for value in (clash.get("p2") or p1)]
    point = [(a + b) / 2 for a, b in zip(p1, p2)]
    a_name = str(clash.get("a_name") or left)
    b_name = str(clash.get("b_name") or right)
    a_class = str(clash.get("a_ifc_class") or "IfcProduct")
    b_class = str(clash.get("b_ifc_class") or "IfcProduct")
    return {
        "kind": "clash",
        "code": kind,
        "topic_type": "Clash",
        "title": f"{a_class} {a_name} and {b_class} {b_name}",
        "description": (
            f"Intersection ({kind}) of {distance:.3f} m between {left} and {right}. "
            "A takeoff of both elements would count the shared volume twice."
        ),
        "priority": "High" if distance >= _HIGH_CLASH_M else "Medium",
        "guids": [left, right],
        "names": [a_name, b_name],
        "point": point,
        "p1": p1,
        "p2": p2,
        "distance": distance,
    }


def _stamp(when: datetime | None) -> str:
    if when is None:
        when = datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _version_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Version VersionId="2.1" xsi:noNamespaceSchemaLocation="version.xsd" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "\t<DetailedVersion>2.1</DetailedVersion>\n"
        "</Version>\n"
    )


def _markup_xml(issue, topic_id, view_id, index, ifc_name, project_guid, author, stamp) -> str:
    project = f' IfcProject="{_attr(project_guid)}"' if project_guid else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Markup xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="markup.xsd">\n'
        "    <Header>\n"
        f'        <File{project} isExternal="true">\n'
        f"            <Filename>{_text(ifc_name)}</Filename>\n"
        f"            <Date>{stamp}</Date>\n"
        "        </File>\n"
        "    </Header>\n"
        f'    <Topic Guid="{_attr(topic_id)}" TopicType="{_attr(issue["topic_type"])}" TopicStatus="Open">\n'
        f"        <Title>{_text(issue['title'])}</Title>\n"
        f"        <Priority>{_text(issue['priority'])}</Priority>\n"
        f"        <Index>{index}</Index>\n"
        f"        <CreationDate>{stamp}</CreationDate>\n"
        f"        <CreationAuthor>{_text(author)}</CreationAuthor>\n"
        f"        <Description>{_text(issue['description'])}</Description>\n"
        "    </Topic>\n"
        f'    <Viewpoints Guid="{_attr(view_id)}">\n'
        "        <Viewpoint>viewpoint.bcfv</Viewpoint>\n"
        "        <Index>0</Index>\n"
        "    </Viewpoints>\n"
        "</Markup>\n"
    )


def _viewpoint_xml(issue, view_id: str) -> str:
    selected = "\n".join(
        f'            <Component IfcGuid="{_attr(guid)}"/>' for guid in issue.get("guids") or []
    )
    selection = f"        <Selection>\n{selected}\n        </Selection>\n" if selected else ""
    eye, direction, up = _camera(issue.get("point") or [0.0, 0.0, 0.0])
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<VisualizationInfo Guid="{_attr(view_id)}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="visinfo.xsd">\n'
        "    <Components>\n"
        '        <ViewSetupHints SpacesVisible="false" SpaceBoundariesVisible="false" OpeningsVisible="true"/>\n'
        f"{selection}"
        '        <Visibility DefaultVisibility="true"/>\n'
        "    </Components>\n"
        "    <PerspectiveCamera>\n"
        f"        <CameraViewPoint>{_xyz(eye)}</CameraViewPoint>\n"
        f"        <CameraDirection>{_xyz(direction)}</CameraDirection>\n"
        f"        <CameraUpVector>{_xyz(up)}</CameraUpVector>\n"
        "        <FieldOfView>60.0</FieldOfView>\n"
        "    </PerspectiveCamera>\n"
        f"{_clash_line(issue)}"
        "</VisualizationInfo>\n"
    )


def _clash_line(issue: dict) -> str:
    """The two IfcClash points, in metres. A defect has no segment."""
    p1, p2 = issue.get("p1"), issue.get("p2")
    if not p1 or not p2:
        return ""
    return (
        "    <Lines>\n"
        "        <Line>\n"
        f"            <StartPoint>{_xyz(p1)}</StartPoint>\n"
        f"            <EndPoint>{_xyz(p2)}</EndPoint>\n"
        "        </Line>\n"
        "    </Lines>\n"
    )


def _camera(point: list[float]) -> tuple[list[float], list[float], list[float]]:
    import numpy as np

    target = np.array(point, dtype=float)
    eye = target + np.array([4.0, -4.0, 3.0])
    direction = target - eye
    norm = float(np.linalg.norm(direction))
    direction = direction / norm if norm else np.array([-1.0, 1.0, -1.0])
    up = np.array([0.0, 0.0, 1.0])
    up = up - direction * float(np.dot(up, direction))
    if float(np.linalg.norm(up)) < 1e-8:
        up = np.array([0.0, 1.0, 0.0])
        up = up - direction * float(np.dot(up, direction))
    up = up / float(np.linalg.norm(up))
    return eye.tolist(), direction.tolist(), up.tolist()


def _xyz(values: list[float]) -> str:
    x, y, z = values
    return f"<X>{x:.6f}</X><Y>{y:.6f}</Y><Z>{z:.6f}</Z>"


def _text(value) -> str:
    return escape(str(value), {"\n": "&#10;"})


def _attr(value) -> str:
    return escape(str(value), {'"': "&quot;"})


def _zip_text(archive: zipfile.ZipFile, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o644 << 16)
    archive.writestr(info, text.encode("utf-8"))


def _zip_dir(archive: zipfile.ZipFile, name: str) -> None:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = (0o40755 << 16) | 0x10
    archive.writestr(info, b"")
