import pytest

from libre_dictum.errors import CommandSyntaxError
from libre_dictum.input.dsl import (
    KeyAction,
    KeyToken,
    VerbToken,
    apply_aliases,
    expand_command,
    expand_repeats,
    parse_response,
    parse_token,
    runnable_while_asleep,
    split_tokens,
)


class TestAliases:
    def test_an_alias_rewrites_the_response(self):
        aliases = {r"^write\((.*)\)$": r"script(copy;;\1) + ctrl + v"}
        assert apply_aliases("write(hello)", aliases) == "script(copy;;hello) + ctrl + v"

    def test_aliases_apply_in_declaration_order(self):
        assert apply_aliases("a", {"a": "b", "b": "c"}) == "c"

    def test_a_response_matching_nothing_is_untouched(self):
        assert apply_aliases("ctrl + c", {r"^write\((.*)\)$": "x"}) == "ctrl + c"


class TestRepeats:
    def test_a_repeat_group_expands(self):
        assert expand_repeats("3(down)") == "down+down+down"

    def test_a_repeat_group_may_hold_a_chord(self):
        assert expand_repeats("2(ctrl + c)") == "ctrl + c+ctrl + c"

    def test_nested_groups_expand_inside_out(self):
        assert expand_repeats("2(2(a))") == "a+a+a+a"

    def test_zero_repeats_to_nothing(self):
        assert expand_repeats("0(a)") == ""

    def test_text_without_a_group_is_unchanged(self):
        assert expand_repeats("ctrl + c") == "ctrl + c"


class TestSplitting:
    def test_a_chord_splits_on_plus(self):
        assert split_tokens("ctrl + c") == ["ctrl", "c"]

    def test_an_escaped_plus_is_a_literal(self):
        assert split_tokens(r"\+") == ["+"]

    def test_an_escaped_plus_survives_inside_a_verb(self):
        assert split_tokens(r"script(copy;;\+)") == ["script(copy;;+)"]


class TestParsingTokens:
    def test_a_bare_key_is_a_tap(self):
        assert parse_token("a") == KeyToken(key="a", action=KeyAction.TAP)

    def test_key_names_are_case_insensitive(self):
        assert parse_token("Ctrl") == KeyToken(key="ctrl", action=KeyAction.TAP)

    @pytest.mark.parametrize(
        ("text", "action"),
        [
            ("hold(shift)", KeyAction.HOLD),
            ("release(shift)", KeyAction.RELEASE),
            ("toggle(shift)", KeyAction.TOGGLE),
        ],
    )
    def test_key_verbs_carry_their_action(self, text, action):
        assert parse_token(text) == KeyToken(key="shift", action=action)

    @pytest.mark.parametrize(
        ("text", "verb", "argument"),
        [
            ("exec(firefox)", "exec", "firefox"),
            ("python(print('hi'))", "python", "print('hi')"),
            ("script(copy;;hello)", "script", "copy;;hello"),
            ("mode(dictate mode)", "mode", "dictate mode"),
            ("save(3)", "save", "3"),
        ],
    )
    def test_action_verbs_carry_their_argument(self, text, verb, argument):
        assert parse_token(text) == VerbToken(verb=verb, argument=argument)

    def test_a_mouse_button_is_a_key(self):
        assert parse_token("left_mouse") == KeyToken(key="left_mouse")

    def test_an_unknown_token_is_rejected(self):
        with pytest.raises(CommandSyntaxError, match="neither a known key nor a known verb"):
            parse_token("bogus")

    def test_an_unknown_key_inside_a_key_verb_is_rejected(self):
        with pytest.raises(CommandSyntaxError, match="unknown key"):
            parse_token("hold(bogus)")


class TestParsingResponses:
    def test_a_response_becomes_a_token_sequence(self):
        assert parse_response("ctrl + c") == (KeyToken(key="ctrl"), KeyToken(key="c"))

    def test_repeats_expand_before_parsing(self):
        assert parse_response("2(a)") == (KeyToken(key="a"), KeyToken(key="a"))

    def test_one_bad_token_rejects_the_whole_response(self):
        with pytest.raises(CommandSyntaxError):
            parse_response("ctrl + bogus")


class TestEscapingSubstitutedValues:
    """Recognized text is not syntax."""

    def test_a_plus_in_a_capture_is_escaped(self):
        assert expand_command("type({1})", ["9 + 10"]) == r"type(9 \+ 10)"

    def test_and_survives_the_round_trip_as_text(self):
        response = expand_command("type({1})", ["9 + 10"])
        assert parse_response(response) == (VerbToken(verb="type", argument="9 + 10"),)

    def test_the_old_clipboard_alias_survives_it_too(self):
        response = expand_command("write({1}) + mode(previous mode)", ["9 + 10"])
        expanded = apply_aliases(response, {r"^write\((.*)\)$": r"script(copy;;\1) + ctrl + v"})
        assert parse_response(expanded)[0] == VerbToken(verb="script", argument="copy;;9 + 10")

    def test_a_default_is_not_escaped(self):
        assert expand_command("{1=a \\+ b}", [], apply_defaults=True) == r"a \+ b"

    def test_a_saved_value_is_escaped_on_the_way_out(self, executor, backend):
        executor.state.saved_value = "9 + 10"
        executor.execute("type({1})")
        assert backend.sequence.count("shift↓") == 1
        assert backend.sequence.startswith("9↓ 9↑ space↓ space↑ shift↓")


class TestWhatASleepingMechanismMayRun:
    """One sentence: it can manage sleep and the display, and it can touch nothing else."""

    def test_a_wake_is_allowed(self):
        assert runnable_while_asleep("wake()")

    def test_so_is_another_sleep(self):
        assert runnable_while_asleep("sleep(pedal)")

    def test_and_a_display_phrase(self):
        assert runnable_while_asleep("hud(open)")

    def test_a_keystroke_is_not(self):
        assert not runnable_while_asleep("ctrl + c")

    def test_nor_a_mode_switch(self):
        assert not runnable_while_asleep("mode(dictate mode)")

    def test_nor_a_script(self):
        assert not runnable_while_asleep("script(anything)")

    def test_a_mixed_chain_is_refused_whole(self):
        assert not runnable_while_asleep("wake() + type(hello)")

    def test_an_empty_response_is_allowed(self):
        assert runnable_while_asleep(None)
        assert runnable_while_asleep("")

    def test_an_alias_cannot_smuggle_anything_past_it(self):
        assert runnable_while_asleep("hud(open)")

        assert not runnable_while_asleep(r"hud\(open\)", {r"hud\(open\)": "type(hello)"})

    def test_and_an_alias_may_make_a_response_allowed(self):
        assert not runnable_while_asleep("nap")

        assert runnable_while_asleep("nap", {"nap": "wake()"})

    def test_a_response_that_will_not_parse_is_refused(self):
        assert not runnable_while_asleep("hold(no-such-key)")
