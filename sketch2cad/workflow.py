"""Drawing-screen view of the agent exchange folders.

The program does not call a model. A package in the project outbox, a reply in the inbox,
and the events file on the drawing are enough to say what the engineer should do next.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

WORKFLOW_FILE = "agent_workflow.json"


def compose_request(text: str, options: list[str]) -> str:
    """Free text, then each checked option on its own line."""
    body = text.strip()
    lines = [f"- {item.strip()}" for item in options if item and item.strip()]
    if body and lines:
        return body + "\n\n" + "\n".join(lines)
    if lines:
        return "\n".join(lines)
    return body


def record_applied(doc_dir: Path | str, *, reply: Path | str, backup: str | None) -> None:
    """Remember that this reply file was applied, so the inbox no longer looks new."""
    reply = Path(reply)
    _append(doc_dir, {
        "kind": "apply",
        "at": _now(),
        "reply": reply.name,
        "reply_mtime_ns": reply.stat().st_mtime_ns,
        "backup": backup,
    })


def record_restore(doc_dir: Path | str, backup_name: str) -> None:
    """A rollback puts the previous spec back, so the last apply is no longer in force."""
    _append(doc_dir, {"kind": "restore", "at": _now(), "backup": backup_name})


def workflow_view(project_dir: Path | str, doc_dir: Path | str, document_code: str) -> dict:
    """Status for one drawing: nothing sent, waiting, reply ready, or already applied."""
    packages = _packages(project_dir, document_code)
    last = packages[-1] if packages else None
    reply = _newest_inbox(project_dir)
    events = _load(doc_dir).get("events") or []
    applied = _effective_apply(events)
    waiting = _reply_waiting(reply, events)
    if waiting:
        state = "reply"
    elif last and (applied is None or str(last["at"]) > str(applied["at"])):
        state = "awaiting"
    elif applied is not None:
        state = "applied"
    elif last is not None:
        state = "awaiting"
    else:
        state = "none"
    return {
        "state": state,
        "last_sent": last,
        "reply_waiting": waiting,
        "reply_name": reply.name if reply is not None else None,
        "last_applied": applied,
    }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path(doc_dir: Path | str) -> Path:
    return Path(doc_dir) / WORKFLOW_FILE


def _load(doc_dir: Path | str) -> dict:
    path = _path(doc_dir)
    if not path.is_file():
        return {"events": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"events": []}
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return {"events": []}
    return data


def _append(doc_dir: Path | str, event: dict) -> None:
    data = _load(doc_dir)
    data["events"].append(event)
    path = _path(doc_dir)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _effective_apply(events: list) -> dict | None:
    current = None
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "apply":
            current = event
        elif event.get("kind") == "restore":
            current = None
    return current


def _reply_waiting(reply: Path | None, events: list) -> bool:
    if reply is None:
        return False
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "restore":
            return True
        if event.get("kind") == "apply":
            same = event.get("reply") == reply.name and event.get("reply_mtime_ns") == reply.stat().st_mtime_ns
            return not same
    return True


def _newest_inbox(project_dir: Path | str) -> Path | None:
    from sketch2cad.bridge import BridgeError, latest_inbox_reply
    try:
        return latest_inbox_reply(project_dir)
    except BridgeError:
        return None


def _packages(project_dir: Path | str, document_code: str) -> list[dict]:
    outbox = Path(project_dir) / "agent_exchange" / "outbox"
    found = []
    if not outbox.is_dir():
        return found
    for folder in outbox.iterdir():
        manifest_path = folder / "manifest.json"
        if not folder.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("document_code") != document_code:
            continue
        request = ""
        req = folder / "request.txt"
        if req.is_file():
            request = req.read_text(encoding="utf-8").strip()
        found.append({
            "at": str(manifest.get("created") or ""),
            "request": request,
            "folder": folder.name,
        })
    found.sort(key=lambda item: (item["at"], item["folder"]))
    return found
