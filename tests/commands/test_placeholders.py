import pytest

from libre_dictum.input.dsl import expand_command, invalid_placeholders


class TestCaptureSubstitution:
    def test_placeholders_take_the_captures_in_order(self):
        assert expand_command("{1}({2})", ["3", "down"]) == "3(down)"

    def test_a_placeholder_may_be_repeated(self):
        assert expand_command("{1} + {1}", ["a"]) == "a + a"

    def test_a_response_without_placeholders_is_unchanged(self):
        assert expand_command("ctrl + c", ["a"]) == "ctrl + c"


class TestLeftoverPlaceholders:
    def test_leftovers_are_renumbered_for_the_next_round(self):
        assert expand_command("{1} + {2}", ["a"]) == "a + {1}"

    def test_renumbering_keeps_the_default(self):
        assert expand_command("{1}({2=1})", ["down"]) == "down({1=1})"

    def test_applying_defaults_resolves_the_leftovers(self):
        assert expand_command("{1=1}(down)", [], apply_defaults=True) == "1(down)"

    def test_a_leftover_without_a_default_disappears(self):
        assert expand_command("{1}(down)", [], apply_defaults=True) == "(down)"

    def test_a_saved_value_beats_the_default(self):
        assert expand_command("{1=1}(down)", ["5"], apply_defaults=True) == "5(down)"


class TestPlaceholderZero:
    def test_zero_is_left_alone(self):
        assert expand_command("{0}", ["x"]) == "{0}"

    def test_zero_is_reported_as_invalid(self):
        assert invalid_placeholders("{0} + {1}") == ["{0}"]

    @pytest.mark.parametrize("response", ["{1}", "ctrl + c", "{2=1}(down)"])
    def test_usable_responses_are_not_reported(self, response):
        assert invalid_placeholders(response) == []
