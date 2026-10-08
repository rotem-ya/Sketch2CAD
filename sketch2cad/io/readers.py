"""Read any incoming file: kind, page count, text for the search index and a preview PNG.

DWG is read through a DXF made by the ODA converter; IFC through a plan cut (ifc2dxf).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
import pymupdf
from bidi.algorithm import get_display
from ezdxf import bbox, recover
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from matplotlib.figure import Figure

from . import oda
from .pdf2dxf import has_hebrew, text_lines

PREVIEW_DPI = 150
MAX_PX = 2000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic", ".heif", ".gif"}
KINDS = {".dxf": "dxf", ".dwg": "dwg", ".pdf": "pdf", ".ifc": "ifc", ".rvt": "rvt", ".rfa": "rvt"}
RVT_WARNING = ("Revit files (RVT) cannot be read locally: there is no free reader. Ask the designer "
               "for IFC or DWG (standard deliverables), or export IFC from Revit. Alternative: "
               "Autodesk APS Model Derivative (free monthly quota, needs an Autodesk account).")


@dataclass
class FileInfo:
    kind: str                          # dxf | dwg | pdf | image | ifc | rvt | other
    pages: int = 0
    text: str = ""
    preview_png: Path | None = None
    dxf_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def read_any(path: Path | str, out_dir: Path | str) -> FileInfo:
    """Inspect a file; previews and converted files are written to out_dir."""
    path, out_dir = Path(path), Path(out_dir)
    if not path.is_file():
        raise FileNotFoundError(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    kind = "image" if ext in IMAGE_EXT else KINDS.get(ext, "other")
    preview = out_dir / f"{path.stem}_preview.png"
    if kind == "dxf":
        return _read_dxf(path, preview, FileInfo("dxf", dxf_path=path))
    if kind == "dwg":
        info = FileInfo("dwg")
        try:
            info.dxf_path = oda.convert(path, "dxf", out_dir)
        except RuntimeError as exc:                              # incl. ConverterNotFound
            info.warnings.append(f"DWG not converted: {exc}")
            return info
        return _read_dxf(info.dxf_path, preview, info)
    if kind == "pdf":
        return _read_pdf(path, preview)
    if kind == "image":
        return _read_image(path, preview)
    if kind == "ifc":
        return _read_ifc(path, out_dir, preview)
    if kind == "rvt":
        return FileInfo("rvt", warnings=[RVT_WARNING])
    return FileInfo("other", warnings=[f"Unsupported file type: {ext or '(none)'}"])


# --- DXF ---------------------------------------------------------------------------------------

def dxf_text(doc) -> str:
    """TEXT/MTEXT/ATTRIB plain text (all layouts and blocks) and layer names, without duplicates."""
    found = []
    for block in doc.blocks:
        for e in block:
            if e.dxftype() in ("TEXT", "MTEXT"):
                found.append(e.plain_text())
            elif e.dxftype() == "INSERT":
                found.extend(a.plain_text() for a in e.attribs)
    found.extend(layer.dxf.name for layer in doc.layers if layer.dxf.name not in ("0", "Defpoints"))
    return "\n".join(dict.fromkeys(t.strip() for t in found if t.strip()))


def _visual(text: str) -> str:
    return get_display(text.replace("%%c", "Ø").replace("%%C", "Ø"))


def _hebrew_for_display(doc) -> None:
    """ezdxf draws glyphs left to right: put Hebrew in visual order. Changes the doc - never save it."""
    for block in doc.blocks:
        for e in block:
            if e.dxftype() == "MTEXT" and has_hebrew(e.text):
                e.text = "\\P".join(_visual(line) for line in e.plain_text().split("\n"))
            texts = [e] if e.dxftype() == "TEXT" else list(e.attribs) if e.dxftype() == "INSERT" else []
            for t in texts:
                if has_hebrew(t.dxf.text):
                    t.dxf.text = _visual(t.dxf.text)


def dxf_extents(doc) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Model space extents ((xmin, ymin), (xmax, ymax)) or None for an empty drawing."""
    box = bbox.extents(doc.modelspace(), fast=True)
    if not box.has_data:
        return None
    return (box.extmin.x, box.extmin.y), (box.extmax.x, box.extmax.y)


def render_dxf_png(doc, png: Path | str, *, extents=None, max_px: int = MAX_PX) -> Path:
    """Model space -> PNG on white, max_px on the long side. extents ((x0, y0), (x1, y1)) default
    to the drawing's own plus a 2% margin. Hebrew text of `doc` is changed for display."""
    _hebrew_for_display(doc)
    if extents is None:
        (x0, y0), (x1, y1) = dxf_extents(doc) or ((0, 0), (1, 1))
        m = 0.02 * max(x1 - x0, y1 - y0, 1e-9)
        extents = (x0 - m, y0 - m), (x1 + m, y1 + m)
    (x0, y0), (x1, y1) = extents
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    f = max_px / max(w, h)
    size_px = (max(1, round(w * f)), max(1, round(h * f)))
    fig = Figure(figsize=(size_px[0] / 100, size_px[1] / 100), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    config = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=config).draw_layout(doc.modelspace())
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("auto")
    ax.set_axis_off()
    fig.savefig(png, dpi=100, facecolor="white")
    return Path(png)


def _read_dxf(path: Path, preview: Path, info: FileInfo) -> FileInfo:
    try:
        doc, auditor = recover.readfile(path)
    except (OSError, ezdxf.DXFStructureError) as exc:
        info.warnings.append(f"Cannot read DXF: {exc}")
        return info
    if auditor.has_errors:
        info.warnings.append(f"DXF had {len(auditor.errors)} errors (recovered)")
    info.pages = 1
    info.text = dxf_text(doc)
    try:
        info.preview_png = render_dxf_png(doc, preview)
    except Exception as exc:                                     # preview is best effort
        info.warnings.append(f"No preview: {exc}")
    return info


# --- PDF and images ----------------------------------------------------------------------------

def _save_page_png(page: pymupdf.Page, png: Path) -> Path:
    """Page at PREVIEW_DPI, long side capped at MAX_PX."""
    zoom = min(PREVIEW_DPI / 72, MAX_PX / max(page.rect.width, page.rect.height))
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(png)
    return png


def _read_pdf(path: Path, preview: Path) -> FileInfo:
    info = FileInfo("pdf")
    with pymupdf.open(path) as doc:
        info.pages = doc.page_count
        info.text = "\n".join(line.text for page in doc for line in text_lines(page))
        if doc.page_count:
            info.preview_png = _save_page_png(doc[0], preview)
    if info.pages and not info.text.strip():
        info.warnings.append("No text layer (scanned PDF?)")
    return info


def _read_image(path: Path, preview: Path) -> FileInfo:
    info = FileInfo("image")
    try:
        with pymupdf.open(path) as img, pymupdf.open("pdf", img.convert_to_pdf()) as pdf:
            info.pages = pdf.page_count
            info.preview_png = _save_page_png(pdf[0], preview)
    except Exception as exc:                                     # e.g. HEIC is not supported by MuPDF
        info.warnings.append(f"Cannot open image ({path.suffix}): {exc}. Save it as JPG or PNG.")
    return info


# --- IFC ---------------------------------------------------------------------------------------

def _read_ifc(path: Path, out_dir: Path, preview: Path) -> FileInfo:
    from . import ifc2dxf

    info = FileInfo("ifc", pages=1)
    try:
        ifcopenshell = ifc2dxf.require_ifcopenshell()
    except ImportError as exc:
        info.warnings.append(str(exc))
        return info
    try:
        model = ifcopenshell.open(str(path))
    except (OSError, RuntimeError, ifcopenshell.Error) as exc:
        info.warnings.append(f"Cannot read IFC: {exc}")
        return info
    lines = [f"{e.is_a()}: {e.Name}" for t in ("IfcProject", "IfcSite", "IfcBuilding")
             for e in model.by_type(t) if e.Name]
    lines += [f"IfcBuildingStorey: {s.Name} ({s.Elevation or 0})" for s in model.by_type("IfcBuildingStorey")]
    counts = Counter(e.is_a() for e in model.by_type("IfcElement"))
    lines += [f"{cls}: {n}" for cls, n in sorted(counts.items())]
    info.text = "\n".join(lines)
    try:
        info.dxf_path = ifc2dxf.ifc_to_dxf(path, out_dir / f"{path.stem}_plan.dxf")
        info.preview_png = render_dxf_png(ezdxf.readfile(info.dxf_path), preview)
    except Exception as exc:                                     # geometry is best effort
        info.warnings.append(f"No plan preview: {exc}")
    return info
