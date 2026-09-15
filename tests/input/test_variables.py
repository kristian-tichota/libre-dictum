class TestSaving:
    def test_a_saved_value_fills_a_placeholder_in_the_next_command(self, executor, backend):
        executor.execute("save(3)")
        backend.clear()

        executor.execute("{1=1}(down)")
        assert backend.sequence == "down↓ down↑ down↓ down↑ down↓ down↑"

    def test_the_default_applies_when_nothing_was_saved(self, executor, backend):
        executor.execute("{1=1}(down)")
        assert backend.sequence == "down↓ down↑"

    def test_saving_emits_nothing(self, executor, backend):
        executor.execute("save(3)")
        assert backend.events == []


class TestConsuming:
    def test_a_keystroke_consumes_the_saved_value(self, executor, backend):
        executor.execute("save(3)")
        executor.execute("{1=1}(down)")
        backend.clear()

        executor.execute("{1=1}(down)")
        assert backend.sequence == "down↓ down↑"

    def test_any_keystroke_clears_the_saved_value(self, executor):
        executor.execute("save(3)")
        executor.execute("a")
        assert executor.state.saved_value is None

    def test_a_second_save_replaces_the_first(self, executor, backend):
        executor.execute("save(5)")
        executor.execute("save(3)")
        backend.clear()

        executor.execute("{1=1}(down)")
        assert backend.sequence == "down↓ down↑ down↓ down↑ down↓ down↑"

    def test_the_panic_verb_discards_a_saved_value(self, executor, backend):
        executor.execute("save(5)")
        executor.panic()
        backend.clear()

        executor.execute("{1=1}(down)")
        assert backend.sequence == "down↓ down↑"
