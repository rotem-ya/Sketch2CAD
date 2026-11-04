"""Server-side folder browsing for the in-app folder picker.

The Streamlit server lists its own drives and folders, so choosing a folder works from any browser
(the office PC, a phone over WARP) and never depends on a native Windows dialog in the server process.
Every function here returns a value instead of raising: a missing drive, a denied folder or a dropped
network share must not break the page.
"""
from __future__ import annotations

import os
import string

MAX_ENTRIES = 300                       # one button per folder: keep the list usable on a phone


def drives() -> list[str]:
    """The roots to start from: existing drive letters on Windows (``C:\\``), ``/`` elsewhere."""
    if os.name != "nt":
        return ["/"]
    found = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        try:
            if os.path.isdir(root):
                found.append(root)
        except OSError:
            continue
    return found


def normalize(path: str) -> str:
    """An absolute path, or "" for the drives list. ``D:`` becomes ``D:\\`` (not the drive's cwd)."""
    text = (path or "").strip().strip('"')
    if not text:
        return ""
    if os.name == "nt" and len(text) == 2 and text[1] == ":":
        text += "\\"
    return os.path.abspath(text)


def start_dir(initial: str) -> str:
    """Where the browser opens: the folder itself, else its nearest existing parent, else "" (drives)."""
    path = normalize(initial)
    while path:
        try:
            if os.path.isdir(path):
                return path
        except OSError:
            pass
        up = parent(path)
        if up == path:
            break
        path = up
    return ""


def parent(path: str) -> str:
    """The folder above ``path``; "" (the drives list) from a drive root or ``/``."""
    path = normalize(path)
    up = os.path.dirname(path) if path else ""
    return "" if up == path else up


def subfolders(path: str, limit: int = MAX_ENTRIES) -> tuple[list[str], bool, str]:
    """Folder names inside ``path`` sorted case-insensitively: (names, truncated, error message).

    Hidden folders (leading dot or the Windows hidden/system attribute) and folders that cannot be
    read are skipped. An unreadable ``path`` gives ([], False, <reason>).
    """
    names: list[str] = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=True) or entry.name.startswith("."):
                        continue
                    attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                    if attrs & 0x6:                       # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
                        continue
                except OSError:
                    continue
                names.append(entry.name)
    except OSError as e:
        return [], False, e.strerror or str(e)
    names.sort(key=str.casefold)
    return names[:limit], len(names) > limit, ""
