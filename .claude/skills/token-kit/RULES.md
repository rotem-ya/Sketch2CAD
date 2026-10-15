<!-- token-kit:start (generated from rotem-ya/claude-guides/token-kit/RULES.md; edit there, then re-run install.py) -->
## Save tokens and time, never quality (token-kit)
Measured on a large project, 15.10.2026: one 22-hour session grew to 733K tokens of context, and ~70% of its cost was
re-reading that context on every turn. Every turn re-reads the whole context, so a tool result costs its size times the
turns left. This is the working method of every repository from now on (Rotem, 15.10.2026), for every session, account
and tool. The method in full, and how to install it in a new repository: the skill `.claude/skills/token-kit/SKILL.md`.
Other tools read the same rules through `AGENTS.md`. The rules:
1. **Short sessions.** One session = one task or one round. When it is done, or the context passes ~200K tokens: update the
   handoff, push, and continue in a fresh session (or `/compact`). Never carry a day in one context.
2. **Find code through the code map, not by reading files.** `docs/codemap/INDEX.md` → the area's map (every function,
   class and type with its line range and the tests that import its file) → read only that range (`Read` offset/limit,
   `sed -n`). `python3 tools/codemap.py find <regex>` / `outline <file>` answer "where is X" in one line. The map is
   generated (gitignored) by the SessionStart hook; if it is missing run `python3 tools/codemap.py` (about a second).
3. **Read by section**, never a whole large file, JSON, log or state dump into the context; summarise big outputs with a
   script and print only the result (`| tail`, `| grep`, counts).
4. **Targeted checks while working** (the touched area's tests, typecheck/lint of the touched package); the full suite and
   builds once before each push. Test output to a file; only the summary and the failures into the context.
5. **Delegate mechanical work** (running a full suite and summarising it, wide searches, reading logs) to a subagent that
   returns only the result, on a smaller model where the task is mechanical. Design, building and review stay on the
   main model. The fewest agents that cover the work; narrow briefs (exact files, lines and the test that proves it).
6. **Waits**: a wait over ~50 minutes lets the prompt cache expire and the whole context is written again. One background
   command with a completion notice, never a polling loop.
7. **A short handoff**: its first section holds only the open next steps (at most ~60 lines); a finished step moves to a
   log in one line. Stale handoff files go to `docs/archive/`.
8. **Quality is not traded for tokens**: every test, gate and review rule of this repository holds. When saving tokens and
   quality conflict, quality wins and the cost is said to Rotem.
<!-- token-kit:end -->
