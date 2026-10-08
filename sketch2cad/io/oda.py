"""DWG <-> DXF through the ODA File Converter (free, installed separately), or - when ODA is not
installed - through AutoCAD itself (accoreconsole.exe, the AutoCAD Core Console; not in AutoCAD LT).

Both converters run on a private temp copy of the source; the result is copied to the target folder.
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
AUTOCAD_GLOBS = (r"C:\Program Files\Autodesk\AutoCAD*\accoreconsole.exe",)
AUTOCAD_FORMATS = {"dwg": "2018", "dxf": "DXF"}       # SAVEAS file-format answers


class ConverterNotFound(RuntimeError):
    """Neither the ODA File Converter nor AutoCAD (accoreconsole) was found."""


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


def find_autocad() -> str | None:
    """AutoCAD Core Console ($AUTOCAD_CORECONSOLE, else the newest AutoCAD install), or None."""
    env = os.environ.get("AUTOCAD_CORECONSOLE", "")
    if env and Path(env).is_file():
        return env
    if os.name == "nt":
        found = [p for pattern in AUTOCAD_GLOBS for p in glob.glob(pattern)]
        if found:
            return max(found, key=_version_key)
    return None


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
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{src.stem}.{fmt}"
    exe = find_converter()
    if exe:
        return _convert_oda(exe, src, fmt, dest, version)
    acad = find_autocad()
    if acad:
        return _convert_autocad(acad, src, fmt, dest)
    raise ConverterNotFound(
        "No DWG converter found. Install ODA File Converter (free, opendesign.com) and set its path in "
        "Settings, or install full AutoCAD (its accoreconsole.exe is used automatically).")


def _run(cmd: list[str], name: str) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Conversion of {name} timed out after {TIMEOUT_S} s") from exc


def _output(proc: subprocess.CompletedProcess) -> str:
    """Converter output as text (accoreconsole writes UTF-16)."""
    raw = proc.stderr or proc.stdout or b""
    text = raw.decode("utf-16-le", "ignore") if raw[1:2] == b"\x00" else raw.decode(errors="ignore")
    return text.strip()[-2000:]


def _convert_oda(exe: str, src: Path, fmt: str, dest: Path, version: str) -> Path:
    with tempfile.TemporaryDirectory(prefix="s2c_oda_") as tmp:
        in_dir, conv_dir = Path(tmp, "in"), Path(tmp, "out")
        in_dir.mkdir()
        conv_dir.mkdir()
        shutil.copy2(src, in_dir / src.name)
        proc = _run(_command(exe) + [str(in_dir), str(conv_dir), version, fmt.upper(), "0", "1", src.name],
                    src.name)
        results = [p for p in conv_dir.iterdir() if p.suffix.lower() == f".{fmt}"]
        if not results:
            raise RuntimeError(f"ODA conversion of {src.name} to {fmt.upper()} failed "
                               f"(exit code {proc.returncode}): {_output(proc)}")
        shutil.copyfile(results[0], dest)
    return dest


def autocad_script(fmt: str, out: Path) -> str:
    """Core Console script: no dialogs, SAVEAS to `fmt` (DXF also answers the accuracy prompt).
    The Core Console exits by itself at the end of the script."""
    answers = [AUTOCAD_FORMATS[fmt]] + ([""] if fmt == "dxf" else []) + [f'"{out}"']
    return "\n".join(["FILEDIA 0", "_.SAVEAS", *answers, ""])


def _convert_autocad(exe: str, src: Path, fmt: str, dest: Path) -> Path:
    # ASCII names in a temp folder: the Core Console script reader is not reliable with Hebrew paths.
    with tempfile.TemporaryDirectory(prefix="s2c_acad_") as tmp:
        work_in = Path(tmp, f"in{src.suffix.lower()}")
        work_out = Path(tmp, f"out.{fmt}")
        shutil.copy2(src, work_in)
        script = Path(tmp, "convert.scr")
        script.write_text(autocad_script(fmt, work_out), encoding="utf-8")
        proc = _run([exe, "/i", str(work_in), "/s", str(script), "/l", "en-US"], src.name)
        if not work_out.is_file():
            raise RuntimeError(f"AutoCAD conversion of {src.name} to {fmt.upper()} failed "
                               f"(exit code {proc.returncode}): {_output(proc)}")
        shutil.copyfile(work_out, dest)
    return dest
