import threading
import time

import pytest

from libre_dictum.audio.base import AudioStream
from libre_dictum.errors import DeviceError


class FakeCapture:
    """Stands in for a sounddevice stream."""

    def __init__(self) -> None:
        self.started = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True


class ProbeStream(AudioStream):
    """An AudioStream that counts blocks instead of recognizing them."""

    def __init__(self, **kwargs) -> None:
        super().__init__(name="probe", **kwargs)
        self.capture = FakeCapture()
        self.processed: list[str] = []
        self.events: list[str] = []
        self.seen = 0
        self.disabled_hook = 0
        self.resumed_on: list[str] = []
        self.explode = False

    def _open_stream(self):
        return self.capture

    def _note_block(self) -> None:
        super()._note_block()
        self.seen += 1

    def _process(self, block):
        if self.explode:
            raise RuntimeError("recognizer died")
        self.processed.append(block)
        self.events.append(block)
        self._emit(block)

    def _on_disabled(self) -> None:
        self.disabled_hook += 1

    def _on_resumed(self) -> None:
        self.resumed_on.append(threading.current_thread().name)
        self.events.append("<resumed>")


@pytest.fixture
def stream():
    heard: list[str] = []
    instance = ProbeStream(chunk_callback=heard.append)
    instance.heard = heard  # type: ignore[attr-defined]
    yield instance
    instance.stop(timeout=2)


def drain(stream, timeout=2.0):
    """Stop the stream and wait for the worker to finish."""
    stream.stop(timeout=timeout)
    assert stream._worker is None, "the worker never finished"


def wait_for(condition, timeout=2.0):
    """Wait for the worker to catch up, without pinning down how fast it is."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
    return False


class TestLifecycle:
    def test_starting_opens_capture(self, stream):
        stream.start()
        assert stream.capture.started

    def test_starting_twice_does_not_start_a_second_worker(self, stream):
        stream.start()
        first = stream._worker
        stream.start()
        assert stream._worker is first

    def test_stopping_closes_capture(self, stream):
        stream.start()
        drain(stream)
        assert stream.capture.closed

    def test_stopping_twice_is_harmless(self, stream):
        stream.start()
        stream.stop(timeout=2)
        stream.stop(timeout=2)


class TestProcessing:
    def test_a_disabled_stream_hears_nothing(self, stream):
        stream.start()
        stream._queue_block("block")
        drain(stream)

        assert stream.processed == []

    def test_an_enabled_stream_processes_and_emits(self, stream):
        stream.start()
        stream.enable()
        stream._queue_block("block")
        assert wait_for(lambda: stream.processed == ["block"])

        assert stream.heard == ["block"]

    def test_stopping_abandons_whatever_is_still_queued(self, stream):
        stream.enable()
        stream.start()
        stream.stop(timeout=2)

        for index in range(20):
            stream._queue_block(f"block-{index}")

        assert stream.processed == []

    def test_disabling_runs_the_reset_hook(self, stream):
        stream.disable()
        assert stream.disabled_hook == 1


class TestResuming:
    """A mode active again starts a new utterance."""

    def test_the_hook_runs_before_the_first_block_is_processed(self, stream):
        stream.start()
        stream.enable()
        stream._queue_block("block")

        assert wait_for(lambda: stream.events == ["<resumed>", "block"])

    def test_it_does_not_run_again_mid_utterance(self, stream):
        stream.start()
        stream.enable()
        for index in range(5):
            stream._queue_block(f"block-{index}")

        assert wait_for(lambda: stream.seen == 5)
        assert stream.events.count("<resumed>") == 1

    def test_a_mode_switched_away_from_and_back_resumes_again(self, stream):
        stream.start()
        stream.enable()
        stream._queue_block("before")
        assert wait_for(lambda: stream.events == ["<resumed>", "before"])

        stream.disable()
        stream._queue_block("while-away")
        assert wait_for(lambda: stream.seen == 2), "the worker never saw the disabled block"

        stream.enable()
        stream._queue_block("after")

        assert wait_for(lambda: stream.seen == 3)
        assert stream.events == ["<resumed>", "before", "<resumed>", "after"]

    def test_it_runs_on_the_worker_thread(self, stream):
        stream.start()
        stream.enable()
        stream._queue_block("block")
        assert wait_for(lambda: stream.resumed_on != [])

        assert stream.resumed_on == ["probe-worker"]


class TestFailure:
    def test_a_dying_worker_is_recorded_rather_than_silent(self, stream):
        stream.start()
        stream.enable()
        stream.explode = True
        stream._queue_block("block")
        stream.stop(timeout=2)

        assert isinstance(stream.failure, RuntimeError)

    def test_the_failure_is_logged(self, stream, caplog):
        stream.start()
        stream.enable()
        stream.explode = True
        stream._queue_block("block")
        stream.stop(timeout=2)

        assert "will not hear anything else" in caplog.text

    def test_a_healthy_stream_reports_no_failure(self, stream):
        stream.start()
        drain(stream)
        assert stream.failure is None


class TestReportingWhatIsBeingHeard:
    """The one signal a display has that the microphone is alive."""

    @pytest.fixture
    def reported(self):
        seen: list[str | None] = []
        instance = ProbeStream(partial_callback=seen.append)
        instance.seen_partials = seen  # type: ignore[attr-defined]
        yield instance
        instance.stop(timeout=2)

    def test_a_new_partial_is_reported(self, reported):
        reported._note_partial("buffer sa")

        assert reported.seen_partials == ["buffer sa"]

    def test_the_same_partial_again_is_not(self, reported):
        for _ in range(5):
            reported._note_partial("buffer sa")

        assert reported.seen_partials == ["buffer sa"]

    def test_the_empty_partial_is_a_value_and_not_an_absence(self, reported):
        reported._note_partial("")

        assert reported.seen_partials == [""]

    def test_silence_after_words_is_reported_as_silence(self, reported):
        reported._note_partial("buffer sa")
        reported._note_partial(None)

        assert reported.seen_partials == ["buffer sa", None]

    def test_switching_away_from_a_mode_clears_what_it_was_hearing(self, reported):
        reported._note_partial("buffer sa")

        reported.disable()

        assert reported.seen_partials[-1] is None

    def test_a_display_that_raises_does_not_deafen_the_recognizer(self, caplog):
        def angry(partial):
            raise RuntimeError("no")

        instance = ProbeStream(partial_callback=angry)
        try:
            instance._note_partial("buffer sa")
        finally:
            instance.stop(timeout=2)

        assert "Reporting a partial" in caplog.text

    def test_a_stream_with_no_display_reports_to_nobody_and_says_nothing(self, stream):
        stream._note_partial("buffer sa")


class TestStalledCapture:
    """A microphone that is unplugged does not raise: the blocks simply stop."""

    def test_silence_for_too_long_is_reported(self, stream, caplog):
        stream.start()
        stream._last_block = time.monotonic() - 60
        stream._last_check = time.monotonic()
        stream._check_for_stall()

        assert isinstance(stream.failure, DeviceError)
        assert "stopped delivering audio" in caplog.text

    def test_audio_coming_back_clears_the_failure(self, stream, caplog):
        stream.start()
        stream._last_block = time.monotonic() - 60
        stream._last_check = time.monotonic()
        stream._check_for_stall()

        stream._note_block()

        assert stream.failure is None

    def test_a_stream_being_stopped_is_not_a_stall(self, stream):
        stream.start()
        stream.stop(timeout=2)
        stream._last_block = time.monotonic() - 60
        stream._last_check = time.monotonic()
        stream._check_for_stall()

        assert stream.failure is None


class TestAWorkerThatWasNotRunning:
    """The false alarm at every single start, and why it was one."""

    def test_a_gap_in_its_own_checks_is_not_blamed_on_the_microphone(self, stream, caplog):
        stream.start()
        stream._last_block = stream._last_check = time.monotonic() - 60

        stream._check_for_stall()

        assert stream.failure is None, "a starved worker accused the device"
        assert "stopped delivering audio" not in caplog.text

    def test_it_starts_watching_again_rather_than_giving_up(self, stream):
        stream.start()
        stream._last_block = stream._last_check = time.monotonic() - 60

        stream._check_for_stall()
        assert stream.failure is None

        stream._last_block = time.monotonic() - 60
        stream._check_for_stall()

        assert isinstance(stream.failure, DeviceError), "the second window must report it"

    def test_the_first_empty_queue_after_a_busy_stretch_is_not_starvation(self, stream):
        stream.start()
        stream._last_check = time.monotonic() - 60
        stream._note_block()
        stream._last_block = time.monotonic() - 60

        stream._check_for_stall()

        assert isinstance(stream.failure, DeviceError)

    def test_a_dead_worker_is_not_overwritten_by_a_stall(self, stream):
        stream.failure = RuntimeError("recognizer died")
        stream._last_block = time.monotonic() - 60
        stream._check_for_stall()

        assert isinstance(stream.failure, RuntimeError)
