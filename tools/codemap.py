#!/usr/bin/env python3
"""Code map (token-kit): runs the skill's codemap.py in .claude/skills/token-kit/. Usage: see CLAUDE.md, token-kit rules.

    python3 tools/codemap.py | find <regex> | outline <file>
"""
import runpy
import sys
from pathlib import Path

sys.argv[0] = str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "token-kit" / "codemap.py")
runpy.run_path(sys.argv[0], run_name="__main__")
