"""Application settings and the registry of known projects.

Stored as JSON in the data folder ("app home"), which also holds the search index, the personal
catalog and the personal title-block templates. Projects live in their own folders. The data folder is:
  1. $SKETCH2CAD_HOME when set (tests, scripts),
  2. the folder named in data_home.txt next to the app (chosen in Settings or in install.bat, e.g. a
     data drive while the program itself is installed on C:),
  3. otherwise  Windows: %APPDATA%/Sketch2CAD   other: ~/.sketch2cad
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
DATA_HOME_FILE = "data_home.txt"
DATA_ITEMS = ("settings.json", "index.db", "catalog", "templates")   # what moves with the data folder


def data_home_file() -> Path:
    return APP_DIR / DATA_HOME_FILE


def configured_data_home() -> Path | None:
    """The data folder chosen by the user (data_home.txt), or None for the default location."""
    f = data_home_file()
    if f.exists():
        text = f.read_text(encoding="utf-8-sig").strip()
        if text:
            return Path(text)
    return None


def default_data_home() -> Path:
    if os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Sketch2CAD"
    return Path.home() / ".sketch2cad"


def app_home() -> Path:
    if os.environ.get("SKETCH2CAD_HOME"):
        home = Path(os.environ["SKETCH2CAD_HOME"])
    else:
        home = configured_data_home() or default_data_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def set_data_home(folder: str | os.PathLike, *, copy_from: Path | None = None) -> Path:
    """Use `folder` as the data folder from now on (writes data_home.txt next to the app).

    Existing data (settings, search index, personal catalog and templates) is copied from `copy_from`
    (default: the current data folder) into the new folder, without overwriting anything already
    there, so pointing at a data folder from another computer keeps that folder's data.
    """
    new = Path(folder).expanduser()
    if not str(folder).strip():
        raise ValueError("empty data folder")
    source = copy_from if copy_from is not None else app_home()
    new.mkdir(parents=True, exist_ok=True)
    if source.resolve() != new.resolve():
        for name in DATA_ITEMS:
            src, dst = source / name, new / name
            if not src.exists() or dst.exists():
                continue
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
    data_home_file().write_text(str(new), encoding="utf-8")
    return new


def _drop_api_keys(data: dict) -> dict:
    """Drop leftover model-key fields. The program does not store or read them."""
    return {key: value for key, value in data.items() if "api_key" not in str(key).lower()}


def _coerce_runner(known: dict) -> dict:
    """Keep a hand-edited settings file from crashing on the runner fields."""
    if "claude_accounts" in known:
        raw = known["claude_accounts"]
        if isinstance(raw, str):
            raw = raw.splitlines()
        if isinstance(raw, list):
            known["claude_accounts"] = [str(item).strip() for item in raw if str(item).strip()]
        else:
            known["claude_accounts"] = []
    if "claude_auto_run" in known:
        known["claude_auto_run"] = bool(known["claude_auto_run"])
    if "claude_exe" in known and not isinstance(known["claude_exe"], str):
        known["claude_exe"] = "" if known["claude_exe"] is None else str(known["claude_exe"])
    if "claude_timeout_s" in known:
        try:
            known["claude_timeout_s"] = max(30, int(known["claude_timeout_s"]))
        except (TypeError, ValueError):
            known["claude_timeout_s"] = 600
    return known


@dataclass
class Settings:
    ui_language: str = "he"                    # "he" | "en"
    default_projects_root: str = ""            # where new project folders are created by default
    oda_converter_path: str = ""               # ODAFileConverter.exe (DWG <-> DXF)
    projects: list[str] = field(default_factory=list)   # registered project folders
    claude_exe: str = ""                       # empty: look on PATH and in %USERPROFILE%\.local\bin
    claude_accounts: list[str] = field(default_factory=list)  # CLAUDE_CONFIG_DIR dirs, in try order
    claude_auto_run: bool = False              # off until an exe path and an account folder are set
    claude_timeout_s: int = 600                # seconds for one headless run

    @classmethod
    def load(cls) -> "Settings":
        path = app_home() / "settings.json"
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return cls()
        cleaned = _drop_api_keys(data)
        if set(cleaned) != set(data):
            try:
                path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass
        known = {key: value for key, value in cleaned.items() if key in cls.__dataclass_fields__}
        return cls(**_coerce_runner(known))

    def save(self) -> None:
        path = app_home() / "settings.json"
        payload = _drop_api_keys(asdict(self))
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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
