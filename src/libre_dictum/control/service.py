from __future__ import annotations

import contextlib
import logging
import socket
import threading
from collections.abc import Callable
from pathlib import Path

from ..errors import ProtocolError
from ..sockets import listen_privately, staging_path
from . import protocol

logger = logging.getLogger(__name__)

BACKLOG = 4

READ_TIMEOUT_SECONDS = 2.0

Handler = Callable[[str], str | None]


class ControlService:
    """Runs what other programs ask for by name, over one unix socket, one request at a time."""

    def __init__(
        self, path: Path, handler: Handler, *, timeout: float = READ_TIMEOUT_SECONDS
    ) -> None:
        self.path = Path(path)
        self._staging = staging_path(self.path)
        self._handler = handler
        self._timeout = timeout
        self.failure: BaseException | None = None
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._acceptor: threading.Thread | None = None

    @property
    def listening(self) -> bool:
        return self._listener is not None

    def start(self) -> None:
        """Bind the socket and take requests, or record why there is none."""
        with self._lock:
            if self._listener is not None:
                return
            try:
                listener = listen_privately(self.path, self._staging, backlog=BACKLOG)
            except OSError as exc:
                self.failure = exc
                logger.warning("No control socket at %s (%s)", self.path, exc)
                return
            self.failure = None
            self._listener = listener
            self._acceptor = threading.Thread(
                target=self._accept, args=(listener,), name="control", daemon=True
            )
            self._acceptor.start()
        logger.info("Control commands are taken on %s", self.path)

    def stop(self) -> None:
        """Stop taking requests and remove the socket."""
        with self._lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            # Workaround: close() alone leaves a thread blocked in accept() on Linux.
            with contextlib.suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            listener.close()
            self.path.unlink(missing_ok=True)

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError as exc:
                with self._lock:
                    failed = self._listener is listener
                    if failed:
                        self._listener = None
                if failed:
                    listener.close()
                    self.failure = exc
                    logger.error("Stopped taking control commands on %s: %s", self.path, exc)
                return
            with connection:
                try:
                    self._serve(connection)
                except Exception:  # noqa: BLE001 - the thread outlives every client
                    logger.exception("Serving a control client failed")

    def _serve(self, connection: socket.socket) -> None:
        """Answer every request on one connection until it hangs up."""
        try:
            connection.settimeout(self._timeout)
            with connection.makefile("rb") as stream:
                while line := stream.readline(protocol.MAX_LINE_BYTES + 1):
                    try:
                        name = protocol.decode_request(line)
                    except ProtocolError as exc:
                        connection.sendall(protocol.encode_reply(str(exc)))
                        return
                    connection.sendall(protocol.encode_reply(self._answer(name)))
        except OSError as exc:
            logger.info("A control client went away: %s", exc)

    def _answer(self, name: str) -> str | None:
        """What the handler makes of one request, or why it could not say."""
        try:
            return self._handler(name)
        except Exception:  # noqa: BLE001 - one failing request must not end the service
            logger.exception("Control command %r failed", name)
            return "it failed; see the log"
