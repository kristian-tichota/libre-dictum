from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum, auto


class Layer(StrEnum):
    """One independent input mechanism, and the mode pointer that belongs to it."""

    VOICE = "voice"
    GESTURE = "gesture"
    PEDAL = "pedal"


QUALIFIER = ":"

ALL_LAYERS: frozenset[Layer] = frozenset(Layer)

DEFAULT_CONFIRM_SECONDS = 5.0


def parse_target(argument: str) -> tuple[Layer | None, str]:
    """Split "pedal:scrolling" into the layer it names and the mode it names."""
    prefix, separator, rest = argument.partition(QUALIFIER)
    if not separator:
        return None, argument
    try:
        layer = Layer(prefix.strip().lower())
    except ValueError:
        return None, argument
    return layer, rest.strip()


def qualified(layer: Layer, mode: str) -> str:
    """ "pedal:scrolling" -- the spelling parse_target reads back."""
    return f"{layer.value}{QUALIFIER}{mode}"


@dataclass(frozen=True, slots=True)
class LayerState:
    """Which mode each layer is in; None where the layer is not running at all."""

    voice: str | None = None
    gesture: str | None = None
    pedal: str | None = None

    def of(self, layer: Layer) -> str | None:
        """The mode one layer is in."""
        return {Layer.VOICE: self.voice, Layer.GESTURE: self.gesture, Layer.PEDAL: self.pedal}[
            layer
        ]

    @property
    def diverged(self) -> tuple[tuple[Layer, str], ...]:
        """The layers that are somewhere other than the voice layer, in Layer order."""
        if self.voice is None:
            return ()
        return tuple(
            (layer, mode)
            for layer in (Layer.GESTURE, Layer.PEDAL)
            if (mode := self.of(layer)) is not None and mode != self.voice
        )

    def summary(self) -> str:
        """One line for a tooltip or a log, naming every layer that is running."""
        return ", ".join(
            f"{layer.value}: {mode}" for layer in Layer if (mode := self.of(layer)) is not None
        )


def parse_layers(argument: str) -> frozenset[Layer]:
    """ "gesture" or "gesture, pedal" as layers; empty argument means all of them."""
    names = [part.strip().lower() for part in argument.split(",") if part.strip()]
    if not names:
        return ALL_LAYERS
    layers = []
    for name in names:
        try:
            layers.append(Layer(name))
        except ValueError:
            allowed = ", ".join(layer.value for layer in Layer)
            raise ValueError(f"{name!r} is not an input layer; it is one of {allowed}") from None
    return frozenset(layers)


class Confirmation(StrEnum):
    """What a sleep or wake request did."""

    ARMED = auto()
    APPLIED = auto()
    IDLE = auto()
    REFUSED = auto()


@dataclass(frozen=True, slots=True)
class SleepView:
    """What a display is handed: which mechanisms are asleep, and what is being asked."""

    asleep: frozenset[Layer] = frozenset()
    owners: frozenset[Layer] = frozenset()
    prompt: str | None = None
    notice: str | None = None

    @property
    def dozing(self) -> bool:
        """Whether anything at all is asleep."""
        return bool(self.asleep)

    @property
    def total(self) -> bool:
        """Whether every mechanism is asleep."""
        return self.asleep == ALL_LAYERS

    @property
    def asking(self) -> bool:
        """Whether a repeat is being waited for, or a refusal is still on screen."""
        return self.prompt is not None or self.notice is not None

    def summary(self) -> str:
        """One line for a tooltip or a log."""
        if not self.asleep:
            return "awake"
        what = "asleep" if self.asleep == ALL_LAYERS else f"asleep: {_named(self.asleep)}"
        return f"{what} (wake with {_named(self.owners)})" if self.owners else what


class SleepSwitch:
    """Which mechanisms are asleep, and the repeat the next change is waiting for."""

    def __init__(self, confirm_seconds: float = DEFAULT_CONFIRM_SECONDS) -> None:
        self.confirm_seconds = confirm_seconds
        self._asleep: dict[Layer, Layer | None] = {}
        self._pending: tuple[frozenset[Layer], bool] | None = None
        self._deadline = 0.0
        self._notice: str | None = None
        self._notice_until = 0.0

    def asleep(self, layer: Layer) -> bool:
        """Whether one mechanism is asleep."""
        return layer in self._asleep

    @property
    def all_asleep(self) -> bool:
        """Whether every mechanism is asleep, which also parks the pointer."""
        return set(self._asleep) == ALL_LAYERS

    def request(
        self,
        layers: Iterable[Layer],
        *,
        asleep: bool,
        now: float,
        by: Layer | None = None,
    ) -> Confirmation:
        """Ask for layers to sleep or to wake."""
        wanted = frozenset(layers)
        if all((layer in self._asleep) is asleep for layer in wanted):
            return Confirmation.IDLE

        if not asleep and (refused := self._not_theirs(wanted, by)):
            self._notice = f"refused: only {_named(refused)} wakes this"
            self._notice_until = now + self.confirm_seconds
            return Confirmation.REFUSED

        asking = (wanted, asleep)
        if self._pending == asking and now <= self._deadline:
            self._pending = None
            self._notice = None
            if asleep:
                self._asleep.update(dict.fromkeys(wanted, by))
            else:
                for layer in wanted:
                    self._asleep.pop(layer, None)
            return Confirmation.APPLIED

        self._pending = asking
        self._deadline = now + self.confirm_seconds
        return Confirmation.ARMED

    def view(self, now: float) -> SleepView:
        """The picture for a display, with anything that has expired already forgotten."""
        if self._pending is not None and now > self._deadline:
            self._pending = None
        if self._notice is not None and now > self._notice_until:
            self._notice = None
        return SleepView(
            asleep=frozenset(self._asleep),
            owners=frozenset(owner for owner in self._asleep.values() if owner is not None),
            prompt=self._prompt(),
            notice=self._notice,
        )

    def wake_everything(self) -> None:
        """Wake every mechanism with no confirmation and no owner: what a reload does."""
        self._asleep.clear()
        self._pending = None
        self._notice = None

    def _not_theirs(self, wanted: frozenset[Layer], by: Layer | None) -> frozenset[Layer]:
        """The mechanisms owning a sleep by may not undo."""
        return frozenset(
            owner
            for layer in wanted
            if (owner := self._asleep.get(layer)) is not None and owner != by
        )

    def _prompt(self) -> str | None:
        """The pending request as a line to put on screen."""
        if self._pending is None:
            return None
        wanted, asleep = self._pending
        verb = "sleep" if asleep else "wake"
        what = "" if wanted == ALL_LAYERS else " " + _named(wanted)
        return f"{verb}{what}? again to confirm"


def _named(layers: Iterable[Layer]) -> str:
    """ "voice, pedal" -- layers in Layer order, for a line somebody reads."""
    wanted = frozenset(layers)
    return ", ".join(layer.value for layer in Layer if layer in wanted)
