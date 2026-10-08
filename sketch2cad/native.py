"""Native OS dialogs for the local app (the Streamlit server runs on the user's own PC)."""
from __future__ import annotations

import os
import subprocess

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
        proc = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", PS_PICK_FOLDER],
                              capture_output=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        path = proc.stdout.decode("utf-8", "ignore").strip()
        return path or None
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
    except Exception:  # noqa: BLE001 - no tkinter or no display (server / CI)
        return None
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(master=root, initialdir=initial or None, title=title or None)
    root.destroy()
    return path or None
