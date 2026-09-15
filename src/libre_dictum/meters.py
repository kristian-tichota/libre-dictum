from __future__ import annotations

from dataclasses import dataclass

ABSENT = "--"


@dataclass(frozen=True, slots=True)
class Reading:
    """One condition's live value against the window it has to be inside."""

    feature: str
    score: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    @property
    def waiting(self) -> bool:
        """Whether the sensor said nothing about this feature at all."""
        return self.score is None

    @property
    def holds(self) -> bool:
        """Whether the score is inside the window."""
        if self.score is None:
            return False
        above = self.minimum is None or self.score >= self.minimum
        below = self.maximum is None or self.score <= self.maximum
        return above and below

    @property
    def closeness(self) -> float:
        """How near this one condition is to being met: 1.0 when it is."""
        if self.score is None:
            return 0.0
        parts = []
        if self.minimum is not None:
            parts.append(self.score / self.minimum if self.minimum > 0.0 else 1.0)
        if self.maximum is not None:
            room = 1.0 - self.maximum
            over = self.score - self.maximum
            parts.append(1.0 - (over / room if room > 0.0 else 1.0) if over > 0.0 else 1.0)
        return _unit(min(parts)) if parts else 1.0

    @property
    def requirement(self) -> str:
        """ ">0.80", "<0.40", or both -- what it would take to hold."""
        parts = []
        if self.minimum is not None:
            parts.append(f">{self.minimum:.2f}")
        if self.maximum is not None:
            parts.append(f"<{self.maximum:.2f}")
        return " ".join(parts)

    def describe(self) -> str:
        """One line naming the feature, its reading and its requirement."""
        reading = ABSENT if self.score is None else f"{self.score:.2f}"
        return f"{self.feature} {reading} {self.requirement}".rstrip()


@dataclass(frozen=True, slots=True)
class GestureMeter:
    """One gesture's live reading: how close it is, and what is keeping it shut."""

    name: str
    readings: tuple[Reading, ...] = ()
    active: bool = False
    hold: float = 0.0

    @property
    def waiting(self) -> bool:
        """Whether any feature went unreported."""
        return any(reading.waiting for reading in self.readings)

    @property
    def value(self) -> float:
        """How close the gesture as a whole is: the least satisfied of its conditions."""
        return min((reading.closeness for reading in self.readings), default=0.0)

    @property
    def holds(self) -> bool:
        """Whether every condition is inside its window this frame."""
        return bool(self.readings) and all(reading.holds for reading in self.readings)

    @property
    def blocking(self) -> Reading | None:
        """The condition to fix first: the least satisfied one that does not hold."""
        unmet = [reading for reading in self.readings if not reading.holds]
        return min(unmet, key=lambda reading: reading.closeness) if unmet else None


FINGERS = ("Thumb", "Index", "Middle", "Ring", "Little")


@dataclass(frozen=True, slots=True)
class HandReading:
    """One hand's raw measurements."""

    side: str = ""
    primary: bool = False
    span: float = 0.0
    extension: tuple[float, ...] = ()
    pinch_index: float = 0.0
    pinch_middle: float = 0.0
    spread: float = 0.0
    palm_area: float = 0.0

    @property
    def label(self) -> str:
        """ "right hand (primary)" -- which hand these numbers came off."""
        name = f"{self.side or 'unknown'} hand"
        return f"{name} (primary)" if self.primary else name

    def extensions(self) -> tuple[tuple[str, float], ...]:
        """The extensions paired with their finger names, ready to draw."""
        return tuple(zip(FINGERS, self.extension, strict=False))


def _unit(value: float) -> float:
    """Clamp to [0, 1], which a closeness promises."""
    return min(max(value, 0.0), 1.0) + 0.0
