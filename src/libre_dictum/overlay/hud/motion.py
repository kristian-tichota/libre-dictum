from __future__ import annotations

from collections.abc import Sequence

SIDES = ("left", "right")

DEFAULT_SIDE = "right"

DURATION_MS = 180.0

EPSILON = 0.5

TAPE_LAG_MS = 200.0

TAPE_SETTLE_MS = 1000.0


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


class Tape:
    """Bars scrolled at the pace they were heard, trailing the newest by TAPE_LAG_MS."""

    def __init__(self, width: int, *, glide: bool = True) -> None:
        self.width = width
        self.glide = glide
        self.clear()

    @property
    def head(self) -> float:
        """How many bars have scrolled in, the one entering counted by its fraction."""
        return self._head

    def clear(self) -> None:
        """Forget every bar."""
        self._bars: dict[int, tuple[float, bool]] = {}
        self._heard = 0
        self._period = 0.0
        self._fed: float | None = None
        self._last: float | None = None
        self._head = 0.0

    def feed(
        self, bars: Sequence[tuple[float, bool]], *, heard: int, period: float, now: float
    ) -> None:
        """Take the newest bars, the last of them bar number heard, each period ms long."""
        fresh = self._fed is None or heard < self._heard
        if fresh:
            self.clear()
        first = heard - len(bars)
        for offset, bar in enumerate(bars):
            self._bars[first + offset] = bar
        for index in [index for index in self._bars if index < heard - 2 * self.width]:
            del self._bars[index]
        self._heard, self._period, self._fed = heard, period, now
        if not self._gliding:
            self._head = float(heard)
        elif fresh:
            self._head = max(0.0, self._target(now))

    def advance(self, now: float) -> float:
        """Step to now, never past the newest bar and never backwards, and return the head."""
        if not self._gliding:
            return self._head
        step = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        target = self._target(now)
        if target - self._head > TAPE_LAG_MS / self._period:
            head = target
        else:
            pull = min(1.0, step / TAPE_SETTLE_MS)
            head = self._head + step / self._period + (target - self._head) * pull
        self._head = min(head, float(self._heard))
        return self._head

    def placed(self) -> tuple[tuple[float, float, bool], ...]:
        """Each bar in view: its centre's distance in bars from the right edge, level, loudness."""
        shown = []
        for index in sorted(self._bars):
            age = self._head - index - 0.5
            if -0.5 < age < self.width + 0.5:
                shown.append((age, *self._bars[index]))
        return tuple(shown)

    @property
    def _gliding(self) -> bool:
        return self.glide and self._period > 0.0

    def _target(self, now: float) -> float:
        """Where the newest bar is by now, less the lag."""
        elapsed = 0.0 if self._fed is None else max(0.0, now - self._fed)
        return self._heard + (elapsed - TAPE_LAG_MS) / self._period
