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


def _content_blocks(paths) -> list[dict]:
    blocks = []
    for p in map(Path, paths):
        if p.suffix.lower() == ".pdf":
            blocks.append({"type": "document", "source": {
                "type": "base64", "media_type": "application/pdf",
                "data": base64.standard_b64encode(p.read_bytes()).decode("ascii")}, "title": p.name})
        else:
            blocks.append(_image_block(p))
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
        messages=[{"role": "user", "content": _content_blocks(paths) + [{"type": "text", "text": request}]}],
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
                 warnings=warnings, model=message.model)
