"""The program does not import a model client or the removed API package."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT_LINE = re.compile(
    r"^\s*(?:import\s+anthropic\b|from\s+anthropic\b|import\s+sketch2cad\.ai\b|from\s+sketch2cad\.ai\b)"
)
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def test_no_anthropic_or_ai_package_import():
    assert not (ROOT / "sketch2cad" / "ai").exists()
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "anthropic" not in requirements.lower()
    hits = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if IMPORT_LINE.match(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert hits == []
