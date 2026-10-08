"""Project details (code, names, client...) read from a document - contract, title page, drawing, letter."""
from __future__ import annotations

import json
import re
import tempfile
import zipfile
from pathlib import Path

from .sketch import IMAGE_TYPES, MODEL, _content_blocks

FIELDS = {
    "code": "Project number / code as used on the project's documents (e.g. 20117). Digits and Latin letters only.",
    "name_he": "Project name in Hebrew (translate if the document is English-only).",
    "name_en": "Project name in English (translate if the document is Hebrew-only).",
    "client": "Client / employer / owner, short (e.g. 'USACE / CDM Smith').",
    "contractor": "Contractor, if stated.",
    "location": "Site / location, if stated.",
    "contract_no": "Contract or tender number, if stated.",
}
SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in FIELDS},
    "required": list(FIELDS),
    "additionalProperties": False,
}
SYSTEM = ("You read construction-project documents (contracts, title blocks, transmittals, letters) and return the "
          "project's identifying details. Use an empty string for anything the document does not state - never "
          "invent a value.\nFields:\n" + "\n".join(f"- {k}: {v}" for k, v in FIELDS.items()))
DIRECT = {".pdf", *IMAGE_TYPES}
MAX_TEXT = 60_000


def _docx_text(path: Path) -> str:
    """Plain text of a .docx without extra dependencies."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _text_of(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        return _docx_text(path)
    if path.suffix.lower() in (".txt", ".csv", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    from ..io.readers import read_any                     # DXF / DWG / IFC title blocks
    return read_any(path, Path(tempfile.mkdtemp(prefix="s2c_info_"))).text


def extract_project_info(paths, api_key: str | None = None, *, client=None) -> dict:
    """Read one or more documents and return {field: value} for FIELDS (empty string = not found)."""
    import anthropic

    paths = [Path(p) for p in paths]
    blocks = _content_blocks([p for p in paths if p.suffix.lower() in DIRECT])
    for p in paths:
        if p.suffix.lower() not in DIRECT:
            text = _text_of(p).strip()
            if text:
                blocks.append({"type": "text", "text": f"--- {p.name} ---\n{text[:MAX_TEXT]}"})
    if not blocks:
        raise ValueError("No readable content in the selected files.")
    client = client or anthropic.Anthropic(api_key=api_key or None)
    message = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": blocks + [{"type": "text", "text": "Return the project details."}]}],
    )
    if message.stop_reason == "refusal":
        raise RuntimeError("The request was declined by the model.")
    text = next(b.text for b in message.content if b.type == "text")
    data = json.loads(text)
    data["code"] = re.sub(r"[^A-Za-z0-9_.-]", "", data.get("code", ""))
    return {k: str(data.get(k, "")).strip() for k in FIELDS}
