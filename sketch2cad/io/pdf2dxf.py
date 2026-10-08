"""PDF page -> DXF: vector paths and text become DXF entities, scanned pages an IMAGE underlay.

Coordinates: PDF points (1/72 inch, y down) -> paper mm (y up) -> model units at 1:scale.
Hebrew text: MuPDF usually returns right-to-left runs in logical order already. A line whose
Hebrew characters advance in the reading direction (left to right for horizontal text) was
extracted in visual order; it is converted back with bidi get_display, which is its own
inverse for pure Hebrew runs (mixed Hebrew/number lines are best effort).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import ezdxf
import pymupdf
from bidi.algorithm import get_display
from ezdxf.lldxf.const import VALID_DXF_LINEWEIGHTS

PT_MM = 25.4 / 72
MIN_VECTOR_PATHS = 20          # fewer paths than this -> treat the page as scanned
ARIAL_CAP_HEIGHT = 0.716       # DXF text height = cap height; PDF font size = em size
UNDERLAY_DPI = 200
UNDERLAY_MAX_PX = 8000


def has_hebrew(text: str) -> bool:
    return any("֐" <= ch <= "׿" for ch in text)


@dataclass
class TextLine:
    """One extracted text line (PDF points, y down)."""
    text: str                  # logical order
    origin: tuple[float, float]  # baseline start of the visually leftmost character
    size: float                # font size in points
    angle: float               # degrees, counter-clockwise (y up)
    color: tuple[int, int, int]


def text_lines(page: pymupdf.Page) -> list[TextLine]:
    """Text of a page, line by line, Hebrew in logical order."""
    lines = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            chars = [c for span in line["spans"] for c in span["chars"]]
            text = "".join(c["c"] for c in chars).strip()
            if not text:
                continue
            dx, dy = line["dir"]
            proj = [c["origin"][0] * dx + c["origin"][1] * dy for c in chars]
            if has_hebrew(text):
                heb = [t for c, t in zip(chars, proj) if has_hebrew(c["c"])]
                if sum(1 if b > a else -1 for a, b in zip(heb, heb[1:]) if b != a) > 0:
                    text = get_display(text)            # visual -> logical
            first = chars[proj.index(min(proj))]
            span = max(line["spans"], key=lambda s: s["size"])
            rgb = span["color"]
            lines.append(TextLine(text, tuple(first["origin"]), span["size"],
                                  math.degrees(math.atan2(-dy, dx)),
                                  ((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)))
    return lines


def calibrate(p1, p2, real_length: float) -> float:
    """Scale factor from two picked points and the real distance between them.

    Pick the points in a DXF made with scale=1 (paper mm); with real_length in the target units
    the result is the `scale` to pass to pdf_to_dxf. In general: multiply the current scale by it.
    """
    measured = math.dist(p1, p2)
    if measured <= 0:
        raise ValueError("the two points coincide")
    return real_length / measured


def _bezier(p0, p1, p2, p3) -> list[tuple[float, float]]:
    """Cubic Bezier as a polyline (excluding p0); point count from the control polygon length."""
    length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    n = max(4, min(32, int(length / 4)))
    pts = []
    for i in range(1, n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def _chains(path: dict) -> list[tuple[list[tuple[float, float]], bool]]:
    """Path items -> list of (points, closed)."""
    chains: list[tuple[list, bool]] = []
    current: list = []
    for item in path["items"]:
        kind = item[0]
        if kind == "re":
            r = item[1]
            chains.append(([(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)], True))
            continue
        if kind == "qu":
            q = item[1]
            chains.append(([tuple(q.ul), tuple(q.ur), tuple(q.lr), tuple(q.ll)], True))
            continue
        start = tuple(item[1])
        if not current or math.dist(current[-1], start) > 1e-3:
            if len(current) > 1:
                chains.append((current, False))
            current = [start]
        if kind == "l":
            current.append(tuple(item[2]))
        elif kind == "c":
            current.extend(_bezier(*(tuple(p) for p in item[1:5])))
    if len(current) > 1:
        chains.append((current, bool(path.get("closePath"))))
    result = []
    for pts, closed in chains:
        if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-3:
            pts, closed = pts[:-1], True
        result.append((pts, closed))
    return result


def _layer(doc, rgb: tuple[int, int, int], prefix: str = "PDF") -> str:
    """Layer per colour: PDF_<hex>; black/near-black uses ACI 7 (black on white, white on black)."""
    name = f"{prefix}_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    if name not in doc.layers:
        layer = doc.layers.add(name, color=7)
        if max(rgb) > 40:
            layer.rgb = rgb
    return name


def _lineweight(width_pt: float | None) -> int:
    """PDF line width (points) -> nearest valid DXF lineweight (1/100 mm, paper size)."""
    if width_pt is None:
        return -1                                            # BYLAYER
    w = width_pt * PT_MM * 100
    return min(VALID_DXF_LINEWEIGHTS, key=lambda v: abs(v - w))


def _rgb(color) -> tuple[int, int, int]:
    return tuple(round(c * 255) for c in color[:3]) if color else (0, 0, 0)


def pdf_to_dxf(pdf: Path | str, out: Path | str, page: int = 0, scale: float = 1.0,
               units: str = "mm", raster_fallback: bool = True) -> Path:
    """Convert one PDF page to DXF at 1:scale. Scanned pages (almost no vector paths) get a
    PNG rendered next to the DXF, inserted as an IMAGE underlay with a warning note."""
    if units not in ("mm", "m"):
        raise ValueError("units must be 'mm' or 'm'")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    k = PT_MM * scale / (1000 if units == "m" else 1)       # PDF points -> model units

    with pymupdf.open(pdf) as src:
        pg = src[page]
        rot = pg.rotation_matrix                              # unrotated -> displayed page
        height = pg.rect.height

        def xy(p) -> tuple[float, float]:
            q = pymupdf.Point(p) * rot
            return q.x * k, (height - q.y) * k

        doc = ezdxf.new("R2018", setup=True, units=4 if units == "mm" else 6)
        doc.header["$MEASUREMENT"] = 1
        doc.header["$LTSCALE"] = scale / (1000 if units == "m" else 1)
        if "ARIAL" not in doc.styles:
            doc.styles.add("ARIAL", font="arial.ttf")
        msp = doc.modelspace()

        paths = [d for d in pg.get_drawings()
                 if d.get("color") is not None or (d.get("fill") and _rgb(d["fill"]) != (255, 255, 255))]
        for d in paths:
            attribs = {"layer": _layer(doc, _rgb(d.get("color") or d.get("fill"))),
                       "lineweight": _lineweight(d.get("width") if d.get("color") else None)}
            if d.get("dashes") and not d["dashes"].startswith("[]"):
                attribs["linetype"] = "DASHED"
            for pts, closed in _chains(d):
                pts = [xy(p) for p in pts]
                if len(pts) == 2:
                    msp.add_line(pts[0], pts[1], dxfattribs=attribs)
                else:
                    msp.add_lwpolyline(pts, close=closed, dxfattribs=attribs)

        if "PDF_TEXT" not in doc.layers:
            doc.layers.add("PDF_TEXT", color=7)
        for line in text_lines(pg):
            attribs = {"layer": "PDF_TEXT", "style": "ARIAL", "height": line.size * ARIAL_CAP_HEIGHT * k,
                       "rotation": (line.angle - pg.rotation) % 360}
            text = msp.add_text(line.text.replace("Ø", "%%c"), dxfattribs=attribs)
            text.dxf.insert = xy(line.origin)
            if max(line.color) > 40:
                text.rgb = line.color

        if raster_fallback and len(paths) < MIN_VECTOR_PATHS:
            zoom = min(UNDERLAY_DPI / 72, UNDERLAY_MAX_PX / max(pg.rect.width, pg.rect.height))
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            png = out.with_name(f"{out.stem}_underlay.png")
            pix.save(png)
            w, h = pg.rect.width * k, pg.rect.height * k
            image_def = doc.add_image_def(filename=png.name, size_in_pixel=(pix.width, pix.height))
            doc.layers.add("PDF_UNDERLAY", color=8)
            msp.add_image(image_def, insert=(0, 0), size_in_units=(w, h),
                          dxfattribs={"layer": "PDF_UNDERLAY"})
            doc.layers.add("PDF_NOTE", color=1)
            note = msp.add_text("SCANNED PDF - RASTER UNDERLAY, NOT VECTOR LINES / "
                                "PDF סרוק - תמונת רקע, לא קווים",
                                dxfattribs={"layer": "PDF_NOTE", "style": "ARIAL", "height": 5 * k / PT_MM})
            note.dxf.insert = (0, h + 3 * note.dxf.height)
    doc.saveas(out)
    return out
