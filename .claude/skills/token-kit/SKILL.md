---
name: token-kit
description: Rotem's standard way of working in every code repository - find code through a generated code map instead of reading whole files, short sessions, targeted checks, and installing that kit in any repository that lacks it. Use at the start of every coding session in a repository, before reading or searching code, when creating or setting up a new project or repository, when asked where something is in the code, when a session grows long or expensive, and whenever tokens, cost, speed or "חיסכון בטוקנים", "מפת קוד", "פרויקט חדש", "איפה נמצא", "סשן ארוך" come up.
---

# token-kit: the working method for every repository

Goal, in this order of priority: keep the quality of the software, cut tokens, cut working time. Measured on a large
project (15.10.2026): a 22-hour session grew to 733K tokens of context and ~70% of its cost was re-reading that context
on every turn. A tool result costs its size times the turns left in the session, so small reads and short sessions are
where the savings are.

## 1. Is the kit installed in this repository?

```bash
test -f tools/codemap.py && grep -q 'token-kit:start' CLAUDE.md && echo installed
```

- **Not installed** (a new project, or an existing one that never got it): install it, from the folder this SKILL.md is in
  (`.claude/skills/token-kit/`, `~/.claude/skills/token-kit/`, or the account's synced skills folder):

  ```bash
  python3 <this skill's folder>/install.py <repository root>
  ```

  It adds `.claude/skills/token-kit/` (this skill, so every session of every account that opens the repository has it),
  `tools/codemap.py`, the SessionStart hook in `.claude/`, `docs/codemap/` in `.gitignore`, and the rules block in
  `CLAUDE.md` and `AGENTS.md` (created when missing). Then: write a few lines above the block in `CLAUDE.md` if it is new
  (what the project is, where to start reading, the test/lint/build commands), run the project's tests (no product code
  changed, so they must be as green as before), commit "Add token-kit" and push per the repository's own rules.
- **Installed, older**: compare `VERSION` in `.claude/skills/token-kit/codemap.py` with this skill's; if older, run
  `install.py` again (idempotent: the blocks are replaced in place).
- **Installed, current**: go to 2.

## 2. Work this way

1. **Find, then read a range.** `docs/codemap/INDEX.md` → the area's map → `Read` with offset/limit (or `sed -n`) on the
   symbol's line range. `python3 tools/codemap.py find <regex>` and `python3 tools/codemap.py outline <file>` answer
   "where is X" in one line. If `docs/codemap/` is missing (no hook ran), run `python3 tools/codemap.py` (about a second).
2. **Never pull a whole large file, JSON, log or state dump into the context.** `grep`, `head`, `tail`, counts; send long
   command output to a file and print only the summary and failures.
3. **Targeted checks while working** (the tests the map lists for the touched file, typecheck/lint of the touched
   package); the full suite and builds once before each push.
4. **Delegate mechanical work** (a full suite and its summary, wide searches, log reading) to a subagent that returns
   only the result, on a smaller model when the task is mechanical. Design, building and review stay on the main model.
   The fewest agents that cover the work; narrow briefs with exact files, lines and the test that proves it.
5. **Waits** under ~50 minutes (the prompt cache lifetime): one background command with a completion notice, never a
   polling loop.
6. **One session = one task or one round.** When it is done, or the context passes ~200K tokens: update the handoff
   (its first section holds only the open next steps), push, and tell Rotem in one line to continue in a fresh session.
7. **Quality first.** Every test, gate and review rule of the repository still holds. When saving tokens and quality
   conflict, quality wins and the cost is said to Rotem.

## 3. Other tools and accounts

- Every Claude Code session of any account that opens the repository gets this skill (`.claude/skills/token-kit/`), the
  hook (`.claude/settings.json`) and the rules (`CLAUDE.md`). On a local machine the hook also copies the skill to
  `~/.claude/skills/token-kit/`, so that account has it in new projects too.
- Codex, Cursor, Copilot, Gemini and other agents read `AGENTS.md`: the same rules, plus "run `python3 tools/codemap.py`
  first" (they have no hook).
- claude.ai accounts: upload `token-kit.zip` (in `rotem-ya/claude-guides/token-kit/dist/`) under Customize → Skills, once
  per account. Then every session of that account has the skill, also in a brand-new repository.

The kit's source and its tests: https://github.com/rotem-ya/claude-guides/tree/main/token-kit (change it there, run its
tests with `python3 -m pytest -q token-kit`, rebuild the zip with `python3 token-kit/package.py`, then re-run
`install.py` in each repository).
