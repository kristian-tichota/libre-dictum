from __future__ import annotations

import math

DERIVATIVE_CUTOFF_HZ = 1.0


def _alpha(cutoff_hz: float, dt: float) -> float:
    """The low-pass weight for one step of dt seconds at cutoff_hz."""
    tau = 1.0 / (2.0 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """Adaptive low-pass on a 2-D signal: smooths hard when still, lightly when moving."""

    def __init__(self, min_cutoff_hz: float = 1.0, beta: float = 0.05) -> None:
        self.min_cutoff_hz = min_cutoff_hz
        self.beta = beta
        self._value: tuple[float, float] | None = None
        self._derivative: tuple[float, float] = (0.0, 0.0)

    def filter(self, x: float, y: float, dt: float) -> tuple[float, float]:
        """Smooth one sample."""
        if self._value is None or dt <= 0:
            self._value = (x, y)
            self._derivative = (0.0, 0.0)
            return self._value

        previous_x, previous_y = self._value
        weight = _alpha(DERIVATIVE_CUTOFF_HZ, dt)
        rate_x = _blend((x - previous_x) / dt, self._derivative[0], weight)
        rate_y = _blend((y - previous_y) / dt, self._derivative[1], weight)
        self._derivative = (rate_x, rate_y)

        cutoff = self.min_cutoff_hz + self.beta * math.hypot(rate_x, rate_y)
        weight = _alpha(cutoff, dt)
        self._value = (_blend(x, previous_x, weight), _blend(y, previous_y, weight))
        return self._value

    def reset(self) -> None:
        """Forget the history, so the next sample is taken as-is."""
        self._value = None
        self._derivative = (0.0, 0.0)


def _blend(new: float, old: float, weight: float) -> float:
    return weight * new + (1.0 - weight) * old


class Neutral:
    """Where the head is considered to be resting."""

    def __init__(self, yaw: float = 0.0, pitch: float = 0.0) -> None:
        self.yaw = yaw
        self.pitch = pitch

    def relative(self, yaw: float, pitch: float) -> tuple[float, float]:
        """How far the given pose is from neutral."""
        return yaw - self.yaw, pitch - self.pitch

    def recenter(self, yaw: float, pitch: float) -> None:
        """Take the given pose as the new neutral."""
        self.yaw = yaw
        self.pitch = pitch

    def settle(self, offset_yaw: float, offset_pitch: float, dt: float, seconds: float) -> None:
        """Leak neutral toward the current pose, with seconds as the time constant."""
        if seconds <= 0 or dt <= 0:
            return
        share = min(1.0, dt / seconds)
        self.yaw += offset_yaw * share
        self.pitch += offset_pitch * share


class SubPixelCarry:
    """Keeps the fractional pixel between frames."""

    def __init__(self) -> None:
        self._x = 0.0
        self._y = 0.0

    def take(self, dx: float, dy: float) -> tuple[int, int]:
        """Add a fractional movement and return the whole pixels now owed."""
        self._x += dx
        self._y += dy
        whole_x = int(self._x)
        whole_y = int(self._y)
        self._x -= whole_x
        self._y -= whole_y
        return whole_x, whole_y

    def reset(self) -> None:
        """Drop the remainder, so a fresh movement does not inherit an old fraction."""
        self._x = 0.0
        self._y = 0.0
