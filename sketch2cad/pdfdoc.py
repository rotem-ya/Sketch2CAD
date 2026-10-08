"""HTML → PDF helpers (PyMuPDF Story) with Hebrew RTL handling, shared by the package and print set.

MuPDF picks paragraph direction from the first strong character and ignores dir="rtl", so RTL cells
start with RLM, Latin/number runs are wrapped in LRE…PDF, and RTL table columns are reversed by the caller.
"""
from __future__ import annotations

import html
import io
import re
from pathlib import Path

import pymupdf

RLM, LRE, PDF = "‏", "‪", "‬"
HEB = re.compile(r"[֐-׿]")
LATIN_RUN = re.compile(r"[A-Za-z0-9Ø%][A-Za-z0-9Ø%\"' ./_:+×x()-]*[A-Za-z0-9)]|[A-Za-z0-9]")
A4 = pymupdf.paper_rect("a4")
A3L = pymupdf.paper_rect("a3-l")

CSS = """
* {font-family: sans-serif;}
body {font-size: 9pt;}
h1 {font-size: 16pt; margin: 0 0 4pt 0;}
h2 {font-size: 12pt; margin: 10pt 0 4pt 0; color: #1F4E79;}
p {margin: 2pt 0;}
table {border-collapse: collapse; width: 100%; margin: 4pt 0;}
th {background-color: #1F4E79; color: white; border: 0.5pt solid #1F4E79; padding: 3pt; font-size: 8pt;}
td {border: 0.5pt solid #888; padding: 3pt; font-size: 8pt; vertical-align: top;}
.rtl, .rtl td, .rtl th {text-align: right;}
.muted {color: #666;}
.error {color: #B00020; font-weight: bold;}
.warning {color: #B26A00; font-weight: bold;}
.info {color: #1F4E79;}
.ok {color: #2E7D32; font-weight: bold;}
.sign td {height: 28pt;}
"""


def has_hebrew(text) -> bool:
    return bool(HEB.search(str(text)))


def txt(value, rtl: bool = False) -> str:
    """Escape text for HTML; in RTL context keep codes/numbers left-to-right."""
    s = "" if value is None else str(value)
    if not rtl and not has_hebrew(s):
        return html.escape(s)
    out = LATIN_RUN.sub(lambda m: LRE + m.group(0) + PDF, s)
    return (RLM if rtl else "") + html.escape(out)


def table(headers: list[str] | None, rows: list[list], rtl: bool = False, classes: list[str] | None = None) -> str:
    """HTML table (no header row when `headers` is None); columns are reversed in RTL mode.
    `classes` optionally styles each row."""
    order = (lambda r: list(reversed(r))) if rtl else list
    head = "<tr>" + "".join(f"<th>{txt(h, rtl)}</th>" for h in order(headers)) + "</tr>" if headers else ""
    body = ""
    for i, row in enumerate(rows):
        cls = f' class="{classes[i]}"' if classes and classes[i] else ""
        body += f"<tr{cls}>" + "".join(f"<td>{txt(c, rtl)}</td>" for c in order(row)) + "</tr>"
    return f'<table class="{"rtl" if rtl else ""}">{head}{body}</table>'


def html_to_pdf(body: str, paper: pymupdf.Rect = A4, margin: float = 36, rtl: bool = False) -> pymupdf.Document:
    """Flow HTML over as many pages as needed."""
    wrapped = f'<div class="{"rtl" if rtl else ""}">{body}</div>'
    story = pymupdf.Story(html=wrapped, user_css=CSS)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    where = paper + (margin, margin, -margin, -margin - 14)
    more = True
    while more:
        dev = writer.begin_page(paper)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buf.getvalue())


def stamp_footer(doc: pymupdf.Document, left: str) -> None:
    """Footer on every page: `left` text and page i / n (Latin only)."""
    n = len(doc)
    for i, page in enumerate(doc):
        r = page.rect
        page.insert_text((24, r.height - 14), left, fontsize=7, color=(0.4, 0.4, 0.4))
        label = f"{i + 1} / {n}"
        page.insert_text((r.width - 24 - pymupdf.get_text_length(label, fontsize=7), r.height - 14), label,
                         fontsize=7, color=(0.4, 0.4, 0.4))


def append_pdf(target: pymupdf.Document, path: Path) -> int:
    """Append all pages of a PDF or image file; returns the number of pages added."""
    src = pymupdf.open(path)
    if not src.is_pdf:
        src = pymupdf.open("pdf", src.convert_to_pdf())
    target.insert_pdf(src)
    return len(src)


def save(doc: pymupdf.Document, out_pdf) -> Path:
    out = Path(out_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, garbage=3, deflate=True)
    return out
