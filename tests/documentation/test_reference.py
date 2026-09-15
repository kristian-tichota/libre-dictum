import ast
import re
import tomllib
from pathlib import Path

import pytest

from libre_dictum.settings import _TYPED_BLOCKS, GESTURE_HOLD_KEY
from libre_dictum.tracking.hands import CALIBRATION_KEYS

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs"
REFERENCE = DOCS / "reference" / "configuration.md"
OVERVIEW = DOCS / "architecture" / "overview.md"
SETTINGS = ROOT / "src" / "libre_dictum" / "settings.py"
PYPROJECT = ROOT / "pyproject.toml"

STRUCTURAL_KEYS = {
    "imports",
    "gestures",
    "pedals",
    GESTURE_HOLD_KEY,
    *_TYPED_BLOCKS,
    *CALIBRATION_KEYS,
}


def keys_the_loader_reads() -> set[str]:
    """Every configuration key settings.py looks up."""
    found = set(STRUCTURAL_KEYS)
    for node in ast.walk(ast.parse(SETTINGS.read_text())):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        function = node.func

        if (
            isinstance(function, ast.Attribute)
            and function.attr == "get"
            and isinstance(function.value, ast.Name)
            and function.value.id in {"data", "raw"}
            and isinstance(first, ast.Constant)
        ):
            found.add(first.value)

        if isinstance(function, ast.Name) and function.id == "knob":
            found.add(f"ht_{first.value}")

        if (
            isinstance(function, ast.Name)
            and function.id in {"_require", "_starting_layer_mode"}
            and isinstance(node.args[1], ast.Constant)
        ):
            found.add(node.args[1].value)

    return found


def documented_keys() -> set[str]:
    """Every key named in the first column of a table in the reference."""
    cells = re.findall(r"^\| (`[^|]+`) \|", REFERENCE.read_text(), flags=re.MULTILINE)
    return {key for cell in cells for key in re.findall(r"`([a-z_]+)`", cell)}


class TestConfigurationReference:
    def test_every_key_the_loader_reads_is_documented(self):
        undocumented = keys_the_loader_reads() - documented_keys()
        assert not undocumented, f"undocumented configuration keys: {sorted(undocumented)}"

    def test_every_documented_key_exists(self):
        invented = documented_keys() - keys_the_loader_reads()
        assert not invented, f"documented but never read: {sorted(invented)}"

    @pytest.mark.parametrize(
        "phrase", ["release everything", "previous mode", "reload config", "whisper-turbo"]
    )
    def test_the_defaults_it_quotes_are_the_real_ones(self, phrase):
        assert phrase in REFERENCE.read_text()

    def test_the_examples_in_the_reference_load(self, settings_of):
        import json

        blocks = re.findall(r"```json\n(.*?)```", REFERENCE.read_text(), flags=re.DOTALL)
        whole_configs = [b for b in blocks if b.lstrip().startswith("{") and '"modes"' in b]
        assert whole_configs, "the reference shows no complete configuration"
        for block in whole_configs:
            settings_of(json.loads(block))


class TestResponseReference:
    def test_it_documents_every_verb(self):
        from libre_dictum.input.dsl import ACTION_VERBS, KEY_VERBS

        text = (DOCS / "reference" / "response-dsl.md").read_text()
        for verb in ACTION_VERBS | set(KEY_VERBS):
            assert f"`{verb}(" in text, f"{verb}() is undocumented"

    def test_it_documents_every_placeholder(self):
        from libre_dictum.pattern import PLACEHOLDER_PATTERNS

        text = (DOCS / "reference" / "response-dsl.md").read_text()
        for placeholder in PLACEHOLDER_PATTERNS:
            assert f"`{{{placeholder}}}`" in text


class TestDocumentationLinks:
    """A moved page must not leave a dead cross-reference behind."""

    LINK = re.compile(r"\[[^\]]+\]\((?!https?://|#)([^)\s]+)\)")

    def test_every_relative_link_in_docs_resolves(self):
        broken = []
        for page in sorted(DOCS.rglob("*.md")):
            for target in self.LINK.findall(page.read_text()):
                path, _, anchor = target.partition("#")
                if path and not (page.parent / path).resolve().exists():
                    broken.append(f"{page.relative_to(DOCS.parent)} -> {target}")
        assert not broken, "dead links: " + ", ".join(broken)

    def test_the_index_lists_every_page(self):
        index = (DOCS / "README.md").read_text()
        pages = {p for p in DOCS.rglob("*.md") if p != DOCS / "README.md"}
        missing = [
            str(p.relative_to(DOCS)) for p in sorted(pages) if str(p.relative_to(DOCS)) not in index
        ]
        assert not missing, f"pages missing from docs/README.md: {missing}"


class TestThePureLayer:
    """Every module the layering table calls pure is on mypy's strict list."""

    def strict_modules(self) -> set[str]:
        overrides = tomllib.loads(PYPROJECT.read_text())["tool"]["mypy"]["overrides"]
        strict = [o["module"] for o in overrides if o.get("disallow_untyped_defs")]
        assert len(strict) == 1, "expected exactly one strict mypy override block"
        return {module.removeprefix("libre_dictum.") for module in strict[0]}

    def documented_pure_modules(self) -> set[str]:
        """The Pure logic row of the layering table in architecture/overview.md."""
        row = re.search(r"^\| \*\*Pure logic\*\* \|([^|]+)\|", OVERVIEW.read_text(), re.MULTILINE)
        assert row, "the layering table has no 'Pure logic' row"
        return {name.replace("/", ".") for name in re.findall(r"`([a-z_/]+)`", row.group(1))}

    def test_every_pure_module_is_type_checked_strictly(self):
        lenient = self.documented_pure_modules() - self.strict_modules()
        assert not lenient, f"pure but not on mypy's strict list: {sorted(lenient)}"
