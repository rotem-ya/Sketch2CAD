"""Sketch / photo / PDF → draft spec.json via the Claude API (M5). The user reviews the draft before rendering."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MODEL = "claude-opus-5-5"
FORMATS_DOC = Path(__file__).resolve().parents[2] / "docs" / "FORMATS.md"
ELEMENT_TYPES = ("pipe", "bend", "flange", "gate_valve", "connector", "manhole", "trench_drain", "rect",
                 "polyline", "text", "flow_arrow", "north_arrow", "weld", "break")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 3_500_000
MAX_IMAGE_SIDE = 2000
# The API takes at most 32 MB per request (base64 included) and 600 PDF pages. Files are sent within a raw
# budget that leaves room for base64 (+1/3) and the prompt; larger PDFs are shrunk or sent as page images.
FILES_BUDGET = 22 * 1024 * 1024
MAX_PDF_PAGES = 100                     # pages per PDF (cost); the rest is reported, never dropped silently
MAX_PAGE_IMAGES = 20                    # pages sent as images when a PDF cannot be shrunk enough

SYSTEM = """You turn engineering hand sketches, site photos and marked-up plans into a drawing spec \
(JSON) for the Sketch2CAD renderer. The renderer, not you, draws the CAD file.

Rules:
- Follow the spec format below exactly. Use only these element types: {types}.
- Geometry in the requested units, real-world size (not paper size). Choose a scale and paper so the drawing fits.
- Prefer catalog items (field "item") whenever a component matches; take dimensions from the catalog, not from \
the sketch. If a needed component is not in the catalog, leave "item" out and set explicit sizes.
- Mark existing elements "existing", new work "new", and changes being proposed "proposed".
- Every legend entry needs English and Hebrew text. Write Hebrew in normal logical order; never put \
parentheses around mixed Hebrew/Latin text (use " - "). Use %%c for the diameter sign.
- Dimensions you read from the sketch go in "dimensions". Anything you estimated (not written on the sketch \
and not in the catalog) goes in "assumptions"; anything you cannot resolve goes in "questions".

Answer with one JSON object and nothing else:
{{"spec": {{...}}, "assumptions": [{{"en": "...", "he": "..."}}], "questions": [{{"en": "...", "he": "..."}}]}}

=== Spec format ===
{formats}

=== Catalog (id | category | name | key dimensions in mm) ===
{catalog}
"""


@dataclass
class Draft:
    spec: dict
    assumptions: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    model: str = MODEL


def _spec_format() -> str:
    """The spec.json section of docs/FORMATS.md."""
    text = FORMATS_DOC.read_text(encoding="utf-8")
    start = text.find("## 1.")
    end = text.find("## 2.")
    return text[start:end].strip() if start >= 0 else text


def _catalog_lines(catalog) -> str:
    if catalog is None:
        return "(empty)"
    lines = []
    for it in catalog.items():
        dims = {**(it.get("dims") or {}), **{k: v for k, v in (it.get("flange") or {}).items() if k != "std"}}
        dim_txt = ", ".join(f"{k}={v}" for k, v in dims.items())
        lines.append(f"{it['id']} | {it.get('category', '')} | {catalog.label(it)} | {dim_txt}")
    return "\n".join(lines) or "(empty)"


def _image_block(path: Path) -> dict:
    """Image content block; large or unsupported images are re-encoded as PNG within the size limits."""
    data = path.read_bytes()
    media = IMAGE_TYPES.get(path.suffix.lower())
    if media is None or len(data) > MAX_IMAGE_BYTES:
        pix = pymupdf.Pixmap(str(path))
        side = max(pix.width, pix.height)
        if side > MAX_IMAGE_SIDE:
            pix = pymupdf.Pixmap(pix, int(pix.width * MAX_IMAGE_SIDE / side), int(pix.height * MAX_IMAGE_SIDE / side))
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        data, media = pix.tobytes("png"), "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.standard_b64encode(data).decode("ascii")}}


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("ascii")


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"


def _page_jpeg(page) -> bytes:
    """One PDF page as a JPEG, at most MAX_IMAGE_SIDE px on its long side."""
    zoom = min(MAX_IMAGE_SIDE / max(page.rect.width, page.rect.height), 200 / 72)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("jpeg", jpg_quality=80)


def _pdf_blocks(path: Path, budget: int, max_pages: int, notes: list) -> tuple[list[dict], int]:
    """Content blocks for one PDF within `budget` raw bytes; returns (blocks, bytes used).

    1. the file as is, when it is small enough;  2. a copy with the first `max_pages` pages and
    recompressed images;  3. the first pages rendered as JPEG images. Steps 2-3 are reported in `notes`.
    """
    data = path.read_bytes()
    doc = pymupdf.open(path)
    total = doc.page_count
    pages = min(total, max_pages)
    if len(data) <= budget and total <= max_pages:
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": _b64(data)}, "title": path.name}], len(data)
    small = pymupdf.open()
    small.insert_pdf(doc, to_page=pages - 1)
    small.rewrite_images(dpi_threshold=160, dpi_target=150, quality=70)
    small_data = small.tobytes(garbage=4, deflate=True, clean=True)
    if len(small_data) <= budget:
        part = f"pages 1-{pages} of {total}" if pages < total else f"all {total} pages"
        notes.append(f"{path.name}: {_mb(len(data))} - sent as a compressed copy ({_mb(len(small_data))}, {part}).")
        blocks = [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                  "data": _b64(small_data)}, "title": path.name}]
        if pages < total:
            blocks.append({"type": "text", "text": f"({path.name}: only pages 1-{pages} of {total} are attached.)"})
        return blocks, len(small_data)
    images, used = [], 0
    for i in range(min(pages, MAX_PAGE_IMAGES)):
        jpg = _page_jpeg(doc[i])
        if used + len(jpg) > budget:
            break
        images.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(jpg)}})
        used += len(jpg)
    if not images:
        raise ValueError(f"{path.name} is too large to send ({_mb(len(data))}). Send fewer files at once, "
                         "or split the PDF and send only the relevant pages.")
    notes.append(f"{path.name}: {_mb(len(data))}, too large to send as a PDF - pages 1-{len(images)} of {total} "
                 "were sent as images. To send other pages, split the PDF and send only those pages.")
    head = {"type": "text", "text": f"{path.name}: pages 1-{len(images)} of {total}, as images:"}
    return [head, *images], used


def _content_blocks(paths, notes: list | None = None, *, max_pdf_pages: int = MAX_PDF_PAGES) -> list[dict]:
    """Image/document blocks for the files, kept within the API request limit.

    Anything that had to be reduced (pages left out, compressed copy, pages as images) is added to `notes`
    so the user is told; a request that cannot fit raises ValueError instead of failing at the API.
    """
    notes = notes if notes is not None else []
    blocks, budget = [], FILES_BUDGET
    for p in map(Path, paths):
        if p.suffix.lower() == ".pdf":
            new, used = _pdf_blocks(p, budget, max_pdf_pages, notes)
        else:
            new = [_image_block(p)]
            used = len(new[0]["source"]["data"]) * 3 // 4
            if used > budget:
                raise ValueError(f"Too many files for one request (limit about {_mb(FILES_BUDGET)}). "
                                 f"Send {p.name} separately.")
        blocks += new
        budget -= used
    return blocks


def parse_reply(text: str) -> dict:
    """Extract the JSON object from the model reply (tolerates a ```json fence)."""
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fence.group(1) if fence else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


def normalize_spec(spec: dict, *, units: str = "mm", language: str = "both") -> tuple[dict, list[str]]:
    """Fill defaults and drop elements the renderer does not know; returns (spec, warnings)."""
    warnings = []
    spec = dict(spec or {})
    spec.setdefault("version", 1)
    spec.setdefault("units", units)
    spec.setdefault("scale", 20)
    spec.setdefault("paper", "A3")
    spec.setdefault("language", language)
    spec.setdefault("view", "")
    spec.setdefault("sheet", {"template": "a3_legend", "fields": {}})
    for key in ("elements", "dimensions", "legend"):
        if not isinstance(spec.get(key), list):
            spec[key] = []
    spec.setdefault("notes", {"en": [], "he": []})
    spec.setdefault("params", {})
    kept, seen = [], set()
    for i, el in enumerate(spec["elements"]):
        if not isinstance(el, dict) or el.get("type") not in ELEMENT_TYPES:
            warnings.append(f"Dropped element {i}: unknown type {el.get('type') if isinstance(el, dict) else el!r}")
            continue
        el.setdefault("id", f"{el['type']}_{i}")
        if el["id"] in seen:
            el["id"] = f"{el['id']}_{i}"
        seen.add(el["id"])
        kept.append(el)
    spec["elements"] = kept
    return spec, warnings


def draft_spec_from_images(paths, notes: str = "", api_key: str | None = None, *, catalog=None,
                           units: str = "mm", language: str = "both", client=None) -> Draft:
    """Ask Claude for a spec draft from sketches/photos/PDFs plus free-text notes."""
    import anthropic

    file_notes: list[str] = []
    content = _content_blocks(paths, file_notes)
    client = client or anthropic.Anthropic(api_key=api_key or None)
    system = SYSTEM.format(types=", ".join(ELEMENT_TYPES), formats=_spec_format(), catalog=_catalog_lines(catalog))
    request = (f"Units: {units}. Drawing language: {language}.\n"
               f"Notes from the engineer:\n{notes.strip() or '(none)'}\n\n"
               "Produce the spec JSON for these inputs.")
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content + [{"type": "text", "text": request}]}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("The request was declined by the model.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The answer was cut off (max_tokens); simplify the input or split the sketch.")
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        reply = parse_reply(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read the model answer as JSON: {exc}") from exc
    spec, warnings = normalize_spec(reply.get("spec", reply), units=units, language=language)
    return Draft(spec=spec, assumptions=reply.get("assumptions", []), questions=reply.get("questions", []),
                 warnings=file_notes + warnings, model=message.model)
