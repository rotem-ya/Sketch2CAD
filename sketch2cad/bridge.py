"""Work-package bridge: export a drawing for an external agent, import its spec reply.

The program does the mechanical work. An agent outside this program reads the package and returns
a spec. This module does not call a model API and does not use the network.

    python -m sketch2cad.bridge export FOLDER --out DIR [--drawing CODE]
        [--request TEXT | --request-file PATH]
    python -m sketch2cad.bridge import REPLY.json --drawing FOLDER [--trial DIR] [--apply]
        [--package DIR]

`FOLDER` is a project folder (`project.json`) or a drawing folder (`document.json`).
Import renders only into the trial folder. `--apply` replaces `spec.json` after a successful render,
with a copy in `spec_history/` (the same names the drawing screen already restores from).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .ai.sketch import _catalog_lines, _spec_format, normalize_spec, parse_reply
from .catalog import Catalog, load_catalog
from .checks import items_used, run_checks
from .documents import DOC_FILE, Document, list_documents
from .projects import PROJECT_FILE, Project

SPEC_HISTORY = "spec_history"          # same folder app.backup_spec writes, so undo keeps working
SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
SECRET_NAMES = {"settings.json", "data_home.txt", ".env", "credentials.json", "secrets.json"}
SECRET_MARKERS = (b"anthropic_api_key", b"ANTHROPIC_API_KEY", b"sk-ant-")
RETURN_ENVELOPE = {
    "spec": {},
    "changes": [{"en": "", "he": ""}],
    "assumptions": [{"en": "", "he": ""}],
    "questions": [{"en": "", "he": ""}],
}
# Standing revision rules, same obligations as sketch2cad/ai/revise.py SYSTEM.
# There is no Papis precision document in the repository; see PAPIS_NOTE.
PRECISION_RULES = (
    "Change only what the request and the markings ask for. Keep every other element, id, "
    "dimension, legend entry, note and setting exactly as it is.",
    "Preserve units, scale and paper unless the request explicitly changes them.",
    "Markings are instructions, not geometry: never add elements that just copy the strokes.",
    "Prefer catalog items (field \"item\") for new components; take their sizes from the catalog. "
    "If the catalog does not have the component, set explicit sizes and say so in assumptions.",
    "Every new or changed legend entry needs English and Hebrew text. Write Hebrew in normal logical "
    "order; never put parentheses around mixed Hebrew/Latin text (use \" - \"). Use %%c for the diameter sign.",
    "If the request is unclear or conflicts with the markings, make the safest reading and list it in "
    "assumptions; anything you cannot resolve goes in questions.",
    "List every change you actually made in changes. Do not describe unchanged parts as changes.",
)
PAPIS_NOTE_EN = (
    "No Papis precision document was found in this repository. The name Papis appears only as a "
    "reviewer sign-off in tests/test_mail.py and as an example of reviewer mail in docs/SPEC.md "
    "(section 12, item 6). The rules above are the program's standing revision rules: change only "
    "what was asked, preserve units, scale and every unrequested element, and report assumptions "
    "and questions."
)
PAPIS_NOTE_HE = (
    "לא נמצא בריפו מסמך דרישות דיוק בשם \"פפיס\". השם Papis מופיע רק כחתימה של בודק בדוגמת המייל "
    "שב-tests/test_mail.py, וכדוגמה למייל בודק ב-docs/SPEC.md (סעיף 12, פריט 6). במקום זה הוטמעו "
    "כללי התיקון הקיימים: לשנות רק את מה שהבקשה והסימונים מבקשים, לשמור יחידות, קנה מידה ונייר, "
    "וכל אלמנט, מזהה, מידה, שורת מקרא, הערה והגדרה שלא התבקש לשנות, ולדווח הנחות ושאלות."
)


class BridgeError(Exception):
    """A package could not be built or the drawing folder is not usable."""


class ReplyError(BridgeError):
    """The agent reply is not a usable spec envelope."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_secret(data: bytes) -> bool:
    folded = data.lower()
    return any(marker.lower() in folded for marker in SECRET_MARKERS)


def _project_above(doc: Document) -> Project:
    for parent in doc.path.parents:
        if (parent / PROJECT_FILE).is_file():
            return Project.load(parent)
    raise BridgeError(f"no {PROJECT_FILE} above {doc.path}")


def open_drawing(folder: Path | str, drawing: str | None = None) -> tuple[Project, Document]:
    """A project folder, or one drawing folder inside a project."""
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise BridgeError(f"not a folder: {folder}")
    if (folder / DOC_FILE).is_file():
        doc = Document.load(folder)
        if drawing and drawing != doc.code:
            raise BridgeError(f"--drawing {drawing} does not match document {doc.code}")
        return _project_above(doc), doc
    if (folder / PROJECT_FILE).is_file():
        project = Project.load(folder)
        docs = list_documents(project)
        if drawing:
            found = [d for d in docs if d.code == drawing]
            if len(found) != 1:
                raise BridgeError(f"no drawing {drawing} in project {project.code}")
            return project, found[0]
        if len(docs) == 1:
            return project, docs[0]
        codes = ", ".join(d.code for d in docs) or "(none)"
        raise BridgeError(f"project {project.code} has {len(docs)} drawings ({codes}); pass --drawing")
    raise BridgeError(f"folder has neither {PROJECT_FILE} nor {DOC_FILE}")


def read_spec(doc: Document) -> dict | None:
    path = doc.path / "spec.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"spec.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeError("spec.json is not a JSON object")
    return data


def relevant_catalog(catalog: Catalog | None, spec: dict | None) -> Catalog | None:
    """Items the spec cites, plus the rest of their categories. The whole catalog when nothing is cited."""
    if catalog is None or not spec:
        return catalog
    used = {item_id for _, item_id in items_used(spec)}
    if not used:
        return catalog
    categories = set()
    for item_id in used:
        item = catalog.get(item_id)
        if item and item.get("category"):
            categories.add(item["category"])
    chosen = [it for it in catalog.items() if it["id"] in used or it.get("category") in categories]
    if not chosen:
        return catalog
    subset = Catalog()
    for item in chosen:
        subset.add(item)
    return subset


def _preview_file(doc: Document) -> tuple[Path | None, bool]:
    """Newest marked sheet if the drawing screen saved one, otherwise the rendered preview."""
    markup_dir = doc.path / "markups"
    marked = sorted(markup_dir.glob("markup_*.png")) if markup_dir.is_dir() else []
    if marked and marked[-1].is_file():
        return marked[-1], True
    rel = (doc.outputs or {}).get("preview")
    if rel and (doc.path / rel).is_file():
        return doc.path / rel, False
    plain = doc.path / "preview.png"
    if plain.is_file():
        return plain, False
    return None, False


def _units_language(current: dict | None, manifest: dict, project: Project, doc: Document) -> tuple[str, str]:
    current = current or {}
    units = current.get("units") or manifest.get("units") or "mm"
    language = (current.get("language") or manifest.get("language") or doc.language_mode
                or project.language_mode or "both")
    return units, language


def _agent_text() -> str:
    rules = "\n".join(f"- {rule}" for rule in PRECISION_RULES)
    envelope = json.dumps(RETURN_ENVELOPE, ensure_ascii=False, indent=2)
    return (
        "# Sketch2CAD work package\n\n"
        "You revise or draft a drawing spec. The Sketch2CAD renderer, not you, draws the CAD file.\n"
        "Do not invent geometry from pen strokes. Read the files in this folder, then return one JSON "
        "object and nothing else.\n\n"
        "## Precision\n"
        f"{rules}\n\n"
        "## Where these rules come from\n"
        f"{PAPIS_NOTE_EN}\n\n"
        f"{PAPIS_NOTE_HE}\n\n"
        "## Files\n"
        "- `spec_format.md` — spec format (docs/FORMATS.md, section 1)\n"
        "- `catalog.txt` — catalog lines: id | category | name | key dimensions in mm\n"
        "- `spec.json` — current spec, when the drawing already has one. Return the complete spec, "
        "not a diff.\n"
        "- `sources/` — sketches, photos and PDFs\n"
        "- `preview.png` — current sheet; hand markings are already drawn on it when the engineer marked it\n"
        "- `request.txt` — what to change, when a request was included\n"
        "- `manifest.json` — hashes, units, language, file names, and the return envelope\n\n"
        "## Return envelope\n"
        "```json\n"
        f"{envelope}\n"
        "```\n"
    )


def _contained(root: Path, rel: str) -> Path | None:
    """A path that stays inside `root`. Absolute paths and `..` are rejected."""
    root = root.resolve()
    path = (root / rel).resolve()
    if path == root or root not in path.parents:
        return None
    return path


def _source_skip_reason(path: Path) -> str | None:
    if path.name.lower() in SECRET_NAMES or path.name.startswith("."):
        return "sensitive file name"
    if path.suffix.lower() not in SOURCE_SUFFIXES:
        return "not a sketch, image or PDF"
    if _has_secret(path.read_bytes()):
        return "secret marker"
    return None


def _file_entry(path: Path, root: Path, role: str) -> dict:
    return {"name": path.relative_to(root).as_posix(), "role": role, "sha256": _sha256(path),
            "bytes": path.stat().st_size}


def _assert_no_secrets(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and _has_secret(path.read_bytes()):
            raise BridgeError(f"refusing to write the package: {path.name} contains a secret marker")


def export_package(folder: Path | str, out: Path | str, *, drawing: str | None = None,
                   request: str = "") -> Path:
    """Build a work package for `folder`. Raises BridgeError and leaves no package folder on failure."""
    project, doc = open_drawing(folder, drawing)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise BridgeError(f"output folder is not empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    try:
        _fill_package(project, doc, out, request.strip())
        _assert_no_secrets(out)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    return out


def _fill_package(project: Project, doc: Document, out: Path, request: str) -> None:
    spec = read_spec(doc)
    catalog = load_catalog(project)
    units, language = _units_language(spec, {}, project, doc)
    excluded = []
    for rel in doc.sources:
        src = _contained(doc.path, rel)
        if src is None:
            excluded.append({"name": rel, "reason": "outside the drawing folder"})
            continue
        if not src.is_file():
            excluded.append({"name": rel, "reason": "missing"})
            continue
        reason = _source_skip_reason(src)
        if reason:
            excluded.append({"name": rel, "reason": reason})
            continue
        dest = _contained(out, rel)
        if dest is None:
            excluded.append({"name": rel, "reason": "outside the drawing folder"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    if spec is not None:
        spec_path = out / "spec.json"
        shutil.copy2(doc.path / "spec.json", spec_path)
        if _has_secret(spec_path.read_bytes()):
            raise BridgeError("refusing to export: spec.json contains a secret marker")
    (out / "spec_format.md").write_text(_spec_format() + "\n", encoding="utf-8")
    (out / "catalog.txt").write_text(_catalog_lines(relevant_catalog(catalog, spec)) + "\n", encoding="utf-8")
    (out / "AGENT.md").write_text(_agent_text(), encoding="utf-8")
    if request:
        (out / "request.txt").write_text(request + "\n", encoding="utf-8")
        if _has_secret((out / "request.txt").read_bytes()):
            raise BridgeError("refusing to export: the request contains a secret marker")
    preview, marked = _preview_file(doc)
    if preview is not None:
        if _has_secret(preview.read_bytes()):
            excluded.append({"name": preview.name, "reason": "secret marker"})
        else:
            shutil.copy2(preview, out / "preview.png")
    files = []
    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        role = _role_for(path.relative_to(out).as_posix(), marked)
        files.append(_file_entry(path, out, role))
    manifest = {
        "kind": "sketch2cad-work-package",
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "project_code": project.code,
        "document_code": doc.code,
        "revision": doc.rev,
        "units": units,
        "language": language,
        "items_used": sorted({item_id for _, item_id in items_used(spec or {})}),
        "preview_marked": bool(marked and (out / "preview.png").is_file()),
        "files": files,
        "excluded": excluded,
        "return_envelope": RETURN_ENVELOPE,
        "precision_rules": list(PRECISION_RULES),
        "precision_source": {"en": PAPIS_NOTE_EN, "he": PAPIS_NOTE_HE},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")


def _role_for(name: str, marked: bool) -> str:
    if name == "spec.json":
        return "spec"
    if name == "spec_format.md":
        return "spec_format"
    if name == "catalog.txt":
        return "catalog"
    if name == "AGENT.md":
        return "instructions"
    if name == "request.txt":
        return "request"
    if name == "preview.png":
        return "marked_preview" if marked else "preview"
    if name.startswith("sources/"):
        return "source"
    return "other"


def _bilingual_list(reply: dict, key: str, warnings: list[str]) -> list:
    if key not in reply:
        return []
    value = reply[key]
    if not isinstance(value, list):
        warnings.append(f"{key} is not a list and was ignored")
        return []
    return value


def _spec_from_reply(reply: dict, units: str, language: str, current: dict | None) -> tuple[dict, list[str]]:
    spec_in = reply["spec"] if "spec" in reply else reply
    if not isinstance(spec_in, dict):
        raise ReplyError("spec must be a JSON object")
    for key in ("elements", "dimensions", "legend"):
        if key in spec_in and not isinstance(spec_in[key], list):
            raise ReplyError(f"{key} must be a list")
    original = spec_in.get("elements") or []
    if any(not isinstance(el, dict) for el in original):
        raise ReplyError("each element must be an object")
    spec, warnings = normalize_spec(spec_in, units=units, language=language)
    if original and not spec["elements"]:
        detail = "; ".join(warnings) or "no known element type"
        raise ReplyError(f"no element survived validation: {detail}")
    if not original and current and current.get("elements"):
        warnings.append("reply has no elements; the current spec has elements")
    return spec, warnings


def _empty_report(trial: Path) -> dict:
    return {
        "ok": False,
        "applied": False,
        "errors": [],
        "warnings": [],
        "render_warnings": [],
        "checks": [],
        "outputs": {},
        "assumptions": [],
        "questions": [],
        "changes": [],
        "backup": None,
        "element_count": 0,
        "trial_dir": str(trial),
        "precision_rules": list(PRECISION_RULES),
        "precision_source": {"en": PAPIS_NOTE_EN, "he": PAPIS_NOTE_HE},
    }


def _write_report(trial: Path, report: dict) -> Path:
    trial.mkdir(parents=True, exist_ok=True)
    path = trial / "report.json"
    report["report"] = str(path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def backup_spec(doc: Document) -> str | None:
    """Copy spec.json to spec_history/spec_<timestamp>.json. Returns the relative path, or None."""
    src = doc.path / "spec.json"
    if not src.is_file():
        return None
    folder = doc.path / SPEC_HISTORY
    folder.mkdir(exist_ok=True)
    dest = folder / f"spec_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
    shutil.copy2(src, dest)
    return f"{SPEC_HISTORY}/{dest.name}"


def write_spec(doc: Document, spec: dict) -> None:
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    (doc.path / "spec.json").write_text(text, encoding="utf-8")


def import_reply(reply_path: Path | str, drawing: Path | str, *, package: Path | str | None = None,
                 trial: Path | str | None = None, apply: bool = False) -> dict:
    """Validate a reply, check it, render into a trial folder, and write report.json.

    The drawing's spec.json is replaced only when `apply` is true and the render succeeded.
    """
    project, doc = open_drawing(drawing)
    trial = Path(trial) if trial else doc.path / "bridge_trials" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report = _empty_report(trial)
    reply_path = Path(reply_path)
    if not reply_path.is_file():
        raise BridgeError(f"reply file not found: {reply_path}")
    raw = reply_path.read_bytes()
    if _has_secret(raw):
        report["errors"].append("reply contains a secret marker and was refused")
        _write_report(trial, report)
        return report
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        report["errors"].append(f"reply is not UTF-8 text: {exc}")
        _write_report(trial, report)
        return report
    manifest = _read_package_manifest(package) if package else {}
    current = read_spec(doc)
    units, language = _units_language(current, manifest, project, doc)
    try:
        reply = parse_agent_reply(text)
        spec, warnings = _spec_from_reply(reply, units, language, current)
    except ReplyError as exc:
        report["errors"].append(str(exc))
        _write_report(trial, report)
        return report
    report["warnings"] = warnings
    report["assumptions"] = _bilingual_list(reply, "assumptions", warnings)
    report["questions"] = _bilingual_list(reply, "questions", warnings)
    report["changes"] = _bilingual_list(reply, "changes", warnings)
    report["warnings"] = warnings
    catalog = load_catalog(project)
    report["checks"] = [finding.to_dict() for finding in run_checks(spec, catalog)]
    try:
        from .drawing import render
        rendered = render(spec, trial, "trial", project=project, document=doc, catalog=catalog,
                          formats=("dxf", "pdf", "png"))
    except Exception as exc:  # noqa: BLE001 - a spec that does not render must not replace spec.json
        report["errors"].append(f"render failed: {exc}")
        _write_report(trial, report)
        return report
    report["ok"] = True
    report["element_count"] = len(spec.get("elements", []))
    report["render_warnings"] = list(rendered.get("warnings") or [])
    report["outputs"] = {key: Path(value).name for key, value in rendered.items()
                         if key != "warnings" and isinstance(value, Path)}
    if apply:
        report["backup"] = backup_spec(doc)
        write_spec(doc, spec)
        report["applied"] = True
    _write_report(trial, report)
    return report


def parse_agent_reply(text: str) -> dict:
    """The envelope object. `parse_reply` accepts a bare JSON object or a ```json fence."""
    try:
        reply = parse_reply(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReplyError(f"reply is not a JSON object: {exc}") from exc
    if not isinstance(reply, dict):
        raise ReplyError("reply JSON must be an object")
    return reply


def _read_package_manifest(package: Path | str) -> dict:
    path = Path(package) / "manifest.json"
    if not path.is_file():
        raise BridgeError(f"no manifest.json in {package}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"manifest.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BridgeError("manifest.json is not an object")
    return data


def _print_import(report: dict) -> int:
    state = "ok" if report["ok"] else "failed"
    applied = " applied" if report["applied"] else ""
    print(f"{state}{applied}: {report.get('report', report['trial_dir'])}")
    for error in report["errors"]:
        print(f"error: {error}", file=sys.stderr)
    checks = report["checks"]
    if checks:
        print(f"checks: {len(checks)}")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sketch2cad.bridge",
                                     description="Export a work package or import an agent spec reply.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    export_cmd = sub.add_parser("export", help="write a work package for one drawing")
    export_cmd.add_argument("folder", help="project folder or drawing folder")
    export_cmd.add_argument("--out", required=True, help="empty folder to create the package in")
    export_cmd.add_argument("--drawing", help="document code, when folder is a project with several drawings")
    export_cmd.add_argument("--request", default="", help="revision request written to request.txt")
    export_cmd.add_argument("--request-file", help="read the revision request from this text file")
    import_cmd = sub.add_parser("import", help="validate, check and render a reply into a trial folder")
    import_cmd.add_argument("reply", help="agent reply, a JSON envelope")
    import_cmd.add_argument("--drawing", required=True, help="drawing folder (document.json)")
    import_cmd.add_argument("--package", help="work package folder, for units and language when there is no spec")
    import_cmd.add_argument("--trial", help="trial output folder (default: <drawing>/bridge_trials/<time>)")
    import_cmd.add_argument("--apply", action="store_true",
                            help="after a successful render, back up spec.json and replace it")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "export":
            if args.request and args.request_file:
                raise BridgeError("pass only one of --request and --request-file")
            request = args.request
            if args.request_file:
                request = Path(args.request_file).read_text(encoding="utf-8")
            package = export_package(args.folder, args.out, drawing=args.drawing, request=request)
            print(package)
            return 0
        report = import_reply(args.reply, args.drawing, package=args.package, trial=args.trial, apply=args.apply)
    except BridgeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return _print_import(report)


if __name__ == "__main__":
    sys.exit(main())
