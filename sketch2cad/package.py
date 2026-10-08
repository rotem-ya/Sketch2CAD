"""One-click submittal package (improvement 3): transmittal, compliance matrix, checks, BOM, drawing, datasheets."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pymupdf

from . import pdfdoc
from .bom import build_bom
from .checks import run_checks
from .documents import STATUSES

L = {
    "title": {"en": "SUBMITTAL PACKAGE", "he": "תיק הגשה"},
    "transmittal": {"en": "Transmittal", "he": "מכתב העברה"},
    "project": {"en": "Project", "he": "פרויקט"},
    "client": {"en": "Client", "he": "מזמין"},
    "document": {"en": "Document", "he": "מסמך"},
    "rev": {"en": "Rev", "he": "מהדורה"},
    "date": {"en": "Date", "he": "תאריך"},
    "status": {"en": "Status", "he": "סטטוס"},
    "contents": {"en": "Contents", "he": "תוכן"},
    "revisions": {"en": "Revision history", "he": "היסטוריית מהדורות"},
    "note": {"en": "Note", "he": "הערה"},
    "matrix": {"en": "Compliance matrix", "he": "טבלת התאמה למפרט"},
    "checks": {"en": "Engineering checks", "he": "בדיקות הנדסיות"},
    "bom": {"en": "Bill of materials", "he": "כתב כמויות"},
    "drawing": {"en": "Drawing", "he": "שרטוט"},
    "datasheets": {"en": "Datasheets", "he": "דפי קטלוג"},
    "item": {"en": "Item", "he": "פריט"},
    "desc": {"en": "Description", "he": "תיאור"},
    "submittal": {"en": "Submittal", "he": "הגשה"},
    "documented": {"en": "Documented dims", "he": "מידות מתועדות"},
    "missing": {"en": "Without source", "he": "ללא מקור"},
    "attached": {"en": "Datasheet", "he": "דף קטלוג"},
    "yes": {"en": "attached", "he": "מצורף"},
    "no": {"en": "not found", "he": "לא נמצא"},
    "severity": {"en": "Severity", "he": "חומרה"},
    "finding": {"en": "Finding", "he": "ממצא"},
    "element": {"en": "Element", "he": "אלמנט"},
    "no_findings": {"en": "No findings.", "he": "אין ממצאים."},
    "qty": {"en": "Qty", "he": "כמות"},
    "unit": {"en": "Unit", "he": "יחידה"},
    "missing_pdf": {"en": "Drawing PDF not generated yet.", "he": "קובץ PDF של השרטוט עוד לא הופק."},
    "prepared": {"en": "Prepared by", "he": "הוכן על ידי"},
    "approved": {"en": "Approved by", "he": "אושר על ידי"},
}


def load_spec(document) -> dict | None:
    path = document.path / "spec.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def find_source_file(name: str, project, document) -> Path | None:
    """Look for a datasheet by file name in the document sources, then anywhere in the project folder."""
    if not name:
        return None
    low = name.lower()
    for root in (document.path / "sources", project.path):
        if root.exists():
            for f in root.rglob("*"):
                if f.is_file() and f.name.lower() == low:
                    return f
    return None


def _items(spec, catalog) -> list[dict]:
    ids = []
    for el in spec.get("elements", []) + spec.get("legend", []):
        if el.get("item") and el["item"] not in ids:
            ids.append(el["item"])
    return [catalog.get(i) or {"id": i} for i in ids]


def build_package(project, document, catalog, out_pdf, *, lang: str | None = None, spec: dict | None = None) -> Path:
    lang = lang or document.language_mode or project.language_mode or "en"
    lang = "he" if lang == "he" else "en"
    rtl = lang == "he"
    tr = {k: v[lang] for k, v in L.items()}
    t = lambda s: pdfdoc.txt(s, rtl)
    spec = spec if spec is not None else load_spec(document) or {}
    items = _items(spec, catalog) if catalog else []
    findings = run_checks(spec, catalog) if spec else []
    bom = build_bom(spec, catalog) if spec else []
    sheets = {i["id"]: find_source_file((i.get("source") or {}).get("file", ""), project, document) for i in items}

    # --- transmittal
    html = f"<h1>{t(tr['title'])}</h1><p class='muted'>{t(project.display_name(lang))}</p>"
    html += pdfdoc.table(None, [
        [tr["project"], f"{project.code} - {project.display_name(lang)}"],
        [tr["client"], project.client],
        [tr["document"], f"{document.code} - {document.display_title(lang)}"],
        [tr["rev"], document.rev],
        [tr["date"], date.today().isoformat()],
        [tr["status"], STATUSES.get(document.status, {}).get(lang, document.status)],
    ], rtl)
    if document.description:
        html += f"<p>{t(document.description)}</p>"
    contents = [tr["matrix"], tr["checks"], tr["bom"], tr["drawing"]]
    if any(sheets.values()):
        contents.append(f"{tr['datasheets']} ({sum(1 for v in sheets.values() if v)})")
    html += f"<h2>{t(tr['contents'])}</h2>" + "".join(f"<p>{t(f'{i + 1}. {c}')}</p>" for i, c in enumerate(contents))
    if document.revisions:
        html += f"<h2>{t(tr['revisions'])}</h2>" + pdfdoc.table(
            [tr["rev"], tr["date"], tr["note"]], [[r["rev"], r["date"], r.get("note", "")] for r in document.revisions], rtl)
    html += pdfdoc.table([tr["prepared"], tr["approved"]], [["", ""]], rtl, ["sign"])

    # --- compliance matrix
    html += f"<h2>{t(tr['matrix'])}</h2>"
    rows = []
    for it in items:
        src = it.get("source") or {}
        missing = catalog.missing_source(it) if "name" in it else ["?"]
        rows.append([it["id"], (it.get("name") or {}).get(lang, ""),
                     src.get("submittal", "-"), src.get("status", "-"), ", ".join(src.get("fields") or []) or "-",
                     ", ".join(missing) or "-", tr["yes"] if sheets.get(it["id"]) else tr["no"]])
    html += pdfdoc.table([tr["item"], tr["desc"], tr["submittal"], tr["status"], tr["documented"], tr["missing"],
                          tr["attached"]], rows, rtl)

    # --- checks
    html += f"<h2>{t(tr['checks'])}</h2>"
    if findings:
        html += pdfdoc.table([tr["severity"], tr["finding"], tr["element"]],
                             [[f.severity.upper(), getattr(f, lang), f.element] for f in findings], rtl,
                             [f.severity for f in findings])
    else:
        html += f"<p class='ok'>{t(tr['no_findings'])}</p>"

    # --- BOM
    html += f"<h2>{t(tr['bom'])}</h2>" + pdfdoc.table(
        ["No", tr["item"], tr["desc"], tr["qty"], tr["unit"], tr["submittal"]],
        [[r["no"], r["item"], r[lang] or r["en"], r["qty"], r["unit"], r["source"]] for r in bom], rtl)

    pdf = pdfdoc.html_to_pdf(html, rtl=rtl)

    # --- drawing + datasheets
    drawing = document.outputs.get("pdf")
    if drawing and (document.path / drawing).exists():
        pdfdoc.append_pdf(pdf, document.path / drawing)
    else:
        pdf.insert_pdf(pdfdoc.html_to_pdf(f"<h2>{t(tr['drawing'])}</h2><p>{t(tr['missing_pdf'])}</p>", rtl=rtl))
    for path in dict.fromkeys(p for p in sheets.values() if p):
        try:
            pdfdoc.append_pdf(pdf, path)
        except (RuntimeError, ValueError, pymupdf.FileDataError):
            continue
    pdfdoc.stamp_footer(pdf, f"{document.code} Rev {document.rev} - {project.code}")
    return pdfdoc.save(pdf, out_pdf)
