import pytest

pytest.importorskip("hid", reason="pedals extra not installed")

from libre_dictum.errors import DeviceError  # noqa: E402
from libre_dictum.pedals import device as device_module  # noqa: E402
from libre_dictum.pedals.device import PedalWatcher  # noqa: E402
from libre_dictum.pedals.reader import PedalEvent  # noqa: E402
from libre_dictum.settings import PedalSettings  # noqa: E402


def build(failing_attempts: int, retry_delays=(0.0, 0.0)):
    """A watcher whose first failing_attempts attempts lose the device."""
    events: list[PedalEvent] = []
    watcher = PedalWatcher(PedalSettings(), callback=events.append, retry_delays=retry_delays)
    attempts: list[int] = []

    def watch():
        attempts.append(1)
        if len(attempts) <= failing_attempts:
            raise DeviceError("cannot open the pedal device 0fd9:0086")

    watcher._watch = watch
    return watcher, attempts, events


def run(watcher) -> None:
    watcher.start()
    watcher._thread.join(timeout=5)
    assert not watcher._thread.is_alive(), "the pedal thread never finished"


class TestRetrying:
    def test_a_board_that_comes_back_is_picked_up(self, caplog):
        watcher, attempts, _ = build(failing_attempts=1)
        run(watcher)

        assert len(attempts) == 2
        assert "gave up" not in caplog.text

    def test_retrying_stops_after_a_bounded_number_of_attempts(self, caplog):
        watcher, attempts, _ = build(failing_attempts=99)
        run(watcher)

        assert len(attempts) == 3
        assert "gave up after 3 attempts" in caplog.text

    def test_the_delays_grow(self):
        assert list(device_module.RETRY_DELAYS) == sorted(device_module.RETRY_DELAYS)
        assert len(set(device_module.RETRY_DELAYS)) == len(device_module.RETRY_DELAYS)

    def test_each_failure_is_logged_with_the_attempt(self, caplog):
        watcher, _, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(watcher)

        assert "cannot open the pedal device" in caplog.text
        assert "attempt 1 of 2" in caplog.text

    def test_the_last_failure_is_kept_for_the_health_report(self):
        watcher, _, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(watcher)

        assert isinstance(watcher.failure, DeviceError)

    def test_stopping_interrupts_the_backoff(self):
        watcher, _, _ = build(failing_attempts=99, retry_delays=(60.0,))
        watcher.start()
        watcher.stop(timeout=5)

        assert not watcher.running

    def test_a_watcher_that_gave_up_is_not_running(self):
        watcher, _, _ = build(failing_attempts=99, retry_delays=(0.0,))
        run(watcher)

        assert not watcher.running


class TestAPedalTheDeviceTookWithIt:
    def test_it_is_released_when_the_device_dies(self, caplog):
        watcher, _, events = build(failing_attempts=99, retry_delays=())
        caplog.set_level("INFO")

        def watch():
            watcher.reader.update([0, 0, 0, 0, 1, 0, 0, 0])
            raise DeviceError("the board stopped answering")

        watcher._watch = watch
        run(watcher)

        assert [event.describe() for event in events] == ["left up (owed)"]
        assert "was still down" in caplog.text

    def test_a_callback_that_raises_does_not_break_the_retry_loop(self, caplog):
        def angry(event):
            raise RuntimeError("no")

        watcher = PedalWatcher(PedalSettings(), callback=angry, retry_delays=(0.0,))

        def watch():
            watcher.reader.update([0, 0, 0, 0, 1, 0, 0, 0])
            raise DeviceError("gone")

        watcher._watch = watch
        run(watcher)

        assert "gave up after 2 attempts" in caplog.text


class TestReading:
    def test_a_timed_out_read_is_not_an_event(self):
        events: list[PedalEvent] = []
        watcher = PedalWatcher(PedalSettings(), callback=events.append)
        reports = [[], [], [0, 0, 0, 0, 1, 0, 0, 0]]

        class Device:
            def read(self, length, timeout):
                if not reports:
                    watcher._stop.set()
                    return []
                return reports.pop(0)

        watcher._pump_reports(Device())

        assert [event.describe() for event in events] == ["left down"]

    def test_a_report_arriving_after_a_failure_reports_the_recovery(self, caplog):
        watcher = PedalWatcher(PedalSettings(), callback=lambda _: None)
        watcher.failure = DeviceError("was gone")
        caplog.set_level("INFO")

        class Device:
            def read(self, length, timeout):
                watcher._stop.set()
                return [0] * 8

        watcher._pump_reports(Device())

        assert watcher.failure is None
        assert "recovered" in caplog.text
