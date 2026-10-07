"""Projects: one folder per project (fixed, relocatable), described by project.json.

<project folder>/
    project.json
    00_Sources/   10_Drawings/<doc code>/   20_Imports/   90_Archive/
The project folder is the source of truth; the search index is rebuilt from it.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .codes import DEFAULT_PATTERN

PROJECT_FILE = "project.json"
SUBFOLDERS = ("00_Sources", "10_Drawings", "20_Imports", "90_Archive")
DEFAULT_DISCIPLINES = {
    "W": {"en": "Water", "he": "מים"},
    "S": {"en": "Sewage", "he": "ביוב"},
    "D": {"en": "Drainage", "he": "ניקוז"},
    "E": {"en": "Electrical", "he": "חשמל"},
    "C": {"en": "Communication", "he": "תקשורת"},
    "R": {"en": "Roads & Paving", "he": "כבישים ופיתוח"},
    "G": {"en": "General", "he": "כללי"},
}


@dataclass
class Project:
    code: str
    name: dict                                  # {"he": ..., "en": ...}
    folder: str                                 # absolute path of the project folder
    client: str = ""
    language_mode: str = "both"                 # "en" | "he" | "both" (drawing text default)
    doc_code_pattern: str = DEFAULT_PATTERN
    default_titleblock: str = "a3_simple"
    disciplines: dict = field(default_factory=lambda: dict(DEFAULT_DISCIPLINES))
    created: str = field(default_factory=lambda: date.today().isoformat())

    # ------------------------------------------------------------------ paths
    @property
    def path(self) -> Path:
        return Path(self.folder)

    @property
    def drawings_dir(self) -> Path:
        return self.path / "10_Drawings"

    def display_name(self, lang: str) -> str:
        return self.name.get(lang) or self.name.get("en") or self.name.get("he") or self.code

    # ------------------------------------------------------------------ persistence
    def save(self) -> None:
        data = asdict(self)
        data.pop("folder")                      # the folder is wherever the file lives
        (self.path / PROJECT_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path | str) -> "Project":
        folder = Path(folder).resolve()
        data = json.loads((folder / PROJECT_FILE).read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(folder=str(folder), **known)


def safe_folder_name(code: str, name_en: str) -> str:
    base = f"{code}_{name_en}".strip("_ ")
    base = re.sub(r"[<>:\"/\\|?*]", "", base)
    return re.sub(r"\s+", "_", base)[:80]


def create_project(parent: Path | str, code: str, name_he: str = "", name_en: str = "", **kwargs) -> Project:
    if not code.strip():
        raise ValueError("project code is required")
    folder = Path(parent).resolve() / safe_folder_name(code.strip(), name_en or name_he)
    if (folder / PROJECT_FILE).exists():
        raise FileExistsError(f"a project already exists in {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    for sub in SUBFOLDERS:
        (folder / sub).mkdir(exist_ok=True)
    project = Project(code=code.strip(), name={"he": name_he, "en": name_en}, folder=str(folder), **kwargs)
    project.save()
    return project


def is_project_folder(folder: Path | str) -> bool:
    return (Path(folder) / PROJECT_FILE).exists()


def relocate_project(project: Project, new_parent_or_folder: Path | str, move_files: bool = True) -> Project:
    """Change a project's folder.

    move_files=True : move the whole folder into new_parent_or_folder (a parent directory).
    move_files=False: the folder was already moved by the user; just point to it.
    """
    target = Path(new_parent_or_folder).resolve()
    if move_files:
        dest = target / project.path.name
        if dest.exists():
            raise FileExistsError(f"{dest} already exists")
        target.mkdir(parents=True, exist_ok=True)
        shutil.move(str(project.path), str(dest))
    else:
        dest = target
        if not is_project_folder(dest):
            raise FileNotFoundError(f"no {PROJECT_FILE} in {dest}")
    return Project.load(dest)
