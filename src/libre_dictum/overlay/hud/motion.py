from __future__ import annotations

SIDES = ("left", "right")

DEFAULT_SIDE = "right"

DURATION_MS = 180.0

EPSILON = 0.5


def side(anchor: str) -> str:
    """Which screen edge anchor sits against, horizontally."""
    tail = anchor.rsplit("-", 1)[-1]
    return tail if tail in SIDES else DEFAULT_SIDE


def clearance(chip: str, sheet: str, *, width: float, gap: float = 0.0) -> float:
    """How far in the chip sits to clear the sheet."""
    if side(chip) != side(sheet):
        return 0.0
    return max(0.0, width + gap)


def ease(fraction: float) -> float:
    """Smoothstep over 0..1, clamped rather than refused."""
    step = min(1.0, max(0.0, fraction))
    return step * step * (3.0 - 2.0 * step)


class Slide:
    """One number easing towards another, advanced by a clock the caller owns."""

    def __init__(self, value: float = 0.0, *, duration: float = DURATION_MS) -> None:
        self.duration = duration
        self.moving = False
        self._value = value
        self._from = value
        self._to = value
        self._began = 0.0

    @property
    def value(self) -> float:
        """Where it is, as of the last advance."""
        return self._value

    @property
    def target(self) -> float:
        """Where it is going, which is where it is when it is not moving."""
        return self._to

    def aim(self, target: float, *, now: float) -> bool:
        """Send it to target, and say whether that needs any frames."""
        if target == self._to:
            return self.moving
        self._from = self._value
        self._to = target
        self._began = now
        self.moving = abs(target - self._value) > EPSILON
        if not self.moving:
            self._value = target
        return self.moving

    def advance(self, now: float) -> float:
        """Step to now, and return where that is."""
        if not self.moving:
            return self._value
        elapsed = now - self._began
        if self.duration <= 0.0 or elapsed >= self.duration:
            self._value = self._to
            self.moving = False
        else:
            self._value = self._from + (self._to - self._from) * ease(elapsed / self.duration)
        return self._value
