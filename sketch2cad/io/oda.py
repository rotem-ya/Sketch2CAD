"""DWG <-> DXF through the ODA File Converter (free, installed separately).

The converter works on folders, so the source is copied into a private temp folder, converted
there and the result is copied to the target folder.
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import Settings

TIMEOUT_S = 180
WINDOWS_GLOBS = (r"C:\Program Files\ODA\ODAFileConverter*\ODAFileConverter.exe",
                 r"C:\Program Files (x86)\ODA\ODAFileConverter*\ODAFileConverter.exe")


class ConverterNotFound(RuntimeError):
    """The ODA File Converter is not installed or not configured."""


def _version_key(path: str) -> tuple[int, ...]:
    """'C:/Program Files/ODA/ODAFileConverter 25.4.0/...' -> (25, 4, 0)."""
    folder = Path(path).parent.name
    return tuple(int(n) for n in re.findall(r"\d+", folder))


def find_converter(settings: Settings | None = None) -> str | None:
    """Path of the ODA File Converter, or None.

    Search order: settings.oda_converter_path, $ODA_CONVERTER, the default Windows install
    folders (newest version wins), then the PATH.
    """
    settings = settings or Settings.load()
    for candidate in (settings.oda_converter_path, os.environ.get("ODA_CONVERTER", "")):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    if os.name == "nt":
        found = [p for pattern in WINDOWS_GLOBS for p in glob.glob(pattern)]
        if found:
            return max(found, key=_version_key)
    return shutil.which("ODAFileConverter")


def _command(exe: str) -> list[str]:
    """The converter is a Qt GUI app: without a display (Linux servers) run it under xvfb-run."""
    if os.name != "nt" and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        return ["xvfb-run", "-a", exe]
    return [exe]


def convert(src: Path | str, fmt: str, out_dir: Path | str | None = None, *,
            version: str = "ACAD2018") -> Path:
    """Convert a DWG/DXF file to `fmt` ("dwg" | "dxf"); the result goes to out_dir (default: next
    to src) as <stem>.<fmt>. Raises ConverterNotFound or RuntimeError (with the converter output)."""
    src = Path(src)
    fmt = fmt.lower()
    if fmt not in ("dwg", "dxf"):
        raise ValueError(f"unsupported target format: {fmt}")
    if not src.is_file():
        raise FileNotFoundError(src)
    exe = find_converter()
    if not exe:
        raise ConverterNotFound(
            "ODA File Converter not found. Install it (free) from opendesign.com and set its path "
            "in Settings (or the ODA_CONVERTER environment variable).")
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="s2c_oda_") as tmp:
        in_dir, conv_dir = Path(tmp, "in"), Path(tmp, "out")
        in_dir.mkdir()
        conv_dir.mkdir()
        shutil.copy2(src, in_dir / src.name)
        cmd = _command(exe) + [str(in_dir), str(conv_dir), version, fmt.upper(), "0", "1", src.name]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                                  creationflags=flags)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"ODA conversion of {src.name} timed out after {TIMEOUT_S} s") from exc
        results = [p for p in conv_dir.iterdir() if p.suffix.lower() == f".{fmt}"]
        if not results:
            output = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"ODA conversion of {src.name} to {fmt.upper()} failed "
                               f"(exit code {proc.returncode}): {output}")
        dest = out_dir / f"{src.stem}.{fmt}"
        shutil.copyfile(results[0], dest)
    return dest
