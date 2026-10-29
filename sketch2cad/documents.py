"""Documents: one folder per document under <project>/10_Drawings/<code>/, described by document.json."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import codes
from .procutil import copy_file, ensure_dir, is_file, make_dir, read_text, write_bytes, write_text
from .projects import Project

DOC_FILE = "document.json"
DOC_TYPES = {
    "as-made": {"en": "As-Made", "he": "עדות / As-Made"},
    "shop-drawing": {"en": "Shop Drawing", "he": "שרטוט ביצוע"},
    "proposal": {"en": "Proposal", "he": "הצעה"},
    "detail": {"en": "Detail", "he": "פרט"},
    "import": {"en": "Imported", "he": "מיובא"},
}
STATUSES = {
    "draft": {"en": "Draft", "he": "טיוטה"},
    "submitted": {"en": "Submitted", "he": "הוגש"},
    "approved": {"en": "Approved", "he": "מאושר"},
    "rejected": {"en": "Rejected", "he": "נדחה"},
}


@dataclass
class Revision:
    rev: str
    date: str
    note: str = ""


@dataclass
class Document:
    code: str
    seq: int
    title: dict                                   # {"he": ..., "en": ...}
    folder: str                                   # absolute path of the document folder
    rev: str = "A"
    description: str = ""
    discipline: str = ""
    subject: str = ""
    type: str = "shop-drawing"
    tags: list = field(default_factory=list)
    status: str = "draft"
    language_mode: str = ""                       # "" = project default
    titleblock: str = ""                          # "" = project default
    title_overrides: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)   # paths relative to the document folder
    outputs: dict = field(default_factory=dict)   # {"dwg": rel, "dxf": rel, "pdf": rel, "preview": rel}
    finals: list = field(default_factory=list)    # outside AutoCAD copies; never replaces outputs
    linked_folder: str = ""                       # PC folder whose newest DWG can be filed as a final
    revisions: list = field(default_factory=list)
    created: str = field(default_factory=lambda: date.today().isoformat())
    updated: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def path(self) -> Path:
        return Path(self.folder)

    def display_title(self, lang: str) -> str:
        return self.title.get(lang) or self.title.get("en") or self.title.get("he") or self.code

    def revision_note(self) -> str:
        """The note recorded for the current revision, or empty."""
        current = str(self.rev).strip().upper()
        for item in self.revisions:
            if str(item.get("rev") or "").strip().upper() == current:
                return str(item.get("note") or "")
        return ""

    def badge_final(self) -> dict | None:
        """The final the drawings list should name: the current rev if one exists, else the latest."""
        if not self.finals:
            return None
        current = str(self.rev).strip().upper()
        same = [item for item in self.finals if str(item.get("rev") or "").strip().upper() == current]
        return (same or self.finals)[-1]

    def save(self) -> None:
        self.updated = datetime.now().isoformat(timespec="seconds")
        data = asdict(self)
        data.pop("folder")
        write_text(self.path / DOC_FILE, json.dumps(data, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, folder: Path | str) -> "Document":
        try:
            folder = Path(folder).resolve()
        except OSError:
            folder = Path(os.path.abspath(folder))
        data = json.loads(read_text(folder / DOC_FILE))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(folder=str(folder), **known)


def list_documents(project: Project) -> list[Document]:
    from .procutil import is_dir, is_file, long_path
    docs = []
    root = project.drawings_dir
    if not is_dir(root):
        return docs
    try:
        names = sorted(
            entry.name for entry in os.scandir(long_path(root)) if entry.is_dir()
        )
    except OSError:
        return docs
    for name in names:
        sub = root / name
        if is_file(sub / DOC_FILE):
            docs.append(Document.load(sub))
    return docs


def create_document(project: Project, *, title_he: str = "", title_en: str = "", discipline: str = "G",
                    subject: str = "", type: str = "shop-drawing", description: str = "",
                    tags: list | None = None, code: str | None = None) -> Document:
    """Create a document with the next free code from the project's pattern (or an explicit code)."""
    existing = [d.code for d in list_documents(project)]
    if code:
        if code in existing:
            raise FileExistsError(f"code {code} already exists in project {project.code}")
        seq = 0
    else:
        code, seq = codes.next_code(project.doc_code_pattern, existing, prj=project.code, disc=discipline,
                                    subj=subject, typ=type)
    folder = project.drawings_dir / code
    make_dir(folder)
    ensure_dir(folder / "sources")
    doc = Document(code=code, seq=seq, title={"he": title_he, "en": title_en}, folder=str(folder),
                   description=description, discipline=discipline, subject=subject, type=type,
                   tags=list(tags or []), revisions=[asdict(Revision("A", date.today().isoformat(), "Created"))])
    doc.save()
    return doc


def add_source(doc: Document, file_path: Path | str | None = None, *, name: str | None = None,
               data: bytes | None = None) -> str:
    """Copy a file (or raw bytes, e.g. an upload) into the document's sources/ folder."""
    if file_path is None and data is None:
        raise ValueError("file_path or data is required")
    name = name or Path(file_path).name
    dest = doc.path / "sources" / name
    stem, suffix, n = dest.stem, dest.suffix, 1
    while is_file(dest):                                   # never overwrite an existing source
        dest = dest.with_name(f"{stem}_{n}{suffix}")
        n += 1
    if data is not None:
        write_bytes(dest, data)
    else:
        copy_file(file_path, dest)
    rel = dest.relative_to(doc.path).as_posix()
    doc.sources.append(rel)
    doc.save()
    return rel


DELETED_SOURCES = "_deleted_sources"


def remove_source(doc: Document, rel: str) -> Path | None:
    """Take a source off the document. The file is never erased: it moves to
    <document>/_deleted_sources/<time>_<name>, outside sources/, so packages and the agent no longer see it.
    Returns the new location, or None when the file was already missing."""
    if rel not in doc.sources:
        raise ValueError(f"not a source of {doc.code}: {rel}")
    src = (doc.path / rel).resolve()
    moved = None
    if src.is_file() and src.is_relative_to(doc.path.resolve()):
        trash = doc.path / DELETED_SOURCES
        trash.mkdir(exist_ok=True)
        moved = trash / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{src.name}"
        shutil.move(str(src), str(moved))
    doc.sources.remove(rel)
    doc.save()
    return moved


def new_revision(doc: Document, project: Project, note: str = "") -> str:
    """Advance the revision letter; archive the current outputs to <project>/90_Archive/<code>_Rev<X>/."""
    if doc.outputs:
        archive = project.path / "90_Archive" / f"{doc.code}_Rev{doc.rev}"
        archive.mkdir(parents=True, exist_ok=True)
        for rel in doc.outputs.values():
            src = doc.path / rel
            if src.exists():
                shutil.copy2(src, archive / src.name)
    doc.rev = codes.next_rev(doc.rev)
    doc.revisions.append(asdict(Revision(doc.rev, date.today().isoformat(), note)))
    doc.save()
    return doc.rev
