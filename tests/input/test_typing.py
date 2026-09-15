import logging

import pytest

from libre_dictum.input.keymap import CHARACTERS, untypeable
from libre_dictum.text import to_ascii


def typed(backend) -> str:
    """Reconstruct the text a recording backend was asked to type."""
    reverse = {(key, shift): character for character, (key, shift) in CHARACTERS.items()}
    shift = False
    text = []
    for event in backend.events:
        key, action = event[:-1], event[-1]
        if key == "shift":
            shift = action == "↓"
            continue
        if action == "↓":
            text.append(reverse[(key, shift)])
    return "".join(text)


class TestWhatComesOut:
    def test_a_lowercase_word_is_typed_key_by_key(self, executor, backend):
        executor.execute("type(hi)")
        assert backend.events == ["h↓", "h↑", "i↓", "i↑"]

    def test_a_capital_holds_shift_over_the_letter_only(self, executor, backend):
        executor.execute("type(Hi)")
        assert backend.events == ["shift↓", "h↓", "h↑", "shift↑", "i↓", "i↑"]

    def test_shift_is_not_re_pressed_across_a_run_of_capitals(self, executor, backend):
        executor.execute("type(ABC)")
        assert backend.events.count("shift↓") == 1
        assert typed(backend) == "ABC"

    def test_shift_never_outlives_the_text(self, executor, backend):
        executor.execute("type(Hi!)")
        assert backend.events[-1] == "shift↑"
        assert executor.state.keys_held == []

    @pytest.mark.parametrize(
        "text",
        [
            "hello world",
            "Hello, World!",
            "def f(x): return x * 2",
            "~/.config/libre-dictum",
            'a "quoted" thing & a `tick`',
            "9 - 10 = -1",
            "line one\nline two",
        ],
    )
    def test_the_text_arrives_intact(self, executor, backend, text):
        executor.execute(f"type({text})")
        assert typed(backend) == text

    def test_a_plus_in_the_text_needs_escaping_in_a_literal_response(self, executor, backend):
        executor.execute(r"type(9 \+ 10)")
        assert typed(backend) == "9 + 10"


class TestTextThatHasNoKey:
    """A dictation model writes characters a keyboard does not have."""

    def test_typographic_punctuation_is_folded_to_ascii(self, executor, backend):
        executor.execute("type(He said “hi” — really…)")
        assert typed(backend) == 'He said "hi" - really...'

    def test_an_accent_is_dropped_rather_than_the_word(self, executor, backend):
        executor.execute("type(café)")
        assert typed(backend) == "cafe"

    def test_what_has_no_key_at_all_is_left_out(self, executor, backend):
        executor.execute("type(hi 好)")
        assert typed(backend) == "hi "

    def test_and_leaving_it_out_is_not_silent(self, executor, backend, caplog):
        with caplog.at_level(logging.WARNING, logger="libre_dictum.input.executor"):
            executor.execute("type(hi 好)")
        assert "'好'" in caplog.text

    def test_the_rest_of_the_sentence_still_arrives(self, executor, backend, caplog):
        with caplog.at_level(logging.WARNING):
            executor.execute("type(the 好 word)")
        assert typed(backend) == "the  word"


class TestHeldShift:
    """hold(shift) outlives a command, so type() has to give it back."""

    def test_typing_restores_a_held_shift(self, executor, backend):
        executor.execute("hold(shift)")
        backend.events.clear()
        executor.execute("type(ab)")
        assert backend.events == ["shift↑", "a↓", "a↑", "b↓", "b↑", "shift↓"]
        assert executor.state.keys_held == ["shift"]

    def test_a_held_shift_needs_no_repress_for_a_capital(self, executor, backend):
        executor.execute("hold(shift)")
        backend.events.clear()
        executor.execute("type(AB)")
        assert backend.events == ["a↓", "a↑", "b↓", "b↑"]
        assert executor.state.keys_held == ["shift"]

    def test_a_pending_modifier_is_consumed_like_any_keystroke(self, executor, backend):
        executor.execute("ctrl + type(hi)")
        assert backend.events[-1] == "ctrl↑"
        assert executor.state.modifiers_held == []


class TestSavedValues:
    def test_typing_consumes_the_saved_value(self, executor):
        executor.execute("save(5)")
        executor.execute("type(hi)")
        assert executor.state.saved_value is None


class TestTiming:
    def test_a_character_costs_the_type_delay_not_the_input_delay(self, backend):
        from libre_dictum.input.executor import InputExecutor

        slept: list[float] = []
        executor = InputExecutor(backend, sleep=slept.append)
        executor.execute("type(abc)", input_delay=0.5, type_delay=0.001)
        assert slept == [0.001, 0.001, 0.001]


class TestUntypeable:
    def test_it_reports_each_character_once_in_order(self):
        assert untypeable("a好b好c☃") == ["好", "☃"]

    def test_it_is_empty_for_text_that_can_be_typed(self):
        assert untypeable(to_ascii("Hello, World! ~`|<>?")) == []
