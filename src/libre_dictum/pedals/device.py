from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence

import hid

from ..errors import DeviceError
from ..settings import PedalSettings
from .reader import PedalEvent, PedalReader

logger = logging.getLogger(__name__)

RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)


class PedalWatcher:
    """Feeds pedal edges to a callback from its own thread."""

    def __init__(
        self,
        settings: PedalSettings,
        *,
        callback: Callable[[PedalEvent], None],
        retry_delays: Sequence[float] = RETRY_DELAYS,
    ) -> None:
        self.settings = settings
        self.callback = callback
        self.reader = PedalReader(settings.buttons)
        self.failure: BaseException | None = None

        self._retry_delays = tuple(retry_delays)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.failure = None
        self._thread = threading.Thread(target=self._run, name="pedals", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("Pedal thread is still running after %.0fs; abandoning it", timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        """Whether the pedal thread is alive and has not been asked to stop."""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        """Read pedals until asked to stop, retrying a device that may yet come back."""
        attempts = len(self._retry_delays) + 1
        for attempt, delay in enumerate((0.0, *self._retry_delays), start=1):
            if self._stop.wait(delay):
                return

            try:
                self._watch()
                return
            except DeviceError as exc:
                self.failure = exc
                logger.error("Pedals: %s (attempt %d of %d)", exc, attempt, attempts)
            except BaseException as exc:  # noqa: BLE001 - an unplugged pedal is not fatal
                self.failure = exc
                logger.exception("Pedals stopped; nothing will come off the pedal board")
                return
            finally:
                self._release_everything()

        logger.error(
            "Pedals gave up after %d attempts; say the reload phrase to try again", attempts
        )

    def _watch(self) -> None:
        device = self._open()
        try:
            self._pump_reports(device)
        finally:
            device.close()

    def _open(self) -> hid.device:
        """Open the configured device, or say what to do about it."""
        device = hid.device()
        try:
            device.open(self.settings.vendor_id, self.settings.product_id)
        except Exception as exc:  # noqa: BLE001 - hidapi raises OSError or IOError
            raise DeviceError(
                f"cannot open the pedal device {self.settings.describe()}. Check that it is "
                "plugged in, and that a udev rule grants you access to its hidraw node "
                f"({exc})"
            ) from exc
        return device

    def _pump_reports(self, device: hid.device) -> None:
        """Read reports until asked to stop."""
        length, timeout = self.settings.report_length, self.settings.read_timeout_ms
        while not self._stop.is_set():
            report = device.read(length, timeout)
            if not report:
                continue

            if self.failure is not None:
                logger.info("Pedals recovered; the pedal board is live again")
                self.failure = None

            for event in self.reader.update(report):
                logger.debug("Pedal %s", event.describe())
                self.callback(event)

    def _release_everything(self) -> None:
        """Hand out the releases a pedal that was down when the device died still owes."""
        for event in self.reader.reset():
            logger.info("Pedal %s was still down; releasing it", event.name)
            try:
                self.callback(event)
            except Exception:  # noqa: BLE001 - teardown may not raise onto the retry loop
                logger.exception("Releasing pedal %r failed", event.name)
