"""Component catalog: dimensions + traceable source (submittal, file, which fields came from it).

Layers (later wins by id): repo `catalog/*.yaml` → user `<app_home>/catalog/*.yaml` → `<project>/catalog.yaml`.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import app_home

REPO_CATALOG = Path(__file__).resolve().parents[1] / "catalog"
CATEGORIES = ("gate_valve", "elbow", "pipe", "flange", "connector", "gasket", "bolt",
              "manhole", "cover", "trench_drain", "other")


def _read_yaml(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [d for d in data if isinstance(d, dict) and d.get("id")]


class Catalog:
    def __init__(self, items: dict[str, dict] | None = None):
        self._items: dict[str, dict] = dict(items or {})

    def __len__(self):
        return len(self._items)

    def __contains__(self, item_id):
        return item_id in self._items

    def get(self, item_id: str | None) -> dict | None:
        return self._items.get(item_id) if item_id else None

    def items(self, category: str | None = None) -> list[dict]:
        return [i for i in self._items.values() if category is None or i.get("category") == category]

    def add(self, item: dict, origin: str = "") -> None:
        self._items[item["id"]] = {**item, "_origin": origin}

    @staticmethod
    def missing_source(item: dict) -> list[str]:
        """Dimension keys that have no documented source ("source" when there is none at all)."""
        src = item.get("source") or {}
        if not (src.get("submittal") or src.get("file")):
            return ["source"]
        documented = set(src.get("fields") or [])
        keys = list((item.get("dims") or {}).keys())
        keys += [k for k in (item.get("flange") or {}) if k != "std"]
        return [k for k in keys if k not in documented]

    @staticmethod
    def label(item: dict, lang: str = "en") -> str:
        name = item.get("name") or {}
        return name.get(lang) or name.get("en") or item["id"]


def catalog_files(project=None) -> list[Path]:
    files = sorted(REPO_CATALOG.glob("*.yaml"))
    files += sorted((app_home() / "catalog").glob("*.yaml"))
    if project is not None and (project.path / "catalog.yaml").exists():
        files.append(project.path / "catalog.yaml")
    return files


def load_catalog(project=None) -> Catalog:
    cat = Catalog()
    for f in catalog_files(project):
        for item in _read_yaml(f):
            cat.add(item, origin=str(f))
    return cat


def save_item(item: dict, project=None) -> Path:
    """Add/replace an item in the project catalog (or the user catalog when no project)."""
    path = project.path / "catalog.yaml" if project is not None else app_home() / "catalog" / "user.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    items = _read_yaml(path) if path.exists() else []
    clean = {k: v for k, v in item.items() if not k.startswith("_")}
    items = [i for i in items if i["id"] != clean["id"]] + [clean]
    path.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
