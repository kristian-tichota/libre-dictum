import pytest

from libre_dictum.pattern import CommandPattern, PartialMatcher
from libre_dictum.text import normalize


def match(template: str, utterance: str):
    return CommandPattern.compile(template).match(normalize(utterance))


class TestWholeUtteranceMatching:
    def test_an_exact_utterance_matches(self):
        assert match("open terminal", "open terminal") == ()

    @pytest.mark.parametrize(
        "utterance",
        ["open", "open terminal now", "please open terminal", "open the terminal"],
    )
    def test_surrounding_text_does_not_match(self, utterance):
        assert match("open terminal", utterance) is None

    @pytest.mark.parametrize("utterance", ["open terminal", "Open Terminal", "OPEN TERMINAL"])
    def test_matching_ignores_case(self, utterance):
        assert match("open terminal", utterance) == ()

    def test_a_pattern_written_with_punctuation_matches_plain_speech(self):
        assert match("Open Terminal!", "open terminal") == ()


class TestLiteralText:
    @pytest.mark.parametrize("phrase", ["c plus plus", "dot star"])
    def test_metacharacters_are_matched_literally(self, phrase):
        assert match(phrase, phrase) == ()

    def test_a_metacharacter_does_not_become_a_wildcard(self):
        assert match("a.c", "abc") is None


class TestPlaceholders:
    def test_numeric_captures_digits(self):
        assert match("go to line {numeric}", "go to line 42") == ("42",)

    def test_numeric_accepts_spoken_numbers_through_normalization(self):
        assert match("go to line {numeric}", "go to line five") == ("5",)

    def test_any_captures_one_word(self):
        assert match("press {any}", "press enter") == ("enter",)
        assert match("press {any}", "press two words") is None

    def test_rest_captures_everything_left(self):
        assert match("{rest}", "anything at all goes here") == ("anything at all goes here",)

    def test_captures_are_returned_in_order(self):
        assert match("{numeric} times press {any}", "3 times press a") == ("3", "a")

    def test_placeholders_are_recorded(self):
        pattern = CommandPattern.compile("{numeric} times press {any}")
        assert pattern.placeholders == ("numeric", "any")
        assert not pattern.has_rest
        assert CommandPattern.compile("{rest}").has_rest


class TestGrammarExpansion:
    def test_numeric_expands_to_one_variant_per_digit_word(self):
        variants = CommandPattern.compile("{numeric} step up").grammar_variants()
        assert len(variants) == 10
        assert "five step up" in variants

    def test_two_numeric_placeholders_expand_to_every_combination(self):
        assert len(CommandPattern.compile("{numeric} and {numeric}").grammar_variants()) == 100

    def test_any_is_left_for_the_partial_matcher(self):
        assert CommandPattern.compile("press {any}").grammar_variants() == ["press {any}"]

    def test_a_pattern_without_placeholders_is_its_own_variant(self):
        assert CommandPattern.compile("open terminal").grammar_variants() == ["open terminal"]


class TestPartialMatcher:
    def test_a_phrase_at_the_end_of_a_partial_matches(self):
        matcher = PartialMatcher(["open terminal", "press {any}"])
        assert matcher.match("nonsense so far open terminal") == "open terminal"

    def test_the_word_the_recognizer_heard_fills_a_placeholder(self):
        assert PartialMatcher(["press {any}"]).match("press enter") == "press enter"

    def test_a_phrase_that_has_not_finished_does_not_match(self):
        assert PartialMatcher(["open terminal"]).match("open termi") is None

    def test_a_phrase_in_the_middle_does_not_match(self):
        assert PartialMatcher(["open terminal"]).match("open terminal and then") is None

    def test_later_phrases_win(self):
        matcher = PartialMatcher(["press {any}", "press enter"])
        assert matcher.match("press enter") == "press enter"
