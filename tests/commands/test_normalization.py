import pytest

from libre_dictum.text import normalize, replace_number_words


class TestNormalize:
    @pytest.mark.parametrize(
        "utterance",
        ["open terminal", "open terminal.", "open terminal?", "open terminal!", "open terminal;"],
    )
    def test_sentence_punctuation_is_stripped(self, utterance):
        assert normalize(utterance) == "open terminal"

    @pytest.mark.parametrize(
        "utterance", ["  open terminal  ", "open    terminal", "open\tterminal"]
    )
    def test_whitespace_collapses(self, utterance):
        assert normalize(utterance) == "open terminal"

    def test_case_is_preserved(self):
        assert normalize("Open Terminal") == "Open Terminal"

    @pytest.mark.parametrize("utterance", ["what's up", "well-known", "hello, world"])
    def test_meaningful_punctuation_survives(self, utterance):
        assert normalize(utterance) == utterance


class TestNumberWords:
    @pytest.mark.parametrize(
        ("spoken", "digits"),
        [("zero", "0"), ("one", "1"), ("five", "5"), ("nine", "9"), ("ten", "10")],
    )
    def test_number_words_become_digits(self, spoken, digits):
        assert normalize(f"go to line {spoken}") == f"go to line {digits}"

    def test_only_whole_words_are_replaced(self):
        assert replace_number_words("press tone") == "press tone"
        assert replace_number_words("someone") == "someone"

    def test_each_number_word_in_an_utterance_is_replaced(self):
        assert normalize("go to four two") == "go to 4 2"

    def test_a_capitalized_number_word_is_replaced_too(self):
        assert normalize("Two times press a") == "2 times press a"
        assert replace_number_words("FIVE") == "5"
