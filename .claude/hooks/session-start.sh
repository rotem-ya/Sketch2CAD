#!/bin/bash
# token-kit SessionStart hook (cloud sessions): install the dependencies and build the code map before the first prompt,
# so no session spends turns on setup. Its stdout reaches the session's context: one status line only.
# A repository with its own needs puts them in .claude/hooks/setup.sh (run instead of the auto-detection below).
# Source: https://github.com/rotem-ya/claude-guides/tree/main/token-kit
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
# Every machine and account that opens this repository gets the skill at user level too, so it is there in new projects.
SRC=.claude/skills/token-kit
DST="$HOME/.claude/skills/token-kit"
if [ -f "$SRC/SKILL.md" ] && ! cmp -s "$SRC/codemap.py" "$DST/codemap.py" 2> /dev/null; then
  mkdir -p "$DST" && cp -R "$SRC/." "$DST/" 2> /dev/null || true
fi
# Local machines: map only (dependencies are the developer's own); cloud sessions: dependencies, then the map.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  python3 tools/codemap.py > /dev/null 2>&1 || true
  exit 0
fi
export LC_ALL=C.UTF-8 LANG=C.UTF-8
LOG=${TMPDIR:-/tmp}/session-start.log
: > "$LOG"
fail() { echo "session-start: $1 failed (see $LOG)"; }

if [ -x .claude/hooks/setup.sh ]; then
  .claude/hooks/setup.sh >> "$LOG" 2>&1 || fail "setup.sh"
else
  # Python: requirements-dev.txt, else requirements.txt, else an installable pyproject (with its dev extra when it has one)
  for d in . server backend app; do
    [ -d "$d" ] || continue
    if [ -f "$d/requirements-dev.txt" ]; then pip install -q -r "$d/requirements-dev.txt" >> "$LOG" 2>&1 || fail "pip ($d)"
    elif [ -f "$d/requirements.txt" ]; then pip install -q -r "$d/requirements.txt" >> "$LOG" 2>&1 || fail "pip ($d)"
    elif [ -f "$d/pyproject.toml" ] && grep -q '^\[project\]' "$d/pyproject.toml"; then
      pip install -q -e "$d[dev]" >> "$LOG" 2>&1 || pip install -q -e "$d" >> "$LOG" 2>&1 || fail "pip -e ($d)"
    fi
  done
  # Node: every tracked package.json outside node_modules, with the package manager its lockfile names
  git ls-files '*package.json' | grep -v node_modules | while read -r pkg; do
    d=$(dirname "$pkg")
    # lockfile-exact installs: a session start must never rewrite a lockfile
    if [ -f "$d/pnpm-lock.yaml" ]; then (cd "$d" && pnpm install --frozen-lockfile --prefer-offline >> "$LOG" 2>&1) || fail "pnpm ($d)"
    elif [ -f "$d/yarn.lock" ]; then (cd "$d" && yarn install --frozen-lockfile >> "$LOG" 2>&1) || fail "yarn ($d)"
    elif [ -f "$d/package-lock.json" ]; then (cd "$d" && npm ci --no-audit --no-fund --loglevel=error >> "$LOG" 2>&1) || fail "npm ci ($d)"
    elif [ "$d" = "." ]; then npm install --no-audit --no-fund --loglevel=error >> "$LOG" 2>&1 || fail "npm ($d)"
    fi
  done
  # Flutter / Dart
  if [ -f pubspec.yaml ] && command -v flutter > /dev/null; then flutter pub get >> "$LOG" 2>&1 || fail "flutter pub get"; fi
fi

python3 tools/codemap.py 2>> "$LOG" | sed 's/^/session-start: /' || fail "codemap"
