"""Native OS dialogs for the local app (the Streamlit server runs on the user's own PC)."""
from __future__ import annotations

import os
import subprocess

from .procutil import run as hidden_run

PICK_TIMEOUT_S = 120

PS_PICK_FOLDER = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
$dlg = New-Object System.Windows.Forms.FolderBrowserDialog
$dlg.ShowNewFolderButton = $true
$dlg.Description = $env:S2C_TITLE
if ($env:S2C_START -and (Test-Path $env:S2C_START)) { $dlg.SelectedPath = $env:S2C_START }
if ($dlg.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { $dlg.SelectedPath }
"""


def pick_folder(initial: str = "", title: str = "") -> str | None:
    """Show the OS folder picker; returns the chosen folder, or None (cancelled / no desktop)."""
    if os.name == "nt":
        env = {**os.environ, "S2C_START": initial or "", "S2C_TITLE": title or ""}
        # gui=True: hide the PowerShell console, not the folder dialog itself. The dialog opens on the
        # server's desktop (unseen from a phone, behind windows, or no desktop at all): a time limit,
        # so a caller is never blocked for good. The app itself uses the in-app browser (folderbrowse).
        try:
            proc = hidden_run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-STA", "-Command", PS_PICK_FOLDER],
                capture_output=True, env=env, gui=True, timeout=PICK_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError):
            return None
        path = (proc.stdout or b"").decode("utf-8", "ignore").strip()
        return path or None
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
    except Exception:  # noqa: BLE001 - no tkinter or no display (server / CI)
        return None
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(master=root, initialdir=initial or None, title=title or None)
    except Exception:  # noqa: BLE001 - a broken display must not break the caller
        path = None
    finally:
        root.destroy()
    return path or None


def disable_console_quick_edit() -> None:
    """Windows: a click inside the program's console window starts "QuickEdit" selection, which pauses
    the program until Enter/Esc is pressed; the browser then shows "Connection error". Turn it off."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)                  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            kernel32.SetConsoleMode(handle, (mode.value & ~ENABLE_QUICK_EDIT_MODE) | ENABLE_EXTENDED_FLAGS)
    except Exception:  # noqa: BLE001 - no console (pythonw) or API unavailable: nothing to do
        pass
