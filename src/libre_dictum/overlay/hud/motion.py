from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from ..protocol import DwellRing

SIDES = ("left", "right")

DEFAULT_SIDE = "right"

DURATION_MS = 180.0

EPSILON = 0.5

FLASH_MS = 260.0

FADE_MS = 160.0

STALE_MS = 250.0

FLASH_SPREAD = 0.8


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


class _Phase(Enum):
    FILLING = auto()
    FLASHING = auto()
    FADING = auto()
    SPENT = auto()


@dataclass(frozen=True, slots=True)
class RingFrame:
    """One painted ring: where, how full, how opaque, and its radius as a multiple."""

    x: float
    y: float
    progress: float
    alpha: float
    spread: float = 1.0


class RingMotion:
    """The dwell ring between the frames that describe it: filling, flashing, fading."""

    def __init__(self) -> None:
        self._phase: _Phase | None = None
        self._ring: DwellRing | None = None
        self._since = 0.0

    @property
    def busy(self) -> bool:
        """Whether anything is drawn or about to change without a new frame."""
        return self._phase in (_Phase.FILLING, _Phase.FLASHING, _Phase.FADING)

    def show(self, ring: DwellRing | None, *, now: float) -> None:
        """Take the ring a frame described, or None once nothing is dwelling."""
        phase = self._phase
        if ring is None:
            if phase is _Phase.FILLING:
                self._enter(_Phase.FADING, now)
            elif phase is _Phase.SPENT:
                self._phase = None
            return
        if ring.clicked:
            if phase not in (_Phase.FLASHING, _Phase.SPENT):
                self._ring = ring
                self._enter(_Phase.FLASHING, now)
            return
        if phase is not _Phase.FILLING or ring != self._ring:
            self._ring = ring
            self._enter(_Phase.FILLING, now)

    def frame(self, now: float) -> RingFrame | None:
        """What to paint at now, in milliseconds on the clock show was given."""
        ring = self._ring
        if ring is None or self._phase in (None, _Phase.SPENT):
            return None
        elapsed = max(0.0, now - self._since)
        if self._phase is _Phase.FILLING:
            if elapsed <= STALE_MS:
                return RingFrame(ring.x, ring.y, ring.progress, 1.0)
            self._enter(_Phase.FADING, now)
            elapsed = 0.0
        if self._phase is _Phase.FADING:
            if elapsed >= FADE_MS:
                self._phase = None
                return None
            return RingFrame(ring.x, ring.y, ring.progress, 1.0 - elapsed / FADE_MS)
        if elapsed >= FLASH_MS:
            self._phase = _Phase.SPENT
            return None
        fraction = elapsed / FLASH_MS
        return RingFrame(ring.x, ring.y, 1.0, 1.0 - fraction, 1.0 + FLASH_SPREAD * ease(fraction))

    def _enter(self, phase: _Phase, now: float) -> None:
        self._phase = phase
        self._since = now
