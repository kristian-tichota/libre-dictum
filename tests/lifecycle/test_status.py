import pytest

from libre_dictum.input.keymap import MODIFIER_KEYS
from libre_dictum.status import (
    MODIFIER_ORDER,
    OTHER_SYMBOL,
    SYMBOLS,
    Grip,
    HeldInput,
    HeldKey,
    canonical,
)


class TestWhatIsDown:
    def test_nothing_held_is_empty_and_says_nothing(self):
        assert HeldInput().empty
        assert HeldInput().summary() == ""

    def test_a_held_key_and_a_pending_one_are_told_apart(self):
        held = HeldInput(held=("shift",), pending=("ctrl",))

        assert [(key.key, key.grip) for key in held.pressed()] == [
            ("ctrl", Grip.PENDING),
            ("shift", Grip.HELD),
        ]

    def test_modifiers_come_in_a_fixed_order(self):
        held = HeldInput(held=("meta", "alt", "shift", "ctrl"))

        assert [key.key for key in held.pressed()] == list(MODIFIER_ORDER)

    def test_other_keys_come_after_the_modifiers(self):
        held = HeldInput(held=("a", "shift"))

        assert [key.key for key in held.pressed()] == ["shift", "a"]

    def test_the_same_modifier_twice_is_shown_once(self):
        assert [key.key for key in HeldInput(pending=("ctrl", "ctrl")).pressed()] == ["ctrl"]

    def test_two_names_for_one_key_are_shown_once(self):
        assert [key.key for key in HeldInput(held=("meta", "win")).pressed()] == ["meta"]

    def test_a_summary_names_the_keys_in_words(self):
        held = HeldInput(held=("shift",), pending=("ctrl",), saved="3")

        assert held.summary() == "⌃ ctrl pending, ⇧ shift held, saved '3'"

    def test_a_saved_value_alone_is_not_empty(self):
        assert not HeldInput(saved="3").empty


class TestGlyphs:
    @pytest.mark.parametrize("name", MODIFIER_ORDER)
    def test_every_modifier_has_a_symbol_of_its_own(self, name):
        assert name in SYMBOLS
        assert len({SYMBOLS[modifier] for modifier in MODIFIER_ORDER}) == len(MODIFIER_ORDER)

    def test_a_key_that_is_not_a_modifier_still_has_one(self):
        assert HeldKey("a", Grip.HELD).symbol
        assert not HeldKey("a", Grip.HELD).is_modifier

    def test_an_alias_is_displayed_under_its_canonical_name(self):
        assert canonical("win") == "meta"
        assert HeldKey("win", Grip.HELD).symbol == SYMBOLS["meta"]
        assert HeldKey("win", Grip.HELD).is_modifier


class TestAgainstTheKeymap:
    def test_the_display_order_covers_exactly_the_keymap_s_modifiers(self):
        """status spells its own modifier list so that it needs only the stdlib."""
        assert set(MODIFIER_ORDER) == {canonical(key) for key in MODIFIER_KEYS}

    def test_every_mouse_button_has_a_symbol_of_its_own(self):
        """And for the same reason, one step further along."""
        from libre_dictum.input.keymap import MOUSE_MAP

        assert set(MOUSE_MAP) <= set(SYMBOLS)
        glyphs = [SYMBOLS[button] for button in MOUSE_MAP]
        assert len(set(glyphs)) == len(glyphs)
        assert OTHER_SYMBOL not in glyphs

    def test_a_held_mouse_button_is_not_drawn_as_a_modifier(self):
        assert not HeldKey("left_mouse", Grip.HELD).is_modifier
        assert HeldKey("left_mouse", Grip.HELD).describe() == "left_mouse held"
