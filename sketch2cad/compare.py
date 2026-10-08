"""Visual and entity-level comparison of two drawings (PDF, DXF/DWG or image).

compare() rasterizes both files and overlays them: unchanged ink grey, only in A (removed) red,
only in B (added) green. Two DXF/DWG files are rendered with the union of their extents so they
align; other pairs are aligned by page (the smaller raster is scaled up to the larger).
"""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import ezdxf
import numpy as np
import pymupdf
from ezdxf import recover

from .io import oda
from .io.readers import dxf_extents, render_dxf_png

INK = 0.75                     # grey level (0 black .. 1 white) below which a pixel is ink
A3_LONG_IN = 420 / 25.4        # DXF rasters: long side as on an A3 sheet
CAD_EXT = (".dxf", ".dwg")


def _load_dxf(path: Path, tmp: Path):
    if path.suffix.lower() == ".dwg":
        path = oda.convert(path, "dxf", tmp)
    return recover.readfile(path)[0]


def _gray(png: Path) -> np.ndarray:
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.Pixmap(str(png)))
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, 0] / 255


def _raster(path: Path, dpi: int) -> np.ndarray:
    """Grey image (0..1) of a PDF's first page or of an image file."""
    with pymupdf.open(path) as doc:
        pdf = doc if doc.is_pdf else pymupdf.open("pdf", doc.convert_to_pdf())
        zoom = dpi / 72
        pix = pdf[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width) / 255


def _resize(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resize (used to scale up only, so no line is lost)."""
    rows = np.arange(shape[0]) * img.shape[0] // shape[0]
    cols = np.arange(shape[1]) * img.shape[1] // shape[1]
    return img[rows][:, cols]


def _dilate(mask: np.ndarray) -> np.ndarray:
    """Grow a mask by one pixel (3x3), tolerating anti-aliasing and 1 px shifts."""
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    grown = out.copy()
    grown[:, 1:] |= out[:, :-1]
    grown[:, :-1] |= out[:, 1:]
    return grown


def _union(docs) -> tuple[tuple[float, float], tuple[float, float]]:
    """Union of the drawings' extents plus a 2% margin."""
    boxes = [e for e in map(dxf_extents, docs) if e] or [((0, 0), (1, 1))]
    x0, y0 = min(e[0][0] for e in boxes), min(e[0][1] for e in boxes)
    x1, y1 = max(e[1][0] for e in boxes), max(e[1][1] for e in boxes)
    m = 0.02 * max(x1 - x0, y1 - y0, 1e-9)
    return (x0 - m, y0 - m), (x1 + m, y1 + m)


def _rasters(a: Path, b: Path, tmp: Path, dpi: int) -> tuple[np.ndarray, np.ndarray]:
    docs = [_load_dxf(p, tmp) if p.suffix.lower() in CAD_EXT else None for p in (a, b)]
    extents = _union(docs) if all(d is not None for d in docs) else None
    images = []
    for i, (path, doc) in enumerate(zip((a, b), docs)):
        if doc is None:
            images.append(_raster(path, dpi))
        else:
            png = render_dxf_png(doc, tmp / f"{i}.png", extents=extents, max_px=round(A3_LONG_IN * dpi))
            images.append(_gray(png))
    shape = (max(images[0].shape[0], images[1].shape[0]), max(images[0].shape[1], images[1].shape[1]))
    return tuple(img if img.shape == shape else _resize(img, shape) for img in images)


def compare(a: Path | str, b: Path | str, out_png: Path | str, *, dpi: int = 150) -> tuple[Path, float]:
    """Overlay A (old) and B (new) into out_png; returns (out_png, changed ink / all ink)."""
    a, b, out_png = Path(a), Path(b), Path(out_png)
    with tempfile.TemporaryDirectory(prefix="s2c_cmp_") as tmp:
        img_a, img_b = _rasters(a, b, Path(tmp), dpi)
    ink_a, ink_b = img_a < INK, img_b < INK
    removed = ink_a & ~_dilate(ink_b)
    added = ink_b & ~_dilate(ink_a)
    rgb = np.full(img_a.shape + (3,), 255, np.uint8)
    rgb[ink_a | ink_b] = (160, 160, 160)
    rgb[removed] = (220, 0, 0)
    rgb[added] = (0, 170, 0)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    h, w = img_a.shape
    pymupdf.Pixmap(pymupdf.csRGB, w, h, rgb.tobytes(), False).save(out_png)
    total = int((ink_a | ink_b).sum())
    return out_png, (int(removed.sum() + added.sum()) / total if total else 0.0)


def _signature(e) -> tuple:
    """Entity identity for diffing: type, layer and rounded geometry/text (no handles)."""
    attribs = e.dxf.all_existing_dxf_attribs()
    for key in ("handle", "owner"):
        attribs.pop(key, None)
    values = []
    for key, value in sorted(attribs.items()):
        if isinstance(value, (tuple, ezdxf.math.Vec3, ezdxf.math.Vec2)):
            value = tuple(round(float(v), 3) for v in value)
        elif isinstance(value, float):
            value = round(value, 3)
        values.append((key, value))
    if e.dxftype() == "LWPOLYLINE":
        values.append(tuple(tuple(round(v, 3) for v in p) for p in e.get_points()))
    elif e.dxftype() == "MTEXT":
        values.append(e.text)
    return e.dxftype(), e.dxf.get("layer", "0"), tuple(values)


def compare_dxf_entities(a: Path | str, b: Path | str) -> dict:
    """Model space entity diff of two DXF/DWG files.

    Returns {"a": n, "b": n, "added": n, "removed": n,
             "by_type": {type: {"added": n, "removed": n}}, "by_layer": {layer: {...}}}.
    A moved or edited entity counts as one removed and one added.
    """
    with tempfile.TemporaryDirectory(prefix="s2c_cmp_") as tmp:
        sig_a, sig_b = (Counter(map(_signature, _load_dxf(Path(p), Path(tmp)).modelspace())) for p in (a, b))
    removed, added = sig_a - sig_b, sig_b - sig_a
    result = {"a": sum(sig_a.values()), "b": sum(sig_b.values()),
              "added": sum(added.values()), "removed": sum(removed.values()), "by_type": {}, "by_layer": {}}
    for change, counter in (("added", added), ("removed", removed)):
        for (dxftype, layer, _), n in counter.items():
            for group, key in (("by_type", dxftype), ("by_layer", layer)):
                entry = result[group].setdefault(key, {"added": 0, "removed": 0})
                entry[change] += n
    return result
