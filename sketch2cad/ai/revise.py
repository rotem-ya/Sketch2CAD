"""Revise an existing drawing spec on request: free text plus hand markings on the drawing sheet."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .sketch import ELEMENT_TYPES, MODEL, _catalog_lines, _image_block, _spec_format, normalize_spec, parse_reply

SYSTEM = """You revise an existing drawing spec (JSON) for the Sketch2CAD renderer according to the \
engineer's request. The renderer, not you, draws the CAD file.

You get the current spec, the request, and usually an image of the current drawing sheet on which the \
engineer marked by hand (free-pen strokes and straight lines in red, blue or green) where and what to change. \
Use the balloons, legend numbers, labels and dimensions on the sheet to find which elements a marking points \
at; the spec's scale and units tell you how sheet distances map to spec coordinates.

Rules:
- Change only what the request and the markings ask for. Keep every other element, id, dimension, legend \
entry, note and setting exactly as it is.
- Markings are instructions, not geometry: never add elements that just copy the strokes.
- Follow the spec format below. Use only these element types: {types}.
- Prefer catalog items (field "item") for new components; take their sizes from the catalog.
- Every new or changed legend entry needs English and Hebrew text. Write Hebrew in normal logical order; \
never put parentheses around mixed Hebrew/Latin text (use " - "). Use %%c for the diameter sign.
- If the request is unclear or conflicts with the markings, make the safest reading, and list it in \
"assumptions"; anything you cannot resolve goes in "questions".

Answer with one JSON object and nothing else:
{{"spec": {{...the complete revised spec...}}, "changes": [{{"en": "...", "he": "..."}}], \
"assumptions": [{{"en": "...", "he": "..."}}], "questions": [{{"en": "...", "he": "..."}}]}}

=== Spec format ===
{formats}

=== Catalog (id | category | name | key dimensions in mm) ===
{catalog}
"""


@dataclass
class Revision:
    spec: dict
    changes: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    model: str = MODEL


def revise_spec(spec: dict, request: str, api_key: str | None = None, *, image: Path | str | None = None,
                marked: bool = False, catalog=None, language: str = "both", client=None) -> Revision:
    """Ask Claude for the complete revised spec. `image` is the drawing sheet, `marked` = it carries markings."""
    import anthropic

    if not request.strip():
        raise ValueError("Write what to change.")
    content = []
    if image is not None:
        content.append(_image_block(Path(image)))
        content.append({"type": "text", "text": "The image above is the current drawing sheet"
                        + (" with the engineer's markings." if marked else " (no markings).")})
    content.append({"type": "text", "text": "Current spec:\n" + json.dumps(spec, ensure_ascii=False)})
    content.append({"type": "text", "text": f"Request from the engineer:\n{request.strip()}\n\n"
                                            "Return the complete revised spec JSON."})
    client = client or anthropic.Anthropic(api_key=api_key or None)
    system = SYSTEM.format(types=", ".join(ELEMENT_TYPES), formats=_spec_format(), catalog=_catalog_lines(catalog))
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=system,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("The request was declined by the model.")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("The answer was cut off (max_tokens); ask for a smaller change.")
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        reply = parse_reply(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read the model answer as JSON: {exc}") from exc
    new_spec, warnings = normalize_spec(reply.get("spec", reply), units=spec.get("units", "mm"),
                                        language=spec.get("language", language))
    return Revision(spec=new_spec, changes=reply.get("changes", []), assumptions=reply.get("assumptions", []),
                    questions=reply.get("questions", []), warnings=warnings, model=message.model)
