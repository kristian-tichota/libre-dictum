from __future__ import annotations

import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..errors import DeviceError

if TYPE_CHECKING:
    import sounddevice as sd

logger = logging.getLogger(__name__)

_QUEUE_TIMEOUT_SECONDS = 0.2

STALL_SECONDS = 5.0


class AudioStream(ABC):
    """A microphone and its worker thread."""

    def __init__(
        self,
        *,
        name: str,
        sample_rate: int = 16000,
        block_duration: float = 0.1,
        chunk_callback: Callable[[str], None] | None = None,
        partial_callback: Callable[[str | None], None] | None = None,
        stall_seconds: float = STALL_SECONDS,
    ) -> None:
        self.name = name
        self.sample_rate = sample_rate
        self.block_duration = block_duration
        self.chunk_callback = chunk_callback
        self.partial_callback = partial_callback
        self.stall_seconds = stall_seconds
        self.enabled = False
        self.failure: BaseException | None = None

        self._audio_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._stream: sd.InputStream | None = None
        self._last_block = 0.0
        self._last_check = 0.0
        self._partial: str | None = None

    def start(self) -> None:
        """Open the microphone and start the worker thread."""
        if self._worker is not None and self._worker.is_alive():
            return

        self._stop_event.clear()
        self.failure = None
        self._last_block = self._last_check = time.monotonic()
        self._stream = self._open_stream()
        self._stream.start()

        self._worker = threading.Thread(target=self._run, name=f"{self.name}-worker", daemon=True)
        self._worker.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the worker and close the microphone."""
        self._stop_event.set()

        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if self._worker is not None:
            self._worker.join(timeout=timeout)
            if self._worker.is_alive():
                logger.warning(
                    "Recognizer thread for mode %r is still running after %.0fs; abandoning it",
                    self.name,
                    timeout,
                )
            self._worker = None

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False
        self._note_partial(None)
        self._on_disabled()

    def _queue_block(self, block: Any) -> None:
        self._audio_queue.put(block)

    def _run(self) -> None:
        try:
            processing = False
            while not self._stop_event.is_set():
                try:
                    block = self._audio_queue.get(timeout=_QUEUE_TIMEOUT_SECONDS)
                except queue.Empty:
                    self._check_for_stall()
                    continue

                self._note_block()
                if not self.enabled:
                    processing = False
                    continue
                if not processing:
                    processing = True
                    self._on_resumed()
                self._process(block)
        except BaseException as exc:  # noqa: BLE001 - a dead worker must not be silent
            self.failure = exc
            logger.exception("Recognizer %r stopped: it will not hear anything else", self.name)

    def _check_for_stall(self) -> None:
        """Report a capture device that has stopped delivering audio."""
        if self._stop_event.is_set() or self.failure is not None:
            return
        now = time.monotonic()
        unwatched, self._last_check = now - self._last_check, now
        if unwatched > self.stall_seconds:
            logger.debug(
                "Mode %r went %.1fs without running, so the gap in its audio proves "
                "nothing; watching again",
                self.name,
                unwatched,
            )
            self._last_block = now
            return
        if now - self._last_block > self.stall_seconds:
            self.failure = DeviceError(
                f"the audio device for mode {self.name!r} stopped delivering audio"
            )
            logger.error("%s", self.failure)

    def _note_block(self) -> None:
        self._last_block = self._last_check = time.monotonic()
        if isinstance(self.failure, DeviceError):
            logger.info("Mode %r is hearing audio again", self.name)
            self.failure = None

    def _emit(self, text: str) -> None:
        """Hand recognized text to the dispatcher."""
        if text and self.chunk_callback:
            self.chunk_callback(text)

    def _note_partial(self, partial: str | None) -> None:
        """Report what is being heard right now, when it changed."""
        if partial == self._partial:
            return
        self._partial = partial
        if self.partial_callback is None:
            return
        try:
            self.partial_callback(partial)
        except Exception:  # noqa: BLE001 - a display must not deafen the recognizer
            logger.exception("Reporting a partial for mode %r failed", self.name)

    def _on_disabled(self) -> None:  # noqa: B027 - an optional hook, not a requirement
        """Hook: drop any half-accumulated state when the mode is switched away from."""

    def _on_resumed(self) -> None:  # noqa: B027 - an optional hook, not a requirement
        """Hook: the first block after the mode became active again, on the worker thread."""

    @abstractmethod
    def _open_stream(self) -> sd.InputStream:
        """Create (but do not start) the capture stream."""

    @abstractmethod
    def _process(self, block: Any) -> None:
        """Handle one captured block."""
