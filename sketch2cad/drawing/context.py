"""Drawing context: the DXF document, layers, styles, unit/scale conversions and catalog lookups.

Units: geometry in the spec is in drawing units ("mm" or "m"); catalog dimensions are always mm;
text sizes in the spec/style are paper mm. ctx.k converts paper mm -> drawing units at the sheet scale,
ctx.u converts catalog mm -> drawing units.
"""
from __future__ import annotations

import ezdxf

from .text import STYLE

STATUS_LAYERS = {"existing": "EXISTING", "new": "NEW", "proposed": "PROPOSED"}
LAYERS = (  # name, ACI color, linetype, lineweight (1/100 mm)
    ("EXISTING", 8, "CONTINUOUS", 25),
    ("NEW", 1, "CONTINUOUS", 35),
    ("PROPOSED", 1, "CONTINUOUS", 35),
    ("DIM", 7, "CONTINUOUS", 13),
    ("TEXT", 7, "CONTINUOUS", 18),
    ("LEGEND", 7, "CONTINUOUS", 18),
    ("FRAME", 7, "CONTINUOUS", 25),
    ("HATCH", 8, "CONTINUOUS", 9),
    ("CENTER", 8, "CENTER", 13),
)
DIMSTYLE = "SKETCH2CAD"
LTSCALE_FACTOR = 2.5                   # DASHED (1.27 dash) -> ~3 mm dashes on paper
DEFAULT_STYLE = {                      # paper mm
    "text_height": 2.5,                # labels, free text
    "dim_text_height": 2.0,
    "table_text_height": 1.8,
    "legend_title_height": 3.0,
    "notes_text_height": 2.0,
    "notes_width": 100.0,
    "balloon_radius": 3.0,
}
UNITS = {"mm": (ezdxf.units.MM, 1.0), "m": (ezdxf.units.M, 0.001)}


class _NoCatalog:
    """Stand-in when no catalog is available: every lookup misses."""

    def get(self, item_id):
        return None


class DrawContext:
    """Everything the element drawers share for one sheet."""

    def __init__(self, spec: dict, catalog, language: str, warnings: list[str]):
        self.units = spec.get("units", "mm")
        if self.units not in UNITS:
            raise ValueError(f"units must be 'mm' or 'm', not {self.units!r}")
        insunits, self.u = UNITS[self.units]
        self.scale = float(spec.get("scale", 1))
        self.k = self.scale * self.u
        self.language = language
        self.style = {**DEFAULT_STYLE, **spec.get("style", {})}
        self.catalog = catalog or _NoCatalog()
        self.warnings = warnings
        self._missing: set[str] = set()

        self.doc = ezdxf.new("R2018", setup=True)
        self.doc.units = insunits
        self.doc.header["$MEASUREMENT"] = 1
        self.doc.header["$LTSCALE"] = LTSCALE_FACTOR * self.k
        self.msp = self.doc.modelspace()
        self._setup_tables()

    # ------------------------------------------------------------------ setup
    def _setup_tables(self) -> None:
        self.doc.styles.add(STYLE, font="arial.ttf")
        for name, color, linetype, lw in LAYERS:
            self.doc.layers.add(name, color=color, linetype=linetype, lineweight=lw)
        ds = self.doc.dimstyles.duplicate_entry("EZDXF", DIMSTYLE)
        k = self.k
        ds.dxf.update({
            "dimlfac": 1.0,             # EZDXF ships with dimlfac=100 (values would show x100)
            "dimscale": 1.0,
            "dimtxt": self.style["dim_text_height"] * k,
            "dimasz": 1.6 * k, "dimexe": 1.2 * k, "dimexo": 1.0 * k, "dimgap": 0.6 * k,
            "dimtad": 1, "dimtsz": 0.0, "dimtxsty": STYLE,
            "dimdec": 0 if self.units == "mm" else 2,
        })

    # ------------------------------------------------------------------ conversions
    def paper(self, mm: float) -> float:
        """Paper mm -> drawing units."""
        return mm * self.k

    def text_h(self, key: str) -> float:
        return self.style[key] * self.k

    # ------------------------------------------------------------------ catalog
    def item(self, item_id: str | None) -> dict:
        """Catalog entry or {} (a missing item is reported once, never fatal)."""
        if not item_id:
            return {}
        entry = self.catalog.get(item_id)
        if entry is None and item_id not in self._missing:
            self._missing.add(item_id)
            self.warnings.append(f"catalog item '{item_id}' not found - using element values / defaults")
        return entry or {}

    def size(self, el: dict, key: str, path: tuple[str, ...], default_mm: float,
             item: dict | None = None) -> float:
        """Element value (drawing units) -> catalog value at path (mm) -> default (mm).

        `item` overrides the catalog entry to read (default: the element's own `item`)."""
        if el.get(key) is not None:
            return float(el[key])
        value = self.item(el.get("item")) if item is None else item
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, (int, float)):
            return float(value) * self.u
        return default_mm * self.u

    # ------------------------------------------------------------------ entity attributes
    def attribs(self, el: dict, status: str | None = None) -> dict:
        """Layer by status; below-grade elements get the DASHED linetype."""
        layer = STATUS_LAYERS.get(status or el.get("status", "new"), "NEW")
        attribs = {"layer": layer}
        if el.get("below_grade"):
            attribs["linetype"] = "DASHED"
        return attribs

    def block(self, name: str, build) -> str:
        """Create a block once (entities on layer 0 / BYBLOCK so inserts take status layer + linetype)."""
        if name not in self.doc.blocks:
            build(self.doc.blocks.new(name))
        return name
