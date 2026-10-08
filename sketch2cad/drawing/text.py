"""Text helpers: Hebrew detection, language selection, measuring, wrapping and DXF text entities.

DXF/DWG keep Hebrew in LOGICAL order (AutoCAD applies bidi itself, verified); Hebrew strings are written
as MTEXT with a right-aligned paragraph (\\pxqr;). Only the PDF/PNG preview converts to visual order
(to_visual), after replacing %%c with Ø.
"""
from __future__ import annotations

import re
from functools import lru_cache

from bidi.algorithm import get_display
from ezdxf.fonts import fonts
from ezdxf.enums import TextEntityAlignment

STYLE = "ARIAL"
FONT = "arial.ttf"
FALLBACK_FONTS = ("LiberationSans-Regular.ttf", "DejaVuSans.ttf")
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


@lru_cache(maxsize=1)
def font():
    """Arial metrics at cap height 1 via ezdxf; without Arial (Linux) use metric-compatible Liberation Sans."""
    fm = fonts.font_manager
    if not fm.has_font(FONT):
        for name in FALLBACK_FONTS:
            if fm.has_font(name):
                fm.add_synonyms({name: FONT}, reverse=False)
                break
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
             rotation: float = 0.0):
    """Add text: TEXT for a plain single line, MTEXT for Hebrew (right-aligned paragraph) or multi-line."""
    al = alignment(align)
    attribs = {"layer": layer, "style": STYLE}
    if has_hebrew(s) or "\\P" in s:
        content = RTL_PARA + s if has_hebrew(s) else s
        width = max(text_width(line, h) for line in s.split("\\P"))      # explicit width: never re-wrapped
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
