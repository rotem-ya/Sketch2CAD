"""The program does not import a model client or keep a model key in settings."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT_LINE = re.compile(
    r"^\s*(?:import\s+anthropic\b|from\s+anthropic\b|import\s+sketch2cad\.ai\b|from\s+sketch2cad\.ai\b)"
)
API_KEY_FIELD = re.compile(r"^\s*(?:anthropic_)?api_key\s*:")
READS_KEY = re.compile(
    r"""(?:getenv|environ\.get)\(\s*['\"]ANTHROPIC_API_KEY['\"]"""
    r"""|environ\[\s*['\"]ANTHROPIC_API_KEY['\"]\]"""
)
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}
INSTALLERS = ("requirements.txt", "install.bat", "update.bat", "uninstall.bat", "run.bat", "run-debug.bat")


def _python_files():
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def test_no_anthropic_import_or_api_key_field():
    assert not (ROOT / "sketch2cad" / "ai").exists()
    for name in INSTALLERS:
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "anthropic" not in text, name
    from sketch2cad.config import Settings
    assert not any("api_key" in name.lower() for name in Settings.__dataclass_fields__)
    hits = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            if IMPORT_LINE.match(line) or API_KEY_FIELD.match(line) or READS_KEY.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert hits == []


def test_settings_strip_api_key_on_load_and_save(tmp_path, monkeypatch):
    monkeypatch.setenv("SKETCH2CAD_HOME", str(tmp_path / "home"))
    from sketch2cad.config import Settings
    path = tmp_path / "home" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "ui_language": "he",
        "anthropic_api_key": "sk-ant-LOCAL",
        "api_key": "sk-ant-OTHER",
        "projects": ["C:/Projects/One"],
    }), encoding="utf-8")
    loaded = Settings.load()
    assert loaded.ui_language == "he"
    assert loaded.projects == ["C:/Projects/One"]
    assert loaded.claude_auto_run is False
    stored = path.read_text(encoding="utf-8")
    assert "api_key" not in stored.lower()
    assert "sk-ant-" not in stored
    loaded.default_projects_root = "C:/Projects"
    loaded.save()
    again = json.loads(path.read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(again).lower()
    assert again["ui_language"] == "he"
    assert again["default_projects_root"] == "C:/Projects"
    assert again["claude_auto_run"] is False
