import threading

from libre_dictum.input.dsl import KeyAction
from libre_dictum.input.executor import InputExecutor


class TestSimpleKeys:
    def test_a_single_key_is_pressed_and_released(self, executor, backend):
        executor.execute("a")
        assert backend.sequence == "a↓ a↑"

    def test_a_chord_holds_the_modifier_until_the_key_completes_it(self, executor, backend):
        executor.execute("ctrl + c")
        assert backend.sequence == "ctrl↓ c↓ c↑ ctrl↑"

    def test_several_modifiers_are_released_together(self, executor, backend):
        executor.execute("ctrl + shift + t")
        assert backend.sequence == "ctrl↓ shift↓ t↓ t↑ ctrl↑ shift↑"

    def test_a_repeat_group_repeats_the_whole_chord(self, executor, backend):
        executor.execute("2(ctrl + c)")
        assert backend.sequence == "ctrl↓ c↓ c↑ ctrl↑ ctrl↓ c↓ c↑ ctrl↑"

    def test_a_mouse_button_goes_to_the_mouse(self, executor, backend):
        executor.execute("left_mouse")
        assert backend.sequence == "left_mouse↓ left_mouse↑"


class TestRejectedResponses:
    def test_an_unknown_key_emits_nothing(self, executor, backend):
        assert executor.execute("bogus") is False
        assert backend.events == []

    def test_a_response_is_rejected_whole(self, executor, backend):
        assert executor.execute("ctrl + bogus") is False
        assert backend.events == []

    def test_an_unknown_key_inside_hold_emits_nothing(self, executor, backend):
        assert executor.execute("hold(bogus)") is False
        assert backend.events == []

    def test_a_rejected_response_is_reported(self, executor, caplog):
        executor.execute("bogus")
        assert "bogus" in caplog.text


class TestDelays:
    def test_the_configured_delay_is_applied_after_every_event(self, backend):
        from libre_dictum.input.executor import InputExecutor

        delays: list[float] = []
        InputExecutor(backend, sleep=delays.append).execute("ctrl + c", input_delay=0.02)
        assert delays == [0.02, 0.02, 0.02, 0.02]


class TestReleaseAll:
    def test_everything_held_is_released(self, executor, backend):
        executor.execute("hold(ctrl) + hold(shift)")
        backend.clear()

        executor.release_all()
        assert backend.sequence == "ctrl↑ shift↑"
        assert executor.state.keys_held == []

    def test_a_pending_modifier_is_released_too(self, executor, backend):
        executor.execute("ctrl")
        backend.clear()

        executor.release_all()
        assert backend.sequence == "ctrl↑"
        assert executor.state.modifiers_held == []

    def test_releasing_nothing_emits_nothing(self, executor, backend):
        executor.release_all()
        assert backend.events == []


class TestKeyActionEnum:
    def test_toggle_resolves_to_hold_or_release_and_never_leaks(self, executor):
        executor.execute("toggle(shift)")
        assert executor.state.keys_held == ["shift"]
        assert KeyAction.TOGGLE not in executor.state.keys_held


class TestReportingHeldInput:
    """What is still down is reported, because nothing on screen would otherwise say so."""

    def executor_reporting_to(self, backend, reports):
        return InputExecutor(backend, on_held_change=reports.append, sleep=lambda _: None)

    def test_a_hold_is_reported(self, backend):
        reports = []
        self.executor_reporting_to(backend, reports).execute("hold(shift)")

        assert [report.summary() for report in reports] == ["⇧ shift held"]

    def test_a_pending_modifier_is_reported(self, backend):
        reports = []
        self.executor_reporting_to(backend, reports).execute("ctrl")

        assert [report.summary() for report in reports] == ["⌃ ctrl pending"]

    def test_a_completed_chord_reports_nothing(self, backend):
        reports = []
        self.executor_reporting_to(backend, reports).execute("ctrl + c")

        assert reports == []

    def test_releasing_reports_the_empty_picture(self, backend):
        reports = []
        executor = self.executor_reporting_to(backend, reports)

        executor.execute("hold(shift)")
        executor.execute("release(shift)")

        assert [report.summary() for report in reports] == ["⇧ shift held", ""]

    def test_the_same_picture_is_not_reported_twice(self, backend):
        reports = []
        executor = self.executor_reporting_to(backend, reports)

        executor.execute("hold(shift)")
        executor.execute("hold(shift)")
        executor.release_all()
        executor.release_all()

        assert len(reports) == 2

    def test_panic_reports_that_everything_is_free(self, backend):
        reports = []
        executor = self.executor_reporting_to(backend, reports)

        executor.execute("hold(shift) + ctrl")
        executor.panic()

        assert reports[-1].empty

    def test_a_saved_value_is_reported(self, backend):
        reports = []
        self.executor_reporting_to(backend, reports).execute("save(3)")

        assert reports[-1].saved == "3"

    def test_a_listener_that_raises_does_not_fail_the_command(self, backend, caplog):
        def explode(held):
            raise RuntimeError("the display is on fire")

        executor = InputExecutor(backend, on_held_change=explode, sleep=lambda _: None)

        assert executor.execute("hold(shift) + a") is True
        assert "a↓" in backend.sequence
        assert "the display is on fire" in caplog.text

    def test_a_wedged_display_cannot_stop_panic(self, backend):
        """A display stuck on an earlier update holds no lock that panic needs."""
        entered, wedged = threading.Event(), threading.Event()

        def listener(held):
            if entered.is_set():
                return
            entered.set()
            wedged.wait(60)

        executor = InputExecutor(backend, on_held_change=listener, sleep=lambda _: None)
        holder = threading.Thread(target=lambda: executor.execute("hold(shift)"))
        holder.start()
        assert entered.wait(5), "the listener was never called"

        panicked = threading.Event()
        rescue = threading.Thread(target=lambda: (executor.panic(), panicked.set()), daemon=True)
        rescue.start()

        try:
            assert panicked.wait(5), "panic() waited for the wedged display"
            assert "shift↑" in backend.sequence
            assert executor.held().empty
        finally:
            wedged.set()
            holder.join(5)
            rescue.join(5)
