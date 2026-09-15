from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

WINDOW = Path(__file__).parents[2] / "src" / "libre_dictum" / "overlay" / "hud" / "window.py"
SOURCE = WINDOW.read_text()


def position(pattern: str) -> int:
    found = re.search(pattern, SOURCE, re.MULTILINE)
    assert found, f"{pattern} is not in window.py at all"
    return found.start()


class TestTheShimIsLoadedFirst:
    def test_the_library_is_loaded_before_gi_is_imported(self):
        assert position(r"^LIBRARY = _load_layer_shell\(") < position(r"^import gi\b")

    def test_it_is_loaded_into_the_global_scope(self):
        assert "mode=ctypes.RTLD_GLOBAL" in SOURCE

    def test_gtk_is_imported_before_it_is_initialised(self):
        assert position(r"^import gi\b") < position(r"Gtk\.init\(\)")


@pytest.mark.parametrize(
    "module",
    [
        "libre_dictum.overlay.hud.render",
        "libre_dictum.overlay.hud.style",
        "libre_dictum.overlay.hud.motion",
        "libre_dictum.overlay.hud.compositor",
        "libre_dictum.overlay.hud.client",
        "libre_dictum.overlay.protocol",
    ],
)
def test_nothing_window_imports_first_pulls_in_the_toolkit(module):
    """Every module window.py imports above the CDLL must be free of gi."""
    probe = (
        f"import sys, {module};"
        "print(sorted(m for m in sys.modules if m == 'gi' or m.startswith('gi.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]", f"{module} dragged in {result.stdout.strip()}"


def test_every_module_imported_above_the_load_is_one_of_those():
    """The list above is the whole list."""
    head = SOURCE[: position(r"^LIBRARY = _load_layer_shell\(")]
    relative = set(re.findall(r"^from (\.+)(\S*) import (.+)$", head, re.MULTILINE))
    named = set()
    for dots, module, imported in relative:
        parent = "libre_dictum.overlay.hud" if dots == "." else "libre_dictum.overlay"
        if module:
            named.add(f"{parent}.{module}")
        else:
            named.update(f"{parent}.{one.strip()}" for one in imported.split(","))
    checked = {
        "libre_dictum.overlay.hud.render",
        "libre_dictum.overlay.hud.style",
        "libre_dictum.overlay.hud.motion",
        "libre_dictum.overlay.hud.compositor",
        "libre_dictum.overlay.hud.client",
        "libre_dictum.overlay.protocol",
    }
    assert named <= checked, f"imported before the shim and unchecked: {sorted(named - checked)}"
