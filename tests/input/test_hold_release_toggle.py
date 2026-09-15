import pytest


class TestHold:
    def test_a_held_key_is_pressed_but_not_released(self, executor, backend):
        executor.execute("hold(shift)")
        assert backend.sequence == "shift↓"
        assert executor.state.keys_held == ["shift"]

    def test_a_held_modifier_applies_to_a_later_command(self, executor, backend):
        executor.execute("hold(shift)")
        backend.clear()

        executor.execute("a")
        assert backend.sequence == "a↓ a↑"
        assert executor.state.keys_held == ["shift"]

    def test_holding_a_held_key_again_changes_nothing(self, executor, backend):
        executor.execute("hold(shift)")
        backend.clear()

        executor.execute("hold(shift)")
        assert backend.events == []
        assert executor.state.keys_held == ["shift"]

    def test_tapping_a_held_key_does_not_repeat_it(self, executor, backend):
        executor.execute("hold(shift)")
        backend.clear()

        executor.execute("shift")
        assert backend.events == []


class TestRelease:
    def test_a_held_key_is_released(self, executor, backend):
        executor.execute("hold(shift)")
        backend.clear()

        executor.execute("release(shift)")
        assert backend.sequence == "shift↑"
        assert executor.state.keys_held == []

    def test_releasing_a_key_that_is_not_held_is_harmless(self, executor, backend):
        assert executor.execute("release(shift)") is True
        assert executor.state.keys_held == []


class TestToggle:
    def test_toggle_holds_a_free_key(self, executor, backend):
        executor.execute("toggle(shift)")
        assert backend.sequence == "shift↓"
        assert executor.state.keys_held == ["shift"]

    def test_toggle_releases_a_held_key(self, executor, backend):
        executor.execute("toggle(shift)")
        backend.clear()

        executor.execute("toggle(shift)")
        assert backend.sequence == "shift↑"
        assert executor.state.keys_held == []

    @pytest.mark.parametrize("repetitions", [2, 4, 6])
    def test_an_even_number_of_toggles_leaves_nothing_held(self, executor, repetitions):
        for _ in range(repetitions):
            executor.execute("toggle(shift)")
        assert executor.state.keys_held == []


class TestModifierBookkeeping:
    def test_holding_a_pending_modifier_promotes_it(self, executor, backend):
        executor.execute("ctrl")
        executor.execute("hold(ctrl)")
        assert executor.state.keys_held == ["ctrl"]
        assert executor.state.modifiers_held == []

    def test_a_pending_modifier_is_dropped_after_the_chord(self, executor):
        executor.execute("ctrl + c")
        assert executor.state.modifiers_held == []
        assert executor.state.keys_held == []
