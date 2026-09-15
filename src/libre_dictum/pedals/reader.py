from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DEFAULT_TAP_MS = 500

DEFAULT_SEQUENCE_MS = 1000


@dataclass(frozen=True, slots=True)
class PedalEvent:
    """One pedal crossing between up and down."""

    name: str
    pressed: bool
    owed: bool = False

    def describe(self) -> str:
        edge = "down" if self.pressed else "up"
        return f"{self.name} {edge}{' (owed)' if self.owed else ''}"


class PedalReader:
    """Turns reports into edges, and remembers which pedals are down."""

    def __init__(self, buttons: Mapping[str, int]) -> None:
        self._offsets = dict(buttons)
        self.down: set[str] = set()

    @property
    def report_length(self) -> int:
        """The shortest report that carries every configured pedal."""
        return max(self._offsets.values(), default=-1) + 1

    def update(self, report: Sequence[int]) -> list[PedalEvent]:
        """Feed one report; return the pedals that just changed."""
        if len(report) < self.report_length:
            return []

        events = []
        for name, offset in self._offsets.items():
            pressed = bool(report[offset])
            if pressed == (name in self.down):
                continue
            if pressed:
                self.down.add(name)
            else:
                self.down.discard(name)
            events.append(PedalEvent(name=name, pressed=pressed))
        return events

    def reset(self) -> list[PedalEvent]:
        """Forget every pedal that was down, and hand back the releases it owes."""
        released = [
            PedalEvent(name=name, pressed=False, owed=True)
            for name in self._offsets
            if name in self.down
        ]
        self.down.clear()
        return released


class TapRuns:
    """The run of bare taps ending now, for a caller to match against its bindings."""

    def __init__(
        self, *, tap_ms: int = DEFAULT_TAP_MS, sequence_ms: int = DEFAULT_SEQUENCE_MS
    ) -> None:
        self.tap_ms = tap_ms
        self.sequence_ms = sequence_ms
        self._down: dict[str, tuple[float, int]] = {}
        self._run: list[str] = []
        self._last = 0.0

    def press(self, name: str, now: float, typed: int = 0) -> None:
        """Note when a pedal went down, and what had been typed by then."""
        self._down[name] = (now, typed)

    def release(self, name: str, now: float, typed: int = 0) -> tuple[str, ...]:
        """The run of taps ending with this release, if it was one."""
        held = self._down.pop(name, None)
        if held is None:
            return ()
        started, before = held
        if typed != before or (now - started) * 1000.0 > self.tap_ms:
            return ()

        if self._run and (now - self._last) * 1000.0 > self.sequence_ms:
            self._run.clear()
        self._run.append(name)
        self._last = now
        return tuple(self._run)

    def clear(self) -> None:
        """Forget the run, which is what a caller does once one of its bindings matched."""
        self._run.clear()

    def reset(self) -> None:
        """Forget everything, including which pedals are down: what a dead board leaves."""
        self._down.clear()
        self._run.clear()
