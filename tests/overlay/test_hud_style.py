from __future__ import annotations

import re
from pathlib import Path

import pytest

from libre_dictum.overlay.hud import style

WINDOW = Path(__file__).parents[2] / "src" / "libre_dictum" / "overlay" / "hud" / "window.py"

CLASSES = (
    "surface",
    "mode",
    "glyph",
    "quiet",
    "dim",
    "hearing",
    "miss",
    "fault",
    "title",
    "axis",
    "phrase",
    "detail",
)

DIALS = (0.0, 0.25, style.DEFAULT_OPACITY, 1.0)


def rule(sheet: str, selector: str) -> str:
    """The body of one CSS rule, so an assertion can be about the right block."""
    found = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", sheet, re.DOTALL)
    assert found, f"no rule for {selector}"
    return found.group(1)


def alpha(declaration: str) -> float:
    """The alpha out of the one rgba(...) in a declaration."""
    found = re.search(r"rgba\([^)]*?,\s*([\d.]+)\s*\)", declaration)
    assert found, f"no rgba in {declaration!r}"
    return float(found.group(1))


class TestTheWindowNeverPaints:
    """The whole of the "fully opaque block" half of the report."""

    @pytest.mark.parametrize("dial", DIALS)
    def test_the_window_is_transparent_at_every_opacity(self, dial):
        body = rule(style.stylesheet(dial), f"window#{style.WIDGET_NAME}")
        assert "background-color: transparent" in body
        assert "background-image: none" in body

    def test_it_hangs_off_an_id_so_the_theme_cannot_outrank_it(self):
        assert f"window#{style.WIDGET_NAME}" in style.stylesheet()

    def test_the_window_name_in_the_stylesheet_is_the_one_the_window_is_given(self):
        assert "set_name(style.WIDGET_NAME)" in WINDOW.read_text()

    def test_no_shadow_is_left_around_the_surfaces(self):
        assert "box-shadow: none" in rule(style.stylesheet(), f"window#{style.WIDGET_NAME}")


class TestTheDial:
    def test_the_default_is_translucent_and_not_a_pane_of_glass(self):
        assert 0.0 < style.DEFAULT_OPACITY < 1.0

    @pytest.mark.parametrize("dial", DIALS)
    def test_the_panel_is_painted_at_exactly_what_was_asked_for(self, dial):
        assert alpha(rule(style.stylesheet(dial), "box.surface")) == dial

    def test_at_zero_there_is_no_rectangle_left_at_all(self):
        body = rule(style.stylesheet(0.0), "box.surface")
        assert alpha(body) == 0.0
        assert re.search(r"border: 1px solid rgba\([^)]*?,\s*0(\.0+)?\s*\)", body), body

    def test_the_edge_fades_with_the_panel_rather_than_being_fixed(self):
        def edge(dial: float) -> float:
            return alpha(rule(style.stylesheet(dial), "box.surface").split("border:")[1])

        assert edge(0.25) < edge(1.0)

    @pytest.mark.parametrize("asked, painted", [(-1.0, 0.0), (2.5, 1.0), (0.4, 0.4)])
    def test_an_impossible_opacity_is_clamped_and_never_refused(self, asked, painted):
        assert style.clamp(asked) == painted
        assert alpha(rule(style.stylesheet(asked), "box.surface")) == painted


class TestTheTextScale:
    """The first person to read the chip on a real screen could not."""

    def test_the_default_is_larger_than_the_system_font(self):
        assert style.DEFAULT_SCALE > 1.0

    @pytest.mark.parametrize("scale, percent", [(1.0, 100), (1.2, 120), (2.0, 200)])
    def test_the_scale_reaches_the_css_as_a_percentage(self, scale, percent):
        assert f"font-size: {percent}%" in rule(style.stylesheet(scale=scale), "box.surface")

    def test_it_is_on_the_surface_so_the_per_class_sizes_compose(self):
        sheet = style.stylesheet(scale=1.5)
        assert "font-size" in rule(sheet, "box.surface")
        assert "font-size" not in rule(sheet, "label")

    @pytest.mark.parametrize("asked, painted", [(0.1, style.MIN_SCALE), (9.0, style.MAX_SCALE)])
    def test_an_impossible_scale_is_clamped_and_never_refused(self, asked, painted):
        assert style.clamp(asked, style.MIN_SCALE, style.MAX_SCALE) == painted
        percent = round(painted * 100)
        assert f"font-size: {percent}%" in rule(style.stylesheet(scale=asked), "box.surface")

    def test_the_dials_do_not_interfere(self):
        body = rule(style.stylesheet(0.0, 1.5), "box.surface")
        assert "font-size: 150%" in body
        assert alpha(body) == 0.0


class TestLegibility:
    @pytest.mark.parametrize("dial", DIALS)
    def test_the_text_carries_its_own_shadow_at_every_opacity(self, dial):
        assert "text-shadow:" in rule(style.stylesheet(dial), "label")

    @pytest.mark.parametrize("name", CLASSES)
    def test_every_class_the_surfaces_use_is_styled(self, name):
        assert re.search(rf"\.{name}\b", style.stylesheet()), f"nothing styles .{name}"
