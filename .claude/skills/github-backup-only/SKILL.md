---
name: github-backup-only
description: Rotem's rule for every repository since 20.10.2026 - GitHub is only the backup of the code (plus Issues and PRs), never a build or test machine - no GitHub Actions runs and nothing on GitHub that costs money. Tests run in the session before every push, a version is a release/vX.Y.Z branch, and the machine that runs the software pulls the code and builds its own installer. Use at the start of every session in a repository, before every push, before adding or changing anything in .github/workflows, before making a version or release, when CI is red or does not start, when GitHub billing, Actions minutes or charges come up, when setting up a new repository, and whenever "Release", "CI", "Actions", "גרסה", "עדכון", "חיוב", "חיובים", "גיבוי" come up.
---

# github-backup-only: GitHub is a backup, not a build machine

Rotem, 20.10.2026 (verbatim): "בגיט צריך שיהיו רק גיבויים", "רוצה להימנע מחיובים", and for the procurement system "אני
רוצה ששרת המחשב במשרד ישמש בדיוק כמו גיט האב ושהעדכונים ימשכו ממנו" ← "כן, דרך 1". Then: "תכין סקיל ותוודא שכל סשן
בקלוד שעובד על פרויקט כלשהו בגיט, מכיר את שיטת העבודה החדשה."

Why: on 20.10.2026 the account (GitHub Free, private repositories) used up its free Actions minutes ($12.18 of usage in
October, mostly Windows release builds, which count double). The spending limit is $0, so nothing was charged and every
workflow simply stopped starting (jobs fail in 2-3 seconds with no log). The answer is not to pay: nothing runs on GitHub.

## 1. Is the rule applied in this repository?

```bash
grep -q 'github-backup-only:start' CLAUDE.md && echo block-ok
grep -lE '^\s*(push|pull_request|pull_request_target|schedule|release|workflow_run):' .github/workflows/*.y*ml 2>/dev/null
```

- **No block in `CLAUDE.md`**: run `python3 <this skill's folder>/install.py <repository root>`. It copies this skill to
  `.claude/skills/github-backup-only/` (every Claude Code session of any account that opens the repository gets it) and
  puts the rules block in `CLAUDE.md` and `AGENTS.md` (between markers; idempotent).
- **A workflow file is listed** (it still runs by itself): change its `on:` to `workflow_dispatch:` only, with a comment
  quoting Rotem's words above, and remove `if:` conditions that only served the old triggers. Keep the file, so it can
  still be run by hand. Never delete a workflow without Rotem.
- **Except a workflow that deploys something people use** (GitHub Pages, a store upload, a website, a scheduled data job):
  turning it off stops the deployment. Do not change it; ask Rotem first, in one question, saying what it deploys and
  that in a **public** repository standard GitHub-hosted runners are free (the charge is for private repositories).
- Then commit ("Apply github-backup-only"), run the repository's checks (no product code changed), and push per the
  repository's own rules. Tell Rotem in one line.

## 2. Work this way

1. **Nothing runs on GitHub.** Never add a workflow trigger other than `workflow_dispatch`. Never start, re-run or
   dispatch a workflow unless Rotem asks for it in his own words, and say first that it uses paid minutes once the free
   quota is gone.
2. **The session is the CI.** Before every push: the repository's full test suite, lint, typecheck and build (whatever
   CI used to run), in the session or in a subagent that returns only the summary. Code whose checks fail is not pushed.
   Write the results (counts) in the handoff and, for a PR, in the PR.
3. **A red or missing check on GitHub is expected.** Do not wait for it and do not push to "kick" it. On a PR you own,
   comment once: the checks do not run on GitHub by Rotem's decision, and these are the local results.
4. **A version is a branch `release/vX.Y.Z`** at the tested commit (cloud sessions may push branches but not tags).
   Bump the version per the repository's rules (CHANGELOG, handoff), run every check, then
   `git push origin HEAD:refs/heads/release/vX.Y.Z`. Never rely on a `[release]` commit or a tag to build anything.
   Pushing a branch other than the session's needs Rotem's approval once per repository (CLAUDE.md rule on branches).
5. **The machine that runs the software builds it.** For procurement: the office server pulls the newest
   `release/vX.Y.Z` (or tag `vX.Y.Z`) and builds and installs from the update screen
   (`server/procurement/services/git_update.py`, `docs/design/office-server-updates.md`). Another project that needs
   a deployment gets the same pattern, planned with Rotem first.
6. **Nothing else on GitHub that costs money:** no Codespaces, no larger runners, no Packages or LFS uploads, no paid
   Copilot. Large files (installers, data exports, backups) go to Drive or to the machine, not into git.
7. **GitHub stays the backup:** push small steps often, exactly as the repository's continuity rules say. Pushing,
   Issues, PRs and reviews (including Codex) cost nothing.

## 3. Other tools and accounts

- Claude Code sessions of any account read `CLAUDE.md` and get this skill from `.claude/skills/github-backup-only/`.
- Codex, Cursor, Copilot, Gemini and other agents read the block in `AGENTS.md`.
- claude.ai accounts: upload `github-backup-only.zip` under Customize → Skills, once per account, so every session has
  the skill even in a brand-new repository. The zip is `dist/github-backup-only.zip` in `rotem-ya/claude-guides/github-backup-only/` (rebuild it after a
  change: `python3 install.py --zip dist/github-backup-only.zip`).

The kit's source and its tests: https://github.com/rotem-ya/claude-guides/tree/main/github-backup-only (change it there,
run its tests with `python3 -m pytest -q github-backup-only`, rebuild the zip with
`python3 github-backup-only/install.py --zip github-backup-only/dist/github-backup-only.zip`, then re-run `install.py`
in each repository).
