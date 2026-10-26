"""Submission rules for the DXF we hand to a utility checker.

ezdxf cannot create annotative objects, so text height is real model-space height
(paper height x scale) or, on a multi-view sheet, paper-space text at 1:1.
Colour and linetype are BYLAYER. An explicit lineweight, including a ground line, is kept.
Layers 0 and DEFPOINTS hold no geometry: symbol blocks carry the status layer, and
dimension arrow / definition-point leftovers are moved or removed after ezdxf builds them.
"""
from __future__ import annotations

ISO_HEIGHTS = (2.5, 3.5, 5.0, 7.0)   # ISO 3098 lettering, millimetres on paper
# Small annotation. It is not a general note, so it is not forced onto the ISO series.
RAW_STYLE_HEIGHTS = {"table_text_height"}
SYSTEM_LAYERS = {"0", "DEFPOINTS"}


def snap_text_height(mm: float, *, up: bool = False) -> float:
    """ISO 3098 paper height (2.5, 3.5, 5, 7 mm).

    The nearest size is used, and a tie takes the larger one. `up=True` never goes below
    the authored size (title-block titles): 3.29 → 3.5, 4.1 → 5, and a size above 7 stays.
    """
    value = abs(float(mm))
    if up:
        for height in ISO_HEIGHTS:
            if height + 1e-9 >= value:
                return height
        return value
    return min(ISO_HEIGHTS, key=lambda height: (abs(height - value), -height))


def apply_text_style(style: dict, *, explicit: set | None = None) -> dict:
    """Snap default style heights onto the ISO series.

    Heights the spec sets itself are kept exactly (`explicit`). `table_text_height`
    is left as authored either way. Template fields are snapped by the title block, not here.
    """
    explicit = explicit or set()
    out = {}
    for key, value in style.items():
        if key in explicit:
            out[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and "height" in key \
                and key not in RAW_STYLE_HEIGHTS:
            out[key] = snap_text_height(value)
        else:
            out[key] = value
    return out


def _layer(entity) -> str:
    if not entity.dxf.hasattr("layer"):
        return ""
    return str(entity.dxf.layer)


def _set(entity, attr: str, value) -> None:
    try:
        entity.dxf.set(attr, value)
    except (AttributeError, TypeError, ValueError):
        return


# DXF lineweights are hundredths of a millimetre. 0.50 mm is 50.
_DXF_WEIGHTS = (5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90,
                100, 106, 120, 140, 158, 200, 211)


def dxf_lineweight(value) -> int:
    """A paper thickness in millimetres (0.50) or a DXF hundredth (50) → the nearest DXF weight."""
    number = float(value)
    hundredths = number * 100.0 if abs(number) <= 2.11 else number
    return min(_DXF_WEIGHTS, key=lambda item: (abs(item - hundredths), item))


def _bylayer(entity) -> None:
    _set(entity, "color", 256)
    _set(entity, "linetype", "BYLAYER")
    # An explicit weight (a ground line at 0.50 mm, or a weight the element asked for) stays.
    # BYLAYER (-1) and an unset weight become BYLAYER.
    weight = entity.dxf.lineweight if entity.dxf.hasattr("lineweight") else None
    if weight is None or weight < 0:
        _set(entity, "lineweight", -1)


def _replacement(block_name: str, entity) -> str:
    if entity.dxftype() == "VIEWPORT":
        return "VIEWPORT"
    name = block_name.upper()
    if name.startswith("*D") or name.startswith("_"):
        return "DIM"
    return "NEW"


def apply_submission_standard(doc) -> None:
    """Force BYLAYER and clear layers 0 and DEFPOINTS of geometry."""
    for block in doc.blocks:
        remove = []
        for entity in block:
            layer = _layer(entity).upper()
            if entity.dxftype() == "POINT" and layer in SYSTEM_LAYERS:
                remove.append(entity)
                continue
            _bylayer(entity)
            if layer in SYSTEM_LAYERS:
                entity.dxf.layer = _replacement(block.name, entity)
        for entity in remove:
            block.delete_entity(entity)
    for name in ("Defpoints", "DEFPOINTS", "defpoints"):
        if name in doc.layers:
            try:
                doc.layers.remove(name)
            except (ValueError, KeyError):
                continue
