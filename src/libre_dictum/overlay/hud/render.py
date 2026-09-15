from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum, auto

from ...meters import GestureMeter, HandReading
from ..protocol import Catalogue, EdgeCard, GroupCard, KeyCard, ModeCard, RouteCard, State
from .style import TONE_BLOCKED, TONE_LIVE, TONE_MEASURED

ANCHORS = ("bottom-left", "bottom-right", "left", "right", "top-left", "top-right")

MISSING = "—"

PRESENT = "•"

WAITING = "connecting…"

HERE = "►"

SPOKEN = ("name", "command")

METHODS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("voice", SPOKEN),
    ("gesture", ("gesture",)),
    ("pedal", ("pedal",)),
)

UNSPOKEN = "anything you say"

TO_INDEX = "all groups"
TO_HIDE = "hide"
TO_MODES = "modes"
TO_PEDALS = "pedals"
TO_GESTURES = "gestures"

MATCHED = "✓"

HEARING = {None: "···", "": "~~", "words": "~~~~"}

MODES_TITLE = "modes"
MODES_HINT = "where you are, and what gets you elsewhere"

PEDALS_TITLE = "pedals"
PEDALS_HINT = "what each pedal does, and what moves them"
PEDAL_COLUMNS = ("press", "release")

GESTURES_TITLE = "gestures"
GESTURES_HINT = "what each gesture does, and what moves them"

NO_GESTURES = "no gestures defined (ht_custom_gestures)"

METER_COLUMN = "now"

BAR_WIDTH = 8

BAR_FULL = "▓"
BAR_EMPTY = "▁"

METER_HELD = "held"

METER_DWELL = "holding for {seconds:.2f}s"

RATIOS_TITLE = "hand ratios, to set ht_hand_calibration from"

NO_HANDS = "no hand in shot"

GESTURE_HELD = "gesture: {name} → {response}"
GESTURE_BARE = "gesture: {name}"
GESTURE_JOIN = " · "

NO_PEDALS = "no pedal device configured (enable_pedals)"

LAYER_ELSEWHERE = "{layer}: {mode}"
LAYER_JOIN = " · "

NO_NAVIGATION = "no voice navigation in this mode"

ASLEEP_ALL = "asleep"
ASLEEP_SOME = "asleep: {layers}"

WAKE_WITH = "wake with {layers}"

ASLEEP_DIM = 0.3


class Layout(StrEnum):
    """How the sheet's body is arranged."""

    INDEX = auto()
    TABLE = auto()
    LIST = auto()


@dataclass(frozen=True, slots=True)
class Item:
    """One thing in the sheet's body."""

    label: str
    detail: str = ""
    phrase: str = ""
    dim: bool = False
    tone: str = ""


@dataclass(frozen=True, slots=True)
class Row:
    """One row of a table."""

    label: str
    items: tuple[Item, ...]
    phrase: str = ""


@dataclass(frozen=True, slots=True)
class Panel:
    """The sheet's body, whichever of the three shapes it is in."""

    layout: Layout
    title: str
    hint: str = ""
    columns: tuple[str, ...] = ()
    items: tuple[Item, ...] = ()
    rows: tuple[Row, ...] = ()
    notes: tuple[str, ...] = ()
    footer: tuple[str, ...] = ()

    @property
    def width(self) -> int:
        """Cells in the widest row."""
        return max((len(row.items) for row in self.rows), default=0)


@dataclass(frozen=True, slots=True)
class Chip:
    """The ambient line: which mode, what is held, what was heard, what is broken."""

    mode: str = WAITING
    colour: tuple[int, int, int] | None = None
    layers: tuple[tuple[str, str], ...] = ()
    held: tuple[KeyCard, ...] = ()
    gestures: tuple[tuple[str, str], ...] = ()
    saved: str | None = None
    utterance: str = ""
    matched: bool = False
    missed: bool = False
    partial: str | None = None
    fault: str | None = None
    note: str | None = None
    asleep: tuple[str, ...] = ()
    wake_with: tuple[str, ...] = ()
    confirming: str | None = None
    notice: str | None = None

    @property
    def glyphs(self) -> str:
        """The held keys as one string of symbols, in MODIFIER_ORDER."""
        return " ".join(key.symbol for key in self.held)

    @property
    def hearing(self) -> bool:
        """Whether the recognizer is taking something in right now."""
        return self.partial is not None

    @property
    def layer_line(self) -> str:
        """Where the other layers are, or empty when all match voice."""
        return LAYER_JOIN.join(
            LAYER_ELSEWHERE.format(layer=layer, mode=mode) for layer, mode in self.layers
        )

    @property
    def gesture_line(self) -> str:
        """What a shape rather than a keyboard is holding."""
        return GESTURE_JOIN.join(
            (GESTURE_HELD if response else GESTURE_BARE).format(name=name, response=response)
            for name, response in self.gestures
        )

    @property
    def swatch(self) -> tuple[int, int, int] | None:
        """The mode's colour, dimmed while anything is asleep."""
        if self.colour is None or not self.asleep:
            return self.colour
        return tuple(round(channel * ASLEEP_DIM) for channel in self.colour)  # type: ignore[return-value]

    @property
    def sleep_line(self) -> str:
        """What is asleep, who may wake it, and what is being asked."""
        parts = []
        if self.asleep:
            parts.append(
                ASLEEP_ALL
                if len(self.asleep) == len(METHODS)
                else ASLEEP_SOME.format(layers=", ".join(self.asleep))
            )
        if self.notice is not None:
            parts.append(self.notice)
        elif self.wake_with:
            parts.append(WAKE_WITH.format(layers=", ".join(self.wake_with)))
        if self.confirming is not None:
            parts.append(self.confirming)
        return LAYER_JOIN.join(parts)

    @property
    def mark(self) -> str:
        """The hearing indicator."""
        if self.partial is None:
            return HEARING[None]
        return HEARING["words"] if self.partial else HEARING[""]

    def tooltip(self) -> str:
        """Every held key and the saved value, in words."""
        parts = [key.describe() for key in self.held]
        if self.saved is not None:
            parts.append(f"saved {self.saved!r}")
        if self.fault is not None:
            parts.append(self.fault)
        return ", ".join(parts)


def chip_of(state: State | None, catalogue: Catalogue | None = None) -> Chip:
    """The chip for one state frame, or the waiting chip before any has arrived."""
    if state is None:
        return Chip()

    mode = catalogue.mode(state.mode) if catalogue is not None else None
    return Chip(
        mode=state.mode or WAITING,
        colour=mode.colour if mode is not None else None,
        layers=_elsewhere(state),
        held=state.held,
        gestures=_held_gestures(state, catalogue),
        saved=state.saved,
        utterance=_utterance(state),
        matched=state.matched is not None,
        missed=state.missed,
        partial=state.partial,
        fault=state.fault,
        note=None if mode is None or mode.navigable else NO_NAVIGATION,
        asleep=state.asleep,
        wake_with=state.wake_with,
        confirming=state.confirming,
        notice=state.notice,
    )


def _held_gestures(state: State, catalogue: Catalogue | None) -> tuple[tuple[str, str], ...]:
    """The held gestures paired with what each one does, off the *gesture* layer's mode."""
    mode = catalogue.mode(state.gesture_mode or state.mode) if catalogue is not None else None
    pairs = []
    for name in state.held_gestures:
        binding = mode.gesture(name) if mode is not None else None
        pairs.append((name, binding.press if binding is not None else ""))
    return tuple(pairs)


def _elsewhere(state: State) -> tuple[tuple[str, str], ...]:
    """Layers away from the voice layer, in column order."""
    if state.mode is None:
        return ()
    return tuple(
        (layer, mode)
        for layer, mode in (("gesture", state.gesture_mode), ("pedal", state.pedal_mode))
        if mode is not None and mode != state.mode
    )


def _utterance(state: State) -> str:
    """The last-utterance line, with the near miss folded into it."""
    if state.heard is None:
        return ""
    said = f'"{state.heard}"'
    if state.matched is not None:
        pattern = "" if state.matched == state.heard else f" → {state.matched}"
        return f"{said}{pattern} {MATCHED}"
    if state.nearest is not None:
        return f"{said} → {state.nearest}?"
    return said


def panel_of(state: State | None, catalogue: Catalogue | None) -> Panel | None:
    """What the sheet draws, or None when it is closed or has nothing yet."""
    if state is None or catalogue is None or not state.sheet:
        return None

    layers = _layer_cards(state, catalogue)
    mode = layers["voice"]
    if mode is None:
        return None

    to_pedals = catalogue.sheet_pedals if catalogue.pedals else ""
    to_gestures = catalogue.sheet_gestures if catalogue.gestures else ""

    if state.page == "modes":
        return modes_panel(
            catalogue,
            layers,
            back=catalogue.sheet_open,
            close=catalogue.sheet_close,
            pedals=to_pedals,
            gestures=to_gestures,
        )

    if state.page == "pedals":
        return pedals_panel(
            catalogue,
            layers,
            back=catalogue.sheet_open,
            close=catalogue.sheet_close,
            modes=catalogue.sheet_modes,
            gestures=to_gestures,
        )

    if state.page == "gestures":
        return gestures_panel(
            catalogue,
            layers,
            back=catalogue.sheet_open,
            close=catalogue.sheet_close,
            modes=catalogue.sheet_modes,
            pedals=to_pedals,
            meters=state.meters,
            hands=state.hands,
        )

    if state.group is not None:
        group = mode.group(state.group)
        if group is not None:
            return group_panel(
                group,
                mode,
                back=catalogue.sheet_open,
                close=catalogue.sheet_close,
                modes=catalogue.sheet_modes,
                pedals=to_pedals,
                gestures=to_gestures,
            )
    return index_panel(
        mode,
        navigate=catalogue.navigate,
        close=catalogue.sheet_close,
        modes=catalogue.sheet_modes,
        pedals=to_pedals,
        gestures=to_gestures,
    )


def _layer_cards(state: State, catalogue: Catalogue) -> dict[str, ModeCard | None]:
    """The mode card each layer is on, keyed by the method name its column uses."""
    return {
        "voice": catalogue.mode(state.mode),
        "gesture": catalogue.mode(state.gesture_mode or state.mode),
        "pedal": catalogue.mode(state.pedal_mode or state.mode),
    }


def index_panel(
    mode: ModeCard,
    *,
    navigate: str = "",
    close: str = "",
    modes: str = "",
    pedals: str = "",
    gestures: str = "",
) -> Panel:
    """The sheet's front page: every group, its size, and the phrase that opens it."""
    return Panel(
        layout=Layout.INDEX,
        title=mode.name,
        hint=_index_hint(mode, navigate),
        items=tuple(
            Item(
                label=_spoken(group.name),
                detail=str(group.count),
                phrase=_quote(group.phrases),
                dim=not group.navigable,
            )
            for group in mode.groups
        ),
        footer=_footer(mode, close=close, modes=modes, pedals=pedals, gestures=gestures),
    )


def _index_hint(mode: ModeCard, navigate: str) -> str:
    if not mode.navigable:
        return NO_NAVIGATION
    return f'say "{navigate}" + a name' if navigate else ""


def group_panel(
    group: GroupCard,
    mode: ModeCard,
    *,
    back: str = "",
    close: str = "",
    modes: str = "",
    pedals: str = "",
    gestures: str = "",
) -> Panel:
    """One group: a table where the model found two axes, a list otherwise."""
    title = _spoken(group.name)
    hint = _quote(group.phrases) or NO_NAVIGATION
    footer = _footer(mode, back=back, close=close, modes=modes, pedals=pedals, gestures=gestures)

    if group.table is None:
        return Panel(
            layout=Layout.LIST,
            title=title,
            hint=hint,
            items=tuple(Item(label=entry.phrase, detail=entry.response) for entry in group.entries),
            footer=footer,
        )

    table = group.table
    rows = []
    for label, cells in zip(table.row_labels, table.rows, strict=True):
        rows.append(
            Row(
                label=label,
                items=tuple(_cell(group, index, aligned=table.aligned) for index in cells),
                phrase="" if table.aligned else f'"{label} _"',
            )
        )
    return Panel(
        layout=Layout.TABLE,
        title=title,
        hint=hint,
        columns=table.column_labels or (),
        rows=tuple(rows),
        footer=footer,
    )


def modes_panel(
    catalogue: Catalogue,
    layers: Mapping[str, ModeCard | None],
    *,
    back: str = "",
    close: str = "",
    pedals: str = "",
    gestures: str = "",
) -> Panel:
    """Where the session is, and what reaches every other state from here."""
    routes: dict[str, dict[str, RouteCard]] = {}
    for method, kinds in METHODS:
        card = layers.get(method)
        for route in card.routes if card is not None else ():
            if route.kind in kinds and route.layer in ("", "voice"):
                routes.setdefault(method, {}).setdefault(route.target, route)

    targets = [card.name for card in catalogue.modes if card.runnable]
    targets += [
        target for reached in routes.values() for target in reached if target not in targets
    ]

    columns = tuple(method for method, _ in METHODS if routes.get(method))
    voice = layers.get("voice")
    here = voice.name if voice is not None else None
    return Panel(
        layout=Layout.TABLE,
        title=MODES_TITLE,
        hint=MODES_HINT if len(targets) > 1 else "",
        columns=columns,
        rows=tuple(
            Row(
                label=f"{HERE} {_spoken(name)}" if name == here else _spoken(name),
                items=tuple(
                    _transition(routes.get(method, {}).get(name), here=name == here)
                    for method in columns
                ),
                phrase="",
            )
            for name in targets
        ),
        footer=(
            _footer(voice, back=back, close=close, pedals=pedals, gestures=gestures)
            if voice is not None
            else ()
        ),
    )


def pedals_panel(
    catalogue: Catalogue,
    layers: Mapping[str, ModeCard | None],
    *,
    back: str = "",
    close: str = "",
    modes: str = "",
    gestures: str = "",
) -> Panel:
    """What each pedal does right now, and what moves the pedal layer."""
    mode = layers.get("pedal")
    rows = tuple(
        Row(
            label=name,
            items=tuple(
                _edge_cell(mode.pedal(name) if mode is not None else None, edge)
                for edge in PEDAL_COLUMNS
            ),
            phrase="",
        )
        for name in catalogue.pedals
    )
    title = PEDALS_TITLE if mode is None else f"{PEDALS_TITLE} · {_spoken(mode.name)}"
    return Panel(
        layout=Layout.TABLE,
        title=title,
        hint=PEDALS_HINT if rows else NO_PEDALS,
        columns=PEDAL_COLUMNS if rows else (),
        rows=rows,
        footer=_layer_footer(
            layers,
            layer="pedal",
            back=back,
            close=close,
            modes=modes,
            other=(gestures, TO_GESTURES),
        ),
    )


def gestures_panel(
    catalogue: Catalogue,
    layers: Mapping[str, ModeCard | None],
    *,
    back: str = "",
    close: str = "",
    modes: str = "",
    pedals: str = "",
    meters: Sequence[GestureMeter] = (),
    hands: Sequence[HandReading] = (),
) -> Panel:
    """What each gesture does, what moves the layer, and why one is quiet."""
    mode = layers.get("gesture")
    live = {meter.name: meter for meter in meters}
    columns = (*PEDAL_COLUMNS, METER_COLUMN) if live else PEDAL_COLUMNS
    rows = tuple(
        Row(
            label=name,
            items=(
                *(
                    _edge_cell(mode.gesture(name) if mode is not None else None, edge)
                    for edge in PEDAL_COLUMNS
                ),
                *((_meter_cell(live[name]),) if name in live else ()),
            ),
            phrase="",
        )
        for name in catalogue.gestures
    )
    title = GESTURES_TITLE if mode is None else f"{GESTURES_TITLE} · {_spoken(mode.name)}"
    return Panel(
        layout=Layout.TABLE,
        title=title,
        hint=GESTURES_HINT if rows else NO_GESTURES,
        columns=columns if rows else (),
        rows=rows,
        notes=_ratio_notes(hands) if live else (),
        footer=_layer_footer(
            layers,
            layer="gesture",
            back=back,
            close=close,
            modes=modes,
            other=(pedals, TO_PEDALS),
        ),
    )


def bar(value: float, width: int = BAR_WIDTH) -> str:
    """value in [0, 1] as a bar of blocks, always width cells wide."""
    filled = round(min(max(value, 0.0), 1.0) * width)
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def _meter_cell(meter: GestureMeter) -> Item:
    """One gesture's live reading: how close it is, and the one thing keeping it shut."""
    if meter.active:
        return Item(label=f"{bar(1.0)} {METER_HELD}", tone=TONE_LIVE)
    blocking = meter.blocking
    if blocking is None:
        return Item(label=f"{bar(1.0)} {METER_DWELL.format(seconds=meter.hold)}", tone=TONE_LIVE)
    return Item(
        label=f"{bar(meter.value)} {blocking.describe()}",
        tone=TONE_MEASURED if meter.waiting else TONE_BLOCKED,
    )


def _ratio_notes(hands: Sequence[HandReading]) -> tuple[str, ...]:
    """The raw hand measurements, as the lines drawn under the table."""
    if not hands:
        return (RATIOS_TITLE, NO_HANDS)
    lines = [RATIOS_TITLE]
    for hand in hands:
        lines.append(f"{hand.label}   span {hand.span:.3f}")
        lines.append(
            "  extension  "
            + "  ".join(f"{finger} {value:.2f}" for finger, value in hand.extensions())
        )
        lines.append(
            f"  pinch  index {hand.pinch_index:.2f}  middle {hand.pinch_middle:.2f}"
            f"    spread {hand.spread:.2f}    palmArea {hand.palm_area:.2f}"
        )
    return tuple(lines)


def _edge_cell(binding: EdgeCard | None, edge: str) -> Item:
    """One pedal's or one gesture's response on one edge, or a drawn absence."""
    response = "" if binding is None else (binding.press if edge == "press" else binding.release)
    return Item(label=response) if response else Item(label=MISSING, dim=True)


def _layer_footer(
    layers: Mapping[str, ModeCard | None],
    *,
    layer: str,
    back: str = "",
    close: str = "",
    modes: str = "",
    other: tuple[str, str] = ("", ""),
) -> tuple[str, ...]:
    """The ways off this page, then everything that moves layer's pointer."""
    routes = [
        f'"{phrase}" → {target}'
        for phrase, target in ((back, TO_INDEX), (modes, TO_MODES), other, (close, TO_HIDE))
        if phrase
    ]
    routes.extend(
        f"{_by(route)} → {_spoken(route.target)}"
        for card in layers.values()
        if card is not None
        for route in card.routes
        if route.layer == layer
    )
    return tuple(dict.fromkeys(routes))


def _transition(route: RouteCard | None, *, here: bool) -> Item:
    """One cell: how to get there, a drawn absence, or nothing at all."""
    if here:
        return Item(label="")
    if route is None:
        return Item(label=MISSING, dim=True)
    return Item(label=_by(route))


def _by(route: RouteCard) -> str:
    """A transition as the reader performs it."""
    if "{" in route.by:
        return UNSPOKEN
    return f'"{route.by}"' if route.kind in SPOKEN else route.by


def _cell(group: GroupCard, index: int | None, *, aligned: bool) -> Item:
    entry = group.cell(index)
    if entry is None:
        return Item(label=MISSING, dim=True)
    if aligned:
        return Item(label=PRESENT, detail=entry.response, phrase=f'"{entry.phrase}"')
    return Item(label=entry.label, detail=entry.response)


def _footer(
    mode: ModeCard,
    *,
    back: str = "",
    close: str = "",
    modes: str = "",
    pedals: str = "",
    gestures: str = "",
) -> tuple[str, ...]:
    """The ways off this page, then the ways out of this mode."""
    routes = [
        f'"{phrase}" → {target}'
        for phrase, target in (
            (back, TO_INDEX),
            (modes, TO_MODES),
            (pedals, TO_PEDALS),
            (gestures, TO_GESTURES),
            (close, TO_HIDE),
        )
        if phrase
    ]
    routes.extend(
        f"{_by(route)} → {_spoken(route.target)}" for route in mode.routes if route.kind != "name"
    )
    return tuple(dict.fromkeys(routes))


def _spoken(name: str) -> str:
    """A group's name as a reader sees it: "::control keys" is "control keys"."""
    return name.removeprefix("::")


def _quote(phrases: tuple[str, ...]) -> str:
    """The phrases that open something, quoted and joined; "" when there are none."""
    return " / ".join(f'"{phrase}"' for phrase in phrases)


@dataclass(frozen=True, slots=True)
class Target:
    """Where the calibration dot goes and what it says, as a display needs it."""

    x: float
    y: float
    settled: float
    caption: str
    hint: str


def target_of(state: State | None) -> Target | None:
    """The dot to draw, or None when no calibration is running."""
    if state is None or state.dot is None:
        return None
    dot = state.dot
    return Target(
        x=dot.x,
        y=dot.y,
        settled=max(0.0, min(1.0, dot.settled)),
        caption=f"{dot.index} of {dot.total}",
        hint="point your nose at the dot and hold still",
    )
