"""Project print set (improvement 8): sheet index + every document's latest drawing PDF, with bookmarks."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pymupdf

from . import pdfdoc
from .documents import STATUSES, list_documents

L = {
    "title": {"en": "DRAWING SET", "he": "סט שרטוטים"},
    "index": {"en": "Sheet index", "he": "רשימת גיליונות"},
    "code": {"en": "Code", "he": "קוד"},
    "rev": {"en": "Rev", "he": "מהדורה"},
    "doc_title": {"en": "Title", "he": "כותרת"},
    "status": {"en": "Status", "he": "סטטוס"},
    "page": {"en": "Page", "he": "עמוד"},
    "missing": {"en": "no PDF", "he": "אין PDF"},
    "date": {"en": "Date", "he": "תאריך"},
}


def build_print_set(project, out_pdf, *, lang: str | None = None, codes: list[str] | None = None,
                    statuses: list[str] | None = None) -> Path:
    """`codes` / `statuses` optionally filter the documents; documents are ordered by code."""
    lang = "he" if (lang or project.language_mode) == "he" else "en"
    rtl = lang == "he"
    tr = {k: v[lang] for k, v in L.items()}
    docs = [d for d in sorted(list_documents(project), key=lambda d: d.code)
            if (codes is None or d.code in codes) and (statuses is None or d.status in statuses)]
    pdfs = {d.code: d.path / d.outputs["pdf"] for d in docs
            if d.outputs.get("pdf") and (d.path / d.outputs["pdf"]).exists()}

    def index_html(pages: dict[str, int]) -> str:
        rows = [[i + 1, d.code, d.rev, d.display_title(lang), STATUSES.get(d.status, {}).get(lang, d.status),
                 pages.get(d.code, tr["missing"])] for i, d in enumerate(docs)]
        return (f"<h1>{pdfdoc.txt(tr['title'], rtl)}</h1>"
                f"<p>{pdfdoc.txt(f'{project.code} - {project.display_name(lang)}', rtl)}</p>"
                f"<p class='muted'>{pdfdoc.txt(project.client, rtl)} | {date.today().isoformat()}</p>"
                f"<h2>{pdfdoc.txt(tr['index'], rtl)}</h2>"
                + pdfdoc.table(["#", tr["code"], tr["rev"], tr["doc_title"], tr["status"], tr["page"]], rows, rtl))

    # the index length decides the start page of every sheet, so lay it out twice
    n_index = len(pdfdoc.html_to_pdf(index_html({}), rtl=rtl))
    pages, cursor = {}, n_index + 1
    for code, path in pdfs.items():
        pages[code] = cursor
        cursor += len(pymupdf.open(path))
    out = pdfdoc.html_to_pdf(index_html(pages), rtl=rtl)
    toc = [[1, tr["index"], 1]]
    for d in docs:
        if d.code in pdfs:
            toc.append([1, f"{d.code} Rev {d.rev} - {d.display_title(lang)}", len(out) + 1])
            pdfdoc.append_pdf(out, pdfs[d.code])
    out.set_toc(toc)
    pdfdoc.stamp_footer(out, f"{project.code} - DRAWING SET")
    return pdfdoc.save(out, out_pdf)
