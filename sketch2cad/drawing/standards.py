"""Submission rules for the DXF we hand to a utility checker.

ezdxf cannot create annotative objects, so text height is real model-space height
(paper height x scale) or, on a multi-view sheet, paper-space text at 1:1.
Every entity is BYLAYER. Layers 0 and DEFPOINTS hold no geometry: symbol blocks
carry the status layer, and dimension arrow / definition-point leftovers are
moved or removed after ezdxf builds them.
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


def apply_text_style(style: dict) -> dict:
    """Snap style heights onto the ISO series. `table_text_height` is left as authored."""
    out = {}
    for key, value in style.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and "height" in key \
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


def _bylayer(entity) -> None:
    _set(entity, "color", 256)
    _set(entity, "linetype", "BYLAYER")
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
