from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest

from libre_dictum.overlay import protocol

DEADLINE_SECONDS = 5.0


@pytest.fixture
def socket_path(tmp_path_factory) -> Path:
    """A socket path short enough for AF_UNIX."""
    return tmp_path_factory.mktemp("hud") / "s.sock"


class Display:
    """A connected display: reads frames, keeps the latest of each."""

    def __init__(self, path: Path) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(DEADLINE_SECONDS)
        self.socket.connect(str(path))
        self._stream = self.socket.makefile("rb")
        self.frames: list[protocol.Catalogue | protocol.State] = []

    def read(self) -> protocol.Catalogue | protocol.State:
        """The next frame, or a timeout that fails the test."""
        frame = protocol.decode(self._stream.readline())
        self.frames.append(frame)
        return frame

    def catalogue(self) -> protocol.Catalogue:
        frame = self.read()
        assert isinstance(frame, protocol.Catalogue), f"expected a catalogue, got {frame}"
        return frame

    def state(self) -> protocol.State:
        frame = self.read()
        assert isinstance(frame, protocol.State), f"expected a state, got {frame}"
        return frame

    def state_where(self, predicate) -> protocol.State:
        """The next state satisfying predicate, skipping catalogues on the way."""
        while True:
            frame = self.read()
            if isinstance(frame, protocol.State) and predicate(frame):
                return frame

    def close(self) -> None:
        self._stream.close()
        self.socket.close()


@pytest.fixture
def display(socket_path):
    """Connects a display to whatever is listening, and hangs up afterwards."""
    connected: list[Display] = []

    def connect(path: Path | None = None) -> Display:
        connected.append(Display(path or socket_path))
        return connected[-1]

    yield connect
    for one in connected:
        one.close()


class WedgedSocket:
    """A socket whose write does not return until it is released."""

    def __init__(self) -> None:
        self.released = threading.Event()
        self.sent: list[bytes] = []

    def sendall(self, payload: bytes) -> None:
        self.released.wait()
        self.sent.append(payload)

    def close(self) -> None:
        self.released.set()


class BrokenSocket:
    """A socket whose write fails, like one whose reader has gone away."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def sendall(self, payload: bytes) -> None:
        raise TimeoutError("timed out")

    def close(self) -> None:
        self.closed.set()
