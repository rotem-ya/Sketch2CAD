"""Internal document codes, built from a per-project pattern.

Tokens: {PRJ} project code, {DISC} discipline, {SUBJ} subject, {TYPE} document type,
        {YYYY} year, {SEQ} / {SEQ:03} running number (zero-padded).
Example: "{PRJ}-{DISC}-{SUBJ}-{SEQ:02}" -> "20117-W-CAMEL-01"
"""
from __future__ import annotations

import re
from datetime import date

TOKEN_RE = re.compile(r"\{(PRJ|DISC|SUBJ|TYPE|YYYY|SEQ)(?::(\d+))?\}")
DEFAULT_PATTERN = "{PRJ}-{DISC}-{SUBJ}-{SEQ:02}"
REV_SEQUENCE = [chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in "IO"]


def _clean(value: str) -> str:
    """Code-safe text: upper case, spaces -> '-', no path characters."""
    value = re.sub(r"\s+", "-", str(value).strip().upper())
    return re.sub(r"[^\w\-.]", "", value, flags=re.UNICODE)


def render(pattern: str, *, prj: str, disc: str = "", subj: str = "", typ: str = "", seq: int | None = None,
           year: int | None = None) -> str:
    values = {"PRJ": prj, "DISC": disc, "SUBJ": subj, "TYPE": typ, "YYYY": str(year or date.today().year)}

    def sub(m: re.Match) -> str:
        tok, width = m.group(1), m.group(2)
        if tok == "SEQ":
            if seq is None:
                return "{SEQ}"
            return str(seq).zfill(int(width or 1))
        return _clean(values[tok])

    code = TOKEN_RE.sub(sub, pattern)
    code = re.sub(r"-{2,}", "-", code).strip("-")      # empty tokens leave no double dashes
    return code


def seq_regex(pattern: str, **fields) -> re.Pattern:
    """Regex that matches codes of this pattern/fields and captures the running number."""
    probe = render(pattern, seq=None, **fields)
    parts = probe.split("{SEQ}")
    if len(parts) != 2:
        raise ValueError("pattern must contain exactly one {SEQ} token")
    return re.compile("^" + re.escape(parts[0]) + r"(\d+)" + re.escape(parts[1]) + "$")


def next_code(pattern: str, existing: list[str], **fields) -> tuple[str, int]:
    """Next free code for the given fields, given the codes already used in the project."""
    rx = seq_regex(pattern, **fields)
    used = [int(m.group(1)) for c in existing if (m := rx.match(c))]
    seq = max(used, default=0) + 1
    return render(pattern, seq=seq, **fields), seq


def next_rev(rev: str | None) -> str:
    if not rev:
        return REV_SEQUENCE[0]
    if rev in REV_SEQUENCE and rev != REV_SEQUENCE[-1]:
        return REV_SEQUENCE[REV_SEQUENCE.index(rev) + 1]
    if rev.isdigit():
        return str(int(rev) + 1)
    raise ValueError(f"cannot advance revision '{rev}'")
