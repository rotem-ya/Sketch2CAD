"""PDF / PNG export of a saved DXF through the ezdxf drawing add-on + matplotlib, at true paper size.

Hebrew rule for this path: the ezdxf add-on draws text as glyph outlines (filled paths) with its own font
engine and never reorders characters, and it does not use matplotlib's text rendering (which, from
matplotlib 3.11, shapes RTL text itself). So the preview copy of the drawing must hold Hebrew in VISUAL
order: text.to_visual() (python-bidi, after %%c -> Ø). The DXF on disk keeps logical order.
"""
from __future__ import annotations

from pathlib import Path

import ezdxf
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (backend must be chosen first)
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import (BackgroundPolicy, ColorPolicy, Configuration,  # noqa: E402
                                         LineweightPolicy)
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402
from ezdxf.npshapes import to_matplotlib_path  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402

from .text import font, to_visual  # noqa: E402

MM_PER_INCH = 25.4
MAX_PNG_PIXELS = 4800                 # longer side; A3 -> 200 dpi, A1 -> ~145 dpi


class _Backend(MatplotlibBackend):
    """Fills with the paths' own winding (nonzero) instead of ezdxf's hole detection.

    Glyph outlines carry correct TrueType winding; hole detection breaks glyphs with overlapping
    contours (Ø, %%c rendered as a filled blob). Our sheets have no solid-filled hatches with islands.
    """

    def draw_filled_paths(self, paths, properties):
        self.ax.add_patch(PathPatch(to_matplotlib_path(paths), color=properties.color, linewidth=0,
                                    fill=True, zorder=self._get_z()))


def preview_doc(dxf_path: Path) -> ezdxf.document.Drawing:
    """A fresh copy of the drawing with every text converted for display (see module docstring)."""
    doc = ezdxf.readfile(dxf_path)
    for block in doc.blocks:                      # model space, paper space and all block definitions
        for e in block.query("TEXT MTEXT ATTRIB"):
            if e.dxftype() == "MTEXT":
                e.text = to_visual(e.text)
            else:
                e.dxf.text = to_visual(e.dxf.text)
    return doc


def render_figure(doc, paper_rect, paper_mm):
    """Matplotlib figure of exactly the paper size showing model space clipped to the paper rectangle."""
    font()                                        # registers the Arial look-alike when needed
    w_in, h_in = paper_mm[0] / MM_PER_INCH, paper_mm[1] / MM_PER_INCH
    fig = plt.figure(figsize=(w_in, h_in))
    ax = fig.add_axes((0, 0, 1, 1))
    config = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.COLOR,
                           lineweight_policy=LineweightPolicy.ABSOLUTE)
    Frontend(RenderContext(doc), _Backend(ax, adjust_figure=False), config=config).draw_layout(doc.modelspace())
    x0, y0, x1, y1 = paper_rect
    ax.set_adjustable("box")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()
    return fig


def export(dxf_path: Path, outputs: dict[str, Path], paper_rect, paper_mm) -> None:
    """Write the requested previews: outputs = {"pdf": path, "png": path} (either may be missing)."""
    fig = render_figure(preview_doc(dxf_path), paper_rect, paper_mm)
    try:
        if "pdf" in outputs:
            fig.savefig(outputs["pdf"], facecolor="white")
        if "png" in outputs:
            dpi = min(200, MAX_PNG_PIXELS / (max(paper_mm) / MM_PER_INCH))
            fig.savefig(outputs["png"], dpi=dpi, facecolor="white")
    finally:
        plt.close(fig)
