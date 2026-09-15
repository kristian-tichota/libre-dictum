from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from libre_dictum.input.executor import InputExecutor
from libre_dictum.layers import LayerState
from libre_dictum.modes import ModeManager
from libre_dictum.settings import AppSettings, ModeSettings, parse_settings
from libre_dictum.status import HeldInput

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "configs"

PRESS = "↓"
RELEASE = "↑"


class RecordingBackend:
    """An InputBackend that records instead of typing."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.movements: list[tuple[float, float]] = []
        self.positions: list[tuple[float, float]] = []
        self.closed = False

    def press(self, key: str) -> None:
        self.events.append(f"{key}{PRESS}")

    def release(self, key: str) -> None:
        self.events.append(f"{key}{RELEASE}")

    def move_relative(self, dx: float, dy: float) -> None:
        self.movements.append((dx, dy))

    def move_absolute(self, x: float, y: float) -> None:
        self.positions.append((x, y))

    def close(self) -> None:
        self.closed = True

    @property
    def sequence(self) -> str:
        """The recorded events as one readable string, e.g."""
        return " ".join(self.events)

    def clear(self) -> None:
        self.events.clear()
        self.movements.clear()
        self.positions.clear()


class FakeStream:
    """Stands in for a recognizer: records its lifecycle, loads no model."""

    def __init__(
        self,
        mode: ModeSettings,
        settings: AppSettings,
        on_text: Callable[[str], None],
        on_partial: Callable[[str | None], None] | None = None,
    ):
        self.mode = mode
        self.settings = settings
        self.on_text = on_text
        self.on_partial = on_partial
        self.enabled = False
        self.started = False
        self.stopped = False
        self.failure: BaseException | None = None

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False


class FakeIndicator:
    """Stands in for the tray icon."""

    def __init__(self) -> None:
        self.colours: dict[str, tuple[int, int, int] | None] = {}
        self.layers = LayerState()
        self.fault: str | None = None
        self.held = HeldInput()
        self.held_pushes: list[HeldInput] = []
        self.shown = False

    def add_mode(self, key: str, rgb: tuple[int, int, int] | None) -> None:
        self.colours[key] = rgb

    def set_layers(self, layers: LayerState) -> None:
        self.layers = layers

    @property
    def current(self) -> str | None:
        """The voice layer's mode -- what "the active mode" meant before there were layers."""
        return self.layers.voice

    def set_fault(self, reason: str) -> None:
        self.fault = reason

    def clear_fault(self) -> None:
        self.fault = None

    def set_held(self, held: HeldInput) -> None:
        self.held = held
        self.held_pushes.append(held)

    def show(self) -> None:
        self.shown = True

    def hide(self) -> None:
        self.shown = False


@pytest.fixture
def backend() -> RecordingBackend:
    return RecordingBackend()


@pytest.fixture
def executor(backend: RecordingBackend) -> InputExecutor:
    """An executor whose input delay costs no wall-clock time."""
    return InputExecutor(backend, sleep=lambda _: None)


@pytest.fixture
def settings_of(tmp_path: Path) -> Callable[[dict[str, Any]], AppSettings]:
    """Build validated settings from a config dict, as if it had been loaded from disk."""

    def build(data: dict[str, Any]) -> AppSettings:
        return parse_settings(data, tmp_path)

    return build


@pytest.fixture
def config_fixture() -> Callable[[str], dict[str, Any]]:
    """Load one of the JSON configs in tests/fixtures/configs."""

    def load(name: str) -> dict[str, Any]:
        return json.loads((FIXTURE_DIR / name).read_text())

    return load


@pytest.fixture
def running_modes(executor, settings_of):
    """A started ModeManager backed by fake streams, plus its parts."""

    def build(data: dict[str, Any], indicator: FakeIndicator | None = None) -> ModeManager:
        streams: dict[str, FakeStream] = {}

        def factory(mode, settings, on_text, on_partial=None):
            streams[mode.name] = FakeStream(mode, settings, on_text, on_partial)
            return streams[mode.name]

        manager = ModeManager(
            settings_of(data),
            executor=executor,
            on_text=lambda text: None,
            stream_factory=factory,
            indicator=indicator,
        )
        manager.streams = streams  # type: ignore[attr-defined] - test convenience
        manager.start()
        return manager

    return build
