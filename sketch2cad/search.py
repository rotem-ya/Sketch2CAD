"""Search index (SQLite FTS5) over all registered projects' documents.

The index is a cache: it lives in the app-data folder and can always be rebuilt from the
project folders (rebuild()).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, app_home
from .documents import Document, list_documents
from .projects import Project, is_project_folder

SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    project_code, project_folder UNINDEXED, code, title_he, title_en, description, tags,
    discipline, type, status, content, aliases, folder UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


@dataclass
class Hit:
    project_code: str
    project_folder: str
    code: str
    title_he: str
    title_en: str
    description: str
    discipline: str
    type: str
    status: str
    folder: str


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or app_home() / "index.db")
    cols = [r[1] for r in con.execute("PRAGMA table_info(docs)")]
    if cols and "aliases" not in cols:                      # index from an older version: rebuild
        con.execute("DROP TABLE docs")
    con.executescript(SCHEMA)
    return con


HE_PREFIXES = set("והבכלמש")


def hebrew_variants(text: str) -> str:
    """Hebrew attaches prepositions/articles to words (לגמל, והשוחה). Index the stripped forms too,
    so a search for "גמל" or "שוחה" finds them."""
    out = []
    for word in text.split():
        w = word.strip(".,;:()[]\"'")
        if not any("\u0590" <= ch <= "\u05ff" for ch in w):
            continue
        for k in (1, 2, 3):
            if len(w) - k >= 2 and all(ch in HE_PREFIXES for ch in w[:k]):
                out.append(w[k:])
    return " ".join(out)


CONTENT_FILE = "content.txt"


def stored_content(doc: Document) -> str:
    """Text extracted from the document's files (kept in content.txt so a rebuild does not lose it)."""
    f = doc.path / CONTENT_FILE
    return f.read_text(encoding="utf-8") if f.exists() else ""


def store_content(doc: Document, name: str, text: str) -> None:
    """Add/replace the extracted text of one file in the document's content.txt."""
    blocks = [b for b in stored_content(doc).split("\n\f") if b and not b.startswith(f"[{name}]\n")]
    if text.strip():
        blocks.append(f"[{name}]\n{text.strip()}")
    (doc.path / CONTENT_FILE).write_text("\n\f".join(blocks), encoding="utf-8")


def _row(project: Project, doc: Document, content: str = "") -> tuple:
    content = content or stored_content(doc)
    he_text = " ".join([doc.title.get("he", ""), doc.description, " ".join(doc.tags), content])
    return (project.code, project.folder, doc.code, doc.title.get("he", ""), doc.title.get("en", ""),
            doc.description, " ".join(doc.tags), doc.discipline, doc.type, doc.status, content,
            hebrew_variants(he_text), doc.folder)


def index_document(project: Project, doc: Document, content: str = "", db_path: Path | None = None) -> None:
    con = _connect(db_path)
    with con:
        con.execute("DELETE FROM docs WHERE folder = ?", (doc.folder,))
        con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _row(project, doc, content))
    con.close()


def rebuild(settings: Settings | None = None, db_path: Path | None = None) -> int:
    """Re-index every document of every registered project. Returns the number of documents."""
    settings = settings or Settings.load()
    con = _connect(db_path)
    n = 0
    with con:
        con.execute("DELETE FROM docs")
        for folder in settings.projects:
            if not is_project_folder(folder):
                continue
            project = Project.load(folder)
            for doc in list_documents(project):
                con.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", _row(project, doc))
                n += 1
    con.close()
    return n


def _fts_query(text: str) -> str:
    """Free text -> FTS5 query: every word must match, as a prefix ("cam" finds "CAMEL")."""
    words = [w.replace('"', "") for w in text.split() if w.strip()]
    return " ".join(f'"{w}"*' for w in words)


def search(text: str = "", *, project_code: str | None = None, discipline: str | None = None,
           type: str | None = None, status: str | None = None, limit: int = 200,
           db_path: Path | None = None) -> list[Hit]:
    con = _connect(db_path)
    where, args = [], []
    if text.strip():
        where.append("docs MATCH ?")
        args.append(_fts_query(text))
    for col, val in (("project_code", project_code), ("discipline", discipline), ("type", type),
                     ("status", status)):
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    sql = ("SELECT project_code, project_folder, code, title_he, title_en, description, discipline, type, "
           "status, folder FROM docs")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (" ORDER BY rank" if text.strip() else " ORDER BY project_code, code") + " LIMIT ?"
    args.append(limit)
    rows = con.execute(sql, args).fetchall()
    con.close()
    return [Hit(*r) for r in rows]
