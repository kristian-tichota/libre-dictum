from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Protocol

from .layers import SleepView

MODIFIER_ORDER = ("ctrl", "shift", "alt", "meta")

ALIASES = {"win": "meta"}

SYMBOLS = {
    "ctrl": "⌃",
    "shift": "⇧",
    "alt": "⌥",
    "meta": "◆",
    "left_mouse": "◧",
    "right_mouse": "◨",
}

OTHER_SYMBOL = "▫"


def canonical(key: str) -> str:
    """The name a key is displayed under."""
    return ALIASES.get(key, key)


class Grip(StrEnum):
    """How a key came to be down, which is also what will release it."""

    HELD = auto()
    PENDING = auto()


@dataclass(frozen=True, slots=True)
class HeldKey:
    """One key that is down, and why."""

    key: str
    grip: Grip

    @property
    def symbol(self) -> str:
        return SYMBOLS.get(canonical(self.key), OTHER_SYMBOL)

    @property
    def is_modifier(self) -> bool:
        """Whether this is a modifier, as opposed to a key hold() pinned down."""
        return canonical(self.key) in MODIFIER_ORDER

    def describe(self) -> str:
        """ "shift held" -- for a tooltip, where a glyph alone is a guess."""
        return f"{canonical(self.key)} {self.grip.value}"


@dataclass(frozen=True, slots=True)
class HeldInput:
    """Everything that outlives the response that created it."""

    held: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    saved: str | None = None

    @property
    def empty(self) -> bool:
        """Whether there is nothing to show."""
        return not self.held and not self.pending and self.saved is None

    def pressed(self) -> tuple[HeldKey, ...]:
        """Every key that is down: modifiers first, in MODIFIER_ORDER."""
        seen: dict[str, HeldKey] = {}
        for key in self.held:
            seen.setdefault(canonical(key), HeldKey(key, Grip.HELD))
        for key in self.pending:
            seen.setdefault(canonical(key), HeldKey(key, Grip.PENDING))
        return tuple(sorted(seen.values(), key=_rank))

    def summary(self) -> str:
        """One line for a tooltip or a log; empty when nothing is down."""
        parts = [f"{key.symbol} {key.describe()}" for key in self.pressed()]
        if self.saved is not None:
            parts.append(f"saved {self.saved!r}")
        return ", ".join(parts)


class HeldIndicator(Protocol):
    """The part of a status display that shows what is still down."""

    def set_held(self, held: HeldInput) -> None: ...


class SleepIndicator(Protocol):
    """The part of a status display that shows what is beyond reach."""

    def set_sleep(self, view: SleepView) -> None: ...


def _rank(entry: HeldKey) -> tuple[int, int, str]:
    """Sort key: modifiers in their fixed order, then anything else by name."""
    name = canonical(entry.key)
    if name in MODIFIER_ORDER:
        return (0, MODIFIER_ORDER.index(name), name)
    return (1, 0, name)
