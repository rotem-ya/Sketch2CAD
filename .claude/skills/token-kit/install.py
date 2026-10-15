#!/usr/bin/env python3
"""Install or update token-kit in a repository (new or existing). Idempotent: run it again to update.

    python3 install.py <repo-dir>

It puts the skill itself in .claude/skills/token-kit/ (so every Claude Code session of any account that opens the
repository has it), a tools/codemap.py entry point, the SessionStart hook (.claude/hooks/session-start.sh and its entry in
.claude/settings.json), docs/codemap/ in .gitignore, and the rules block in CLAUDE.md and AGENTS.md (between the
token-kit markers, replacing an older block; the files are created when missing). It builds the code map once. It never
overwrites a hook the repository wrote itself: it prints what to add instead.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
SKILL_FILES = ["SKILL.md", "codemap.py", "codemap_ts.cjs", "install.py", "session-start.sh", "RULES.md", "AGENTS_BLOCK.md"]
START, END = "<!-- token-kit:start", "<!-- token-kit:end -->"
HOOK_CMD = '"$CLAUDE_PROJECT_DIR"/.claude/hooks/session-start.sh'
SHIM = '''#!/usr/bin/env python3
"""Code map (token-kit): runs the skill's codemap.py in .claude/skills/token-kit/. Usage: see CLAUDE.md, token-kit rules.

    python3 tools/codemap.py | find <regex> | outline <file>
"""
import runpy
import sys
from pathlib import Path

sys.argv[0] = str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "token-kit" / "codemap.py")
runpy.run_path(sys.argv[0], run_name="__main__")
'''


def put_block(path: Path, block: str, title: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
    rx = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    text = rx.sub(lambda _: block, text) if rx.search(text) else text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def main(target: Path) -> None:
    if not (target / ".git").exists():
        sys.exit(f"not a git repository: {target}")
    notes = []

    skill = target / ".claude" / "skills" / "token-kit"
    skill.mkdir(parents=True, exist_ok=True)
    if KIT.resolve() != skill.resolve():
        for f in SKILL_FILES:
            shutil.copy2(KIT / f, skill / f)
    for f in ("codemap.py", "install.py", "session-start.sh"):
        (skill / f).chmod(0o755)

    tools = target / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "codemap.py").write_text(SHIM, encoding="utf-8")
    (tools / "codemap.py").chmod(0o755)
    (tools / "codemap_ts.cjs").unlink(missing_ok=True)  # kit 1.0 kept a copy here; it lives in the skill now

    hooks = target / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "session-start.sh"
    if hook.exists() and "token-kit" not in hook.read_text(encoding="utf-8"):
        notes.append(f"{hook.relative_to(target)} is the repository's own: make it run `python3 tools/codemap.py` and copy "
                     ".claude/skills/token-kit to ~/.claude/skills (see the kit's session-start.sh)")
    else:
        shutil.copy2(KIT / "session-start.sh", hook)
        hook.chmod(0o755)

    settings_p = target / ".claude" / "settings.json"
    settings = json.loads(settings_p.read_text(encoding="utf-8")) if settings_p.exists() else {}
    starts = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
    if not any("session-start.sh" in h.get("command", "") for e in starts for h in e.get("hooks", [])):
        starts.append({"matcher": "startup", "hooks": [{"type": "command", "command": HOOK_CMD}]})
    settings_p.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    gi = target / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    if not any(l.strip().rstrip("/") == "docs/codemap" for l in lines):
        gi.write_text("\n".join(lines + ["docs/codemap/"]) + "\n", encoding="utf-8")

    put_block(target / "CLAUDE.md", (KIT / "RULES.md").read_text(encoding="utf-8").strip(),
              f"{target.name}: instructions for every Claude session")
    put_block(target / "AGENTS.md", (KIT / "AGENTS_BLOCK.md").read_text(encoding="utf-8").strip(),
              f"{target.name}: instructions for every agent")

    res = subprocess.run([sys.executable, str(tools / "codemap.py")], cwd=target, capture_output=True, text=True)
    print(res.stdout.strip() or res.stderr.strip())
    for n in notes:
        print("note:", n)
    print(f"token-kit installed in {target}: .claude/skills/token-kit, tools/codemap.py, .claude/hooks, .claude/settings.json, "
          ".gitignore, CLAUDE.md, AGENTS.md")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve())
