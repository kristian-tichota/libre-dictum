import pytest
from evdev import ecodes

from libre_dictum.input.keymap import KEY_MAP, MODIFIER_KEYS, MOUSE_MAP, is_known_key, key_code


class TestLookup:
    @pytest.mark.parametrize("name", ["a", "5", "f13", "space", "enter", "pagedown", "["])
    def test_known_keys_are_recognized(self, name):
        assert is_known_key(name)

    def test_lookup_ignores_case(self):
        assert is_known_key("CTRL")
        assert key_code("Ctrl") == key_code("ctrl")

    def test_an_unknown_name_is_not_a_key(self):
        assert not is_known_key("bogus")

    def test_a_key_resolves_to_its_evdev_code(self):
        assert key_code("a") == (ecodes.KEY_A, False)

    def test_a_mouse_button_is_marked_as_such(self):
        assert key_code("left_mouse") == (ecodes.BTN_LEFT, True)


class TestCoverage:
    def test_every_modifier_is_a_key(self):
        assert KEY_MAP.keys() >= MODIFIER_KEYS

    def test_the_alphabet_is_complete(self):
        assert set("abcdefghijklmnopqrstuvwxyz") <= KEY_MAP.keys()

    def test_the_digits_are_complete(self):
        assert set("0123456789") <= KEY_MAP.keys()

    def test_keys_and_mouse_buttons_do_not_overlap(self):
        assert KEY_MAP.keys().isdisjoint(MOUSE_MAP.keys())
