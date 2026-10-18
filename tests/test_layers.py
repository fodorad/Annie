"""Architecture rules, checked over every import in ``annie/`` (lazy ones included).

The layers, bottom to top:

- ``core``: models, config, logging; imports nothing else from Annie.
- ``parsers`` (file formats) and ``system`` (OS integration): ``core`` only.
- ``dataset`` (the domain) and ``media`` (decoding, rendering): below the UI, never each
  other's way up — ``dataset`` never imports ``media``.
- ``app_state``: the composition root that wires the backend for the UI.
- ``pages`` and ``app``: the UI. Only they (and the desktop wrapper) import NiceGUI.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "annie"

#: package → Annie packages it may import (besides itself).
_ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "parsers": {"core"},
    "system": {"core"},
    "media": {"core", "parsers"},
    "dataset": {"core", "parsers"},
    "app_state": {"core", "dataset", "media", "parsers"},
}

#: Modules allowed to import NiceGUI.
_UI = {"pages", "app", "desktop"}


def _layer(path: Path) -> str:
    """The layer a source file belongs to (``core``, ``pages``, ``app_state`` …)."""
    parts = path.relative_to(_ROOT).with_suffix("").parts
    return parts[0]


def _imports(path: Path) -> list[tuple[str, list[str]]]:
    """Every ``(module, imported names)`` in a file, from any scope."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(alias.name, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.module, [alias.name for alias in node.names]))
    return found


def _sources() -> list[Path]:
    return sorted(p for p in _ROOT.rglob("*.py") if p.name != "__init__.py")


class TestLayers(unittest.TestCase):
    def test_each_layer_imports_only_the_layers_below(self) -> None:
        problems = []
        for path in _sources():
            layer = _layer(path)
            if layer not in _ALLOWED:
                continue
            for module, _names in _imports(path):
                if not module.startswith("annie."):
                    continue
                target = module.split(".")[1]
                if target not in (_ALLOWED[layer] | {layer}):
                    problems.append(f"{path.relative_to(_ROOT)} imports {module}")
        self.assertEqual(problems, [])

    def test_only_the_ui_imports_nicegui(self) -> None:
        problems = [
            f"{path.relative_to(_ROOT)} imports {module}"
            for path in _sources()
            if _layer(path) not in _UI
            for module, _names in _imports(path)
            if module == "nicegui" or module.startswith("nicegui.")
        ]
        self.assertEqual(problems, [])

    def test_the_composition_root_is_used_only_by_the_ui(self) -> None:
        problems = [
            f"{path.relative_to(_ROOT)} imports the app state"
            for path in _sources()
            if _layer(path) not in _UI | {"app_state"}
            for module, _names in _imports(path)
            if module == "annie.app_state"
        ]
        self.assertEqual(problems, [])

    def test_no_private_names_cross_packages(self) -> None:
        problems = []
        for path in _sources():
            layer = _layer(path)
            for module, names in _imports(path):
                if not module.startswith("annie.") or module.split(".")[1] == layer:
                    continue
                private = [n for n in names if n.startswith("_") and not n.startswith("__")]
                if private:
                    problems.append(f"{path.relative_to(_ROOT)}: {module} {private}")
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
