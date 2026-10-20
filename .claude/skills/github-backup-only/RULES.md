<!-- github-backup-only:start (generated from rotem-ya/claude-guides/github-backup-only/RULES.md; edit there, then re-run install.py) -->
## GitHub is a backup, not a build machine (github-backup-only)
Rotem, 20.10.2026: "בגיט צריך שיהיו רק גיבויים", "רוצה להימנע מחיובים". Every session, account and tool, in every
repository. The method in full: the skill `.claude/skills/github-backup-only/SKILL.md`. The rules:
1. **Nothing runs on GitHub.** Workflows are `workflow_dispatch` only; never start, re-run or dispatch one unless Rotem
   asks in his own words. Nothing on GitHub that costs money (Actions minutes, Codespaces, Packages, LFS, larger runners).
2. **The session is the CI:** the full tests, lint, typecheck and build run in the session before every push; failing
   code is not pushed; the results go in the handoff and the PR. A red or missing GitHub check is expected, never waited on.
3. **A version is a branch `release/vX.Y.Z`** at the tested commit (cloud sessions cannot push tags). The machine that
   runs the software pulls it and builds its own installer (procurement: the update screen on the office server).
4. **GitHub stays the backup:** push small steps often; Issues, PRs and reviews are free and stay.
<!-- github-backup-only:end -->
