"""Child processes with no console window on Windows.

Every product subprocess goes through `run` or `popen`: CREATE_NO_WINDOW, and (unless `gui=True`)
a hidden startup window. `gui=True` is only for the folder picker, whose dialog must stay visible.
Console flashes are also avoided by `-WindowStyle Hidden` on PowerShell and by pythonw for the
launcher and the updater.
"""
from __future__ import annotations

import os
import subprocess

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
