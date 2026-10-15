<!-- token-kit:start (generated from rotem-ya/claude-guides/token-kit; edit there, then re-run install.py) -->
## For every agent and tool (Claude Code, Codex, Cursor, Copilot, Gemini, any account)
This repository works by **token-kit**: the full rules are the "token-kit" block in `CLAUDE.md`, and the method is the
skill in `.claude/skills/token-kit/SKILL.md`. Read both before working. In short:
1. Run `python3 tools/codemap.py` first if `docs/codemap/` is missing (Claude Code's SessionStart hook does it; other tools
   do not). Then find code with `docs/codemap/INDEX.md`, `python3 tools/codemap.py find <regex>` or `outline <file>`, and
   read only the line range you need.
2. Never read a whole large file, log or dump; targeted tests while working, the full suite before each push.
3. One session per task; update the handoff and push before stopping.
4. Quality first: every test and rule in `CLAUDE.md` holds.
<!-- token-kit:end -->
