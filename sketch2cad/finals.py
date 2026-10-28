"""A final version is the DWG, DXF or PDF finished in AutoCAD, outside the app.

The file is copied under `<drawing>/finals/` and recorded on the document. The app's own
outputs are left as they are.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from .documents import Document

_ALLOWED = {".dwg", ".dxf", ".pdf"}


def lock_path(source: Path) -> Path | None:
    """AutoCAD's lock next to an open DWG (``.dwl`` or ``.dwl2``), if it is there."""
    if source.suffix.lower() != ".dwg":
        return None
    for suffix in (".dwl", ".dwl2"):
        lock = source.with_suffix(suffix)
        if lock.is_file():
            return lock
    return None


def newest_dwg(folder: Path | str) -> Path | None:
    """The newest ``.dwg`` in a folder. Lock files and AutoCAD backups are skipped."""
    root = Path(folder)
    if not root.is_dir():
        return None
    found = [path for path in root.iterdir()
             if path.is_file() and path.suffix.lower() == ".dwg" and not path.name.startswith("~$")]
    if not found:
        return None
    return max(found, key=lambda path: (path.stat().st_mtime, path.name))


def register_final(doc: Document, *, name: str, data: bytes | None = None, source: Path | str | None = None,
                   original_path: str = "", rev: str | None = None, note: str = "",
                   sent_to_client: bool = False, project=None) -> tuple[dict, list[str]]:
    """Copy one finished file into ``finals/`` and append a ``finals[]`` record.

    A second save of the same name is a new file. ``outputs`` is not changed.
    """
    source_path = Path(source) if source else None
    if data is None:
        if source_path is None or not source_path.is_file():
            raise FileNotFoundError(original_path or (str(source_path) if source_path else name))
        data = source_path.read_bytes()
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED:
        raise ValueError(f"a final version is a DWG, DXF or PDF, not {suffix or name!r}")
    warnings: list[str] = []
    origin = original_path or (str(source_path) if source_path else "")
    lock = lock_path(source_path) if source_path else (lock_path(Path(origin)) if origin else None)
    if lock is not None:
        warnings.append(lock.name)
    stored_at = datetime.now().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe = Path(name).name.replace("/", "_").replace("\\", "_")
    folder = doc.path / "finals"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stamp}_{safe}"
    extra = 1
    while dest.exists():
        dest = folder / f"{stamp}_{extra}_{safe}"
        extra += 1
    dest.write_bytes(data)
    if source_path is not None and source_path.is_file():
        mtime = datetime.fromtimestamp(source_path.stat().st_mtime).isoformat(timespec="seconds")
    else:
        mtime = stored_at
    entry = {
        "file": dest.relative_to(doc.path).as_posix(),
        "name": safe,
        "md5": hashlib.md5(data).hexdigest(),
        "original_path": origin,
        "mtime": mtime,
        "rev": rev or doc.rev,
        "note": note,
        "sent_to_client": bool(sent_to_client),
        "stored": stored_at,
    }
    doc.finals.append(entry)
    doc.save()
    from .learning import learn_from_final
    warnings.extend(learn_from_final(doc, entry, project))
    return entry, warnings
