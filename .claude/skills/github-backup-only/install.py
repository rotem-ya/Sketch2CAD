#!/usr/bin/env python3
"""Install or update github-backup-only in a repository. Idempotent: run it again to update.

    python3 install.py <repo-dir>          copy the skill and put the rules block in CLAUDE.md and AGENTS.md
    python3 install.py --zip <out.zip>     build the zip to upload as a claude.ai skill (Customize → Skills)

It lists workflow files that still run by themselves; it does not change them (SKILL.md says how).
"""

from __future__ import annotations

import re
import shutil
import sys
import zipfile
from pathlib import Path

KIT = Path(__file__).resolve().parent
NAME = "github-backup-only"
FILES = ["SKILL.md", "RULES.md", "AGENTS_BLOCK.md", "install.py"]
START, END = f"<!-- {NAME}:start", f"<!-- {NAME}:end -->"
AUTO = re.compile(r"^\s*(push|pull_request|pull_request_target|schedule|release|workflow_run)\s*:", re.M)


def put_block(path: Path, block: str, title: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
    rx = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    text = rx.sub(lambda _: block.strip(), text) if rx.search(text) else text.rstrip() + "\n\n" + block.strip() + "\n"
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def auto_workflows(target: Path) -> list[str]:
    wf = target / ".github" / "workflows"
    if not wf.is_dir():
        return []
    return sorted(
        str(p.relative_to(target)) for p in wf.iterdir() if p.suffix in (".yml", ".yaml") and AUTO.search(p.read_text("utf-8"))
    )


def install(target: Path) -> list[str]:
    if not (target / ".git").exists():
        sys.exit(f"not a git repository: {target}")
    skill = target / ".claude" / "skills" / NAME
    skill.mkdir(parents=True, exist_ok=True)
    if KIT.resolve() != skill.resolve():
        for f in FILES:
            shutil.copy2(KIT / f, skill / f)
    put_block(target / "CLAUDE.md", (KIT / "RULES.md").read_text(encoding="utf-8"), "CLAUDE.md")
    put_block(target / "AGENTS.md", (KIT / "AGENTS_BLOCK.md").read_text(encoding="utf-8"), "AGENTS.md")
    return auto_workflows(target)


def build_zip(out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in FILES:
            zf.write(KIT / f, f"{NAME}/{f}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--zip":
        build_zip(Path(sys.argv[2]))
        print(f"built {sys.argv[2]}")
    elif len(sys.argv) == 2:
        left = install(Path(sys.argv[1]).resolve())
        print(f"{NAME} installed in {sys.argv[1]}")
        for w in left:
            print(f"still runs by itself (change `on:` to workflow_dispatch only): {w}")
    else:
        sys.exit(__doc__)
