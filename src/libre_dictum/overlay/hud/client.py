from __future__ import annotations

import contextlib
import logging
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from ...errors import ProtocolError
from ..protocol import Catalogue, State, check_socket_path, read_frames

logger = logging.getLogger(__name__)

RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)


class HudClient:
    """The latest catalogue and the latest state, kept current for as long as it runs."""

    def __init__(
        self,
        path: Path,
        *,
        on_change: Callable[[], None] | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS,
    ) -> None:
        self.path = check_socket_path(Path(path))
        self.catalogue: Catalogue | None = None
        self.state: State | None = None
        self.connected = False

        self._on_change = on_change
        self._retry_delays = retry_delays
        self._stopping = threading.Event()
        self._reader: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        """Begin connecting, on a thread of its own."""
        if self._reader is not None:
            return
        self._stopping.clear()
        self._reader = threading.Thread(target=self._run, name="hud-reader", daemon=True)
        self._reader.start()

    def stop(self) -> None:
        """Stop reading and close the socket."""
        self._stopping.set()
        current, self._socket = self._socket, None
        if current is not None:
            with contextlib.suppress(OSError):
                current.shutdown(socket.SHUT_RDWR)
            current.close()
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.join(timeout=2.0)

    def _run(self) -> None:
        attempt = 0
        complained = False
        while not self._stopping.is_set():
            try:
                self._read_until_closed()
                attempt = 0
                complained = False
            except (OSError, ProtocolError) as exc:
                if not complained:
                    logger.warning("Waiting for libre-dictum on %s (%s)", self.path, exc)
                    complained = True
                else:
                    logger.debug("Still waiting for %s (%s)", self.path, exc)
            except Exception:  # noqa: BLE001 - the reader thread outlives every failure
                logger.exception("Reading %s failed", self.path)

            self._note_disconnected()
            delay = self._retry_delays[min(attempt, len(self._retry_delays) - 1)]
            attempt += 1
            if self._stopping.wait(delay):
                return

    def _read_until_closed(self) -> None:
        """One connection, from connect to end of stream."""
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = connection
        try:
            connection.connect(str(self.path))
            logger.info("Connected to libre-dictum on %s", self.path)
            self.connected = True
            self._changed()
            with connection.makefile("rb") as stream:
                for frame in read_frames(stream):
                    self._accept(frame)
        finally:
            self._socket = None
            connection.close()

    def _accept(self, frame: Catalogue | State) -> None:
        if isinstance(frame, Catalogue):
            self.catalogue = frame
        else:
            self.state = frame
        self._changed()

    def _note_disconnected(self) -> None:
        if self.connected:
            self.connected = False
            self._changed()

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 - a display must not kill the thread feeding it
            logger.exception("Redrawing after a frame failed")
