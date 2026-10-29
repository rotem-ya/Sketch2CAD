"""Child processes with no console window on Windows.

Every product subprocess goes through `run` or `popen`: CREATE_NO_WINDOW, and (unless `gui=True`)
a hidden startup window. `gui=True` is only for the folder picker, whose dialog must stay visible.
Console flashes are also avoided by `-WindowStyle Hidden` on PowerShell and by pythonw for the
launcher and the updater.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# The stdlib constant exists only on Windows. The numeric value is the Win32 flag.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)


def _startupinfo():
    """SW_HIDE. Absent on non-Windows Pythons (and when a test forces os.name)."""
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info


def _hide(kwargs: dict) -> dict:
    gui = kwargs.pop("gui", False)
    extra = kwargs.pop("extra_flags", 0)
    if os.name != "nt":
        return kwargs
    kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | CREATE_NO_WINDOW | int(extra)
    if not gui:
        info = _startupinfo()
        if info is not None:
            kwargs.setdefault("startupinfo", info)
    return kwargs


def run(argv, **kwargs):
    """subprocess.run with no console on Windows."""
    return subprocess.run(argv, **_hide(kwargs))


def popen(argv, **kwargs):
    """subprocess.Popen with no console on Windows."""
    return subprocess.Popen(argv, **_hide(kwargs))


def extended_form(text: str) -> str:
    r"""``C:\a`` -> ``\\?\C:\a``; ``\\server\share`` -> ``\\?\UNC\server\share``; already extended: unchanged."""
    if text.startswith("\\\\?\\"):
        return text
    text = text.replace("/", "\\")
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def long_path(path: Path | str) -> str:
    """Windows: the extended-length form, so a path past 260 characters still works.

    Other systems: the absolute path. ``\\\\?\\`` is not added there.
    """
    text = os.fspath(path)
    if os.name != "nt":
        return os.path.abspath(text)
    import ntpath
    if text.startswith("\\\\?\\"):
        return text
    return extended_form(ntpath.abspath(text))


def ensure_dir(path: Path | str) -> None:
    os.makedirs(long_path(path), exist_ok=True)


def make_dir(path: Path | str) -> None:
    """Create a directory. Raises if it already exists. Uses the extended form on Windows."""
    os.makedirs(long_path(path))


def is_file(path: Path | str) -> bool:
    try:
        return Path(long_path(path)).is_file()
    except OSError:
        return False


def copy_file(src: Path | str, dest: Path | str) -> None:
    """Copy one file. On Windows the ``\\\\?\\`` form is used so a long path does not fail."""
    ensure_dir(Path(dest).parent)
    shutil.copy2(long_path(src), long_path(dest))


def read_bytes(path: Path | str) -> bytes:
    return Path(long_path(path)).read_bytes()


def write_bytes(path: Path | str, data: bytes) -> None:
    ensure_dir(Path(path).parent)
    Path(long_path(path)).write_bytes(data)


def read_text(path: Path | str, encoding: str = "utf-8") -> str:
    return Path(long_path(path)).read_text(encoding=encoding)


def write_text(path: Path | str, text: str, encoding: str = "utf-8") -> None:
    ensure_dir(Path(path).parent)
    Path(long_path(path)).write_text(text, encoding=encoding)


def iter_files(root: Path) -> list[Path]:
    """Files under ``root``, including ones whose path is longer than Windows allows plainly."""
    found: list[Path] = []
    base = long_path(root)
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            full = os.path.join(dirpath, name)
            found.append(_plain(full))
    return found


def _plain(text: str) -> Path:
    if text.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + text[len("\\\\?\\UNC\\"):])
    if text.startswith("\\\\?\\"):
        return Path(text[4:])
    return Path(text)
