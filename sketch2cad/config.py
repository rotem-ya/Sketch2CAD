"""Application settings and the registry of known projects.

Stored as JSON in the per-user app-data folder:
  Windows: %APPDATA%/Sketch2CAD   other: ~/.sketch2cad   override: $SKETCH2CAD_HOME
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def app_home() -> Path:
    if os.environ.get("SKETCH2CAD_HOME"):
        home = Path(os.environ["SKETCH2CAD_HOME"])
    elif os.environ.get("APPDATA"):
        home = Path(os.environ["APPDATA"]) / "Sketch2CAD"
    else:
        home = Path.home() / ".sketch2cad"
    home.mkdir(parents=True, exist_ok=True)
    return home


@dataclass
class Settings:
    ui_language: str = "he"                    # "he" | "en"
    default_projects_root: str = ""            # where new project folders are created by default
    oda_converter_path: str = ""               # ODAFileConverter.exe (DWG <-> DXF)
    anthropic_api_key: str = ""                # Claude API (AI sketch reading, later stage)
    projects: list[str] = field(default_factory=list)   # registered project folders

    @classmethod
    def load(cls) -> "Settings":
        path = app_home() / "settings.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        path = app_home() / "settings.json"
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    def register_project(self, folder: Path | str) -> None:
        folder = str(Path(folder).resolve())
        if folder not in self.projects:
            self.projects.append(folder)
            self.save()

    def unregister_project(self, folder: Path | str) -> None:
        folder = str(Path(folder).resolve())
        if folder in self.projects:
            self.projects.remove(folder)
            self.save()
