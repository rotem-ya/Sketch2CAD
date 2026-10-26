"""Text helpers: Hebrew detection, language selection, measuring, wrapping and DXF text entities.

DXF/DWG keep Hebrew in LOGICAL order (AutoCAD applies bidi itself, verified); Hebrew strings are written
as MTEXT with a right-aligned paragraph (\\pxqr;). Only the PDF/PNG preview converts to visual order
(to_visual), after replacing %%c with Ø.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from bidi.algorithm import get_display
from ezdxf.fonts import fonts
from ezdxf.enums import TextEntityAlignment

from .standards import ISO_HEIGHTS, snap_text_height

STYLE = "ARIAL"
FONT = "arial.ttf"
_BUNDLED_FONT = Path(__file__).resolve().parent / "fonts" / "LiberationSans-Regular.ttf"
WIDTH_MARGIN = 1.05
RTL_PARA = "\\pxqr;"
LINE_SPACING = 1.667                      # MTEXT default line pitch, x char height
HEBREW = re.compile(r"[\u0590-\u05FF]")
SPECIAL = (("%%c", "Ø"), ("%%C", "Ø"), ("%%d", "°"), ("%%D", "°"), ("%%p", "±"), ("%%P", "±"))

# short names accepted in specs/templates -> ezdxf alignment
ALIGN_NAMES = {"left": "LEFT", "center": "CENTER", "right": "RIGHT", "middle": "MIDDLE_CENTER"}
# TEXT alignment -> MTEXT attachment point (1..9 = top/middle/bottom x left/center/right)
ATTACHMENT = {
    "TOP_LEFT": 1, "TOP_CENTER": 2, "TOP_RIGHT": 3,
    "MIDDLE_LEFT": 4, "MIDDLE_CENTER": 5, "MIDDLE": 5, "MIDDLE_RIGHT": 6,
    "BOTTOM_LEFT": 7, "LEFT": 7, "ALIGNED": 7, "FIT": 7,
    "BOTTOM_CENTER": 8, "CENTER": 8, "BOTTOM_RIGHT": 9, "RIGHT": 9,
}


def has_hebrew(s: str) -> bool:
    return bool(HEBREW.search(s or ""))


def pick(text, lang: str) -> list[str]:
    """Strings to show for a {en, he} dict (or a plain string) in language mode en | he | both."""
    if not text:
        return []
    if isinstance(text, str):
        return [text]
    en, he = text.get("en") or "", text.get("he") or ""
    if lang == "en":
        return [en or he] if en or he else []
    if lang == "he":
        return [he or en] if en or he else []
    return [s for s in (en, he) if s]


def plain(s: str) -> str:
    """Replace AutoCAD %% codes by their Unicode characters."""
    for code, char in SPECIAL:
        s = s.replace(code, char)
    return s


# Names ezdxf asks for when a style is missing. The live gate below is what stops a system
# file: these aliases only cover the names we already know.
_PINNED_FONT_NAMES = (
    FONT,
    "txt",
    "ArialUni.ttf",
    "Arial Unicode.ttf",
    "Arial.ttf",
    "DejaVuSansCondensed.ttf",
    "DejaVuSans.ttf",
    "OpenSans-Regular.ttf",
    "LiberationSans-Regular.ttf",
)
# The one CacheEntry every resolution returns. A list so the gate can be installed once.
_BUNDLED_ENTRY: list = []


def _pin_measurement_font() -> None:
    """Measure every text lookup with the bundled Liberation Sans.

    The DXF style name stays ``arial.ttf``. A document with no such style is measured as ``txt``.
    On Windows ezdxf maps that to ``txt_____.ttf`` and would open ``C:\\WINDOWS\\Fonts\\txt_____.ttf``.
    Font resolution itself — any name, any SHX-to-TTF mapping — returns the bundled file, and the
    TrueType open never reads another path.
    """
    from fontTools.ttLib import TTFont, TTLibError
    from ezdxf.fonts.font_manager import FontCache, FontNotFoundError

    manager = fonts.font_manager
    manager.scan_folder(_BUNDLED_FONT.parent)
    raw = manager._font_cache._cache
    entry = raw.get(FontCache.key(_BUNDLED_FONT.name))
    if entry is None or Path(entry.file_path).resolve() != _BUNDLED_FONT.resolve():
        raise FileNotFoundError(_BUNDLED_FONT)
    _BUNDLED_ENTRY[:] = [entry]
    for name in _PINNED_FONT_NAMES:
        raw[FontCache.key(name)] = entry
    manager._fallback_font_name = _BUNDLED_FONT.name

    if not getattr(FontCache, "_sketch2cad_resolves_to_bundle", False):
        def _resolve(self, font_name: str, fallback: str = ""):
            del self, font_name, fallback
            return _BUNDLED_ENTRY[0]

        def _resolve_item(self, item: str):
            del self, item
            return _BUNDLED_ENTRY[0]

        FontCache.get = _resolve          # type: ignore[method-assign]
        FontCache.__getitem__ = _resolve_item  # type: ignore[method-assign]
        FontCache._sketch2cad_resolves_to_bundle = True

    if not getattr(manager, "_sketch2cad_ttf_pinned", False):
        def get_ttf_font(font_name: str, font_number: int = 0):
            try:
                return manager._loaded_ttf_fonts[font_name]
            except KeyError:
                pass
            try:
                font = TTFont(str(_BUNDLED_FONT), fontNumber=font_number)
            except (OSError, TTLibError) as exc:
                raise FontNotFoundError(str(exc)) from exc
            manager._loaded_ttf_fonts[font_name] = font
            return font

        manager.get_ttf_font = get_ttf_font
        manager._sketch2cad_ttf_pinned = True

    match_cache = getattr(manager, "_match_cache", None)
    if isinstance(match_cache, dict):
        match_cache.clear()
    loaded = getattr(manager, "_loaded_ttf_fonts", None)
    if isinstance(loaded, dict):
        loaded.clear()
    glyphs = getattr(fonts.TrueTypeFont, "_glyph_caches", None)
    if isinstance(glyphs, dict):
        glyphs.clear()


_pin_measurement_font()


@lru_cache(maxsize=1)
def font():
    """Bundled Liberation Sans metrics at cap height 1, under the ARIAL style name."""
    return fonts.make_font(FONT, 1.0)


def text_width(s: str, h: float) -> float:
    """Width of a single line at text (cap) height h, with a small safety margin for AutoCAD's metrics."""
    return font().text_width(plain(s)) * h * WIDTH_MARGIN


def wrap(s: str, h: float, width: float) -> list[str]:
    """Greedy word wrap to the estimated width (explicit line breaks keep AutoCAD and preview identical)."""
    lines, cur = [], ""
    for word in s.split():
        cand = f"{cur} {word}" if cur else word
        if cur and text_width(cand, h) > width:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    return lines + [cur] if cur else lines


def alignment(name: str | None) -> TextEntityAlignment:
    key = (name or "left").strip()
    key = ALIGN_NAMES.get(key.lower(), key.upper())
    return TextEntityAlignment[key]


def put_text(msp, s: str, at, h: float, *, layer: str = "TEXT", align: str | None = "left",
             rotation: float = 0.0, nowrap: bool = False, box_width: float | None = None):
    """Add text: TEXT for a plain single line, MTEXT for Hebrew (right-aligned paragraph) or multi-line."""
    al = alignment(align)
    attribs = {"layer": layer, "style": STYLE}
    if has_hebrew(s) or "\\P" in s:
        content = RTL_PARA + s if has_hebrew(s) else s
        # width 0: one line, AutoCAD does not reflow it. Otherwise the column is the measured line.
        # box_width gives that one line a real column, so the bbox matches the glyphs.
        if box_width is not None:
            width = float(box_width)
        else:
            width = 0.0 if nowrap else max(text_width(line, h) for line in s.split("\\P"))
        m = msp.add_mtext(content, dxfattribs={**attribs, "char_height": h, "width": width})
        m.set_location(at, rotation=rotation, attachment_point=ATTACHMENT[al.name])
        return m
    t = msp.add_text(s, height=h, rotation=rotation, dxfattribs=attribs)
    t.set_placement(at, align=al)
    return t


def to_visual(s: str) -> str:
    """Preview only: %% codes -> Unicode, then Hebrew paragraphs -> visual order (python-bidi)."""
    s = plain(s)
    if not has_hebrew(s):
        return s
    prefix = RTL_PARA if s.startswith(RTL_PARA) else ""
    body = s[len(prefix):]
    return prefix + "\\P".join(get_display(p) for p in body.split("\\P"))
