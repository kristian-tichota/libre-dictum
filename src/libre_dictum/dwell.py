from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .settings import DwellSettings

CLICK = "left_mouse"

GRACE_FRACTION = 0.5

EASE_SECONDS = 0.1

ARRIVAL_WINDOW_SECONDS = 1.0

MAX_GAP_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class Step:
    """Where one pose points, the ring to draw around it, and whether to click there."""

    yaw: float
    pitch: float
    ring: float | None = None
    clicked: bool = False
    click: bool = False


@dataclass(slots=True)
class _Stop:
    since: float
    typed: int
    armed: bool
    yaw_total: float
    pitch_total: float
    frames: int = 1
    clicked_at: tuple[float, float] | None = None

    @property
    def mean(self) -> tuple[float, float]:
        return self.yaw_total / self.frames, self.pitch_total / self.frames

    def add(self, yaw: float, pitch: float) -> None:
        self.yaw_total += yaw
        self.pitch_total += pitch
        self.frames += 1


class Dwell:
    """Clicks once where the head stops after travelling, and holds the pointer there."""

    def __init__(self, settings: DwellSettings) -> None:
        self.settings = settings
        self._recent: deque[tuple[float, float, float]] = deque()
        self._last: float | None = None
        self._stop: _Stop | None = None

    def feed(
        self, at: float, yaw: float, pitch: float, *, typed: int = 0, held: bool = False
    ) -> Step:
        """One smoothed pose; typed counts input so far, held says a mouse button is down."""
        if self._last is not None and at - self._last > MAX_GAP_SECONDS:
            self.reset()
        self._last = at

        stop = self._stop
        if stop is None or _distance(stop.mean, yaw, pitch) > self.settings.radius:
            stop = self._stop = _Stop(
                since=at,
                typed=typed,
                armed=self._arrived(yaw, pitch),
                yaw_total=yaw,
                pitch_total=pitch,
            )
        else:
            stop.add(yaw, pitch)
        self._remember(at, yaw, pitch)

        if stop.clicked_at is not None:
            return Step(*stop.clicked_at, ring=1.0, clicked=True)
        if held or typed != stop.typed:
            stop.armed = False

        elapsed = at - stop.since
        grace = self.settings.seconds * GRACE_FRACTION
        if not stop.armed or elapsed < grace:
            return Step(yaw, pitch)

        mean = stop.mean
        if elapsed >= self.settings.seconds:
            stop.clicked_at = mean
            return Step(*mean, ring=1.0, clicked=True, click=True)

        weight = min(1.0, (elapsed - grace) / EASE_SECONDS)
        progress = (elapsed - grace) / (self.settings.seconds - grace)
        return Step(
            yaw + (mean[0] - yaw) * weight,
            pitch + (mean[1] - pitch) * weight,
            ring=progress,
        )

    def reset(self) -> None:
        """Forget the stop and the travel before it, for a gap with no face in it."""
        self._recent.clear()
        self._last = None
        self._stop = None

    def _arrived(self, yaw: float, pitch: float) -> bool:
        """Whether the head was at least the arrival angle away within the last window."""
        if self.settings.arrival <= 0:
            return True
        return any(
            _distance((then_yaw, then_pitch), yaw, pitch) >= self.settings.arrival
            for _, then_yaw, then_pitch in self._recent
        )

    def _remember(self, at: float, yaw: float, pitch: float) -> None:
        recent = self._recent
        recent.append((at, yaw, pitch))
        while recent and at - recent[0][0] > ARRIVAL_WINDOW_SECONDS:
            recent.popleft()


def _distance(point: tuple[float, float], yaw: float, pitch: float) -> float:
    return math.hypot(yaw - point[0], pitch - point[1])
