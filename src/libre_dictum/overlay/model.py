from __future__ import annotations

import difflib
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum, auto

from ..errors import ConfigError
from ..health import Health
from ..input.dsl import VerbToken, apply_aliases, hud_group, validate_response
from ..layers import Layer, LayerState, SleepView, parse_target
from ..meters import GestureMeter, HandReading
from ..settings import (
    AppSettings,
    Command,
    GroupDisplay,
    ModeKind,
    ModeSettings,
    spoken_phrase,
)
from ..status import HeldInput
from ..text import normalize

logger = logging.getLogger(__name__)

DEFAULT_NAVIGATE = "open"

MISC = "misc"

MIN_TABLE_ROWS = 2

MIN_TABLE_COLUMNS = 2

MIN_ALIGNED_FILL = 0.75

MIN_RAGGED_ROWS = 3
MIN_RAGGED_SHARE = 0.6

MIN_MERGE_SIZE = 2

NEAREST_CUTOFF = 0.4

_PLAIN = GroupDisplay()


@dataclass(frozen=True, slots=True)
class Entry:
    """One command, as a display shows it."""

    phrase: str
    response: str
    tail: str = ""

    @property
    def first_word(self) -> str:
        return self.phrase.split(maxsplit=1)[0] if self.phrase else ""


@dataclass(frozen=True, slots=True)
class Table:
    """A group whose phrases factor into two axes."""

    row_labels: tuple[str, ...]
    cells: tuple[tuple[Entry | None, ...], ...]
    column_labels: tuple[str, ...] | None = None

    @property
    def aligned(self) -> bool:
        return self.column_labels is not None


@dataclass(frozen=True, slots=True)
class Group:
    """A named set of commands, and the phrase that opens it."""

    names: tuple[str, ...]
    entries: tuple[Entry, ...]
    source: str = ""
    table: Table | None = None
    phrases: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return "/".join(self.names)

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def navigable(self) -> bool:
        return bool(self.phrases)

    def answers_to(self, phrase: str) -> bool:
        """Whether phrase is one of this group's navigation phrases."""
        return phrase in self.phrases


class RouteKind(StrEnum):
    """How one mode leads to another."""

    NAME = auto()
    COMMAND = auto()
    GESTURE = auto()
    PEDAL = auto()


@dataclass(frozen=True, slots=True)
class Route:
    """One way to leave where the session is now."""

    target: str
    kind: RouteKind
    by: str
    layer: Layer | None = None


@dataclass(frozen=True, slots=True)
class EdgeAction:
    """What one mode does with one pedal, or with one gesture."""

    name: str
    press: str = ""
    release: str = ""


@dataclass(frozen=True, slots=True)
class ModeView:
    """Everything a display can say about one mode, worked out once at load."""

    mode: str
    groups: tuple[Group, ...] = ()
    routes: tuple[Route, ...] = ()
    pedals: tuple[EdgeAction, ...] = ()
    gestures: tuple[EdgeAction, ...] = ()
    previous_keyword: str | None = None
    runnable: bool = True
    navigable: bool = True

    @property
    def phrases(self) -> tuple[str, ...]:
        """Every generated navigation phrase, for the recognizer's closed grammar."""
        if not self.navigable:
            return ()
        return tuple(phrase for group in self.groups for phrase in group.phrases)

    def group_named(self, phrase: str) -> Group | None:
        """The group a navigation phrase opens, or None."""
        for group in self.groups:
            if group.answers_to(phrase):
                return group
        return None

    def group_called(self, name: str) -> Group | None:
        """The group carrying name, whether or not it is the primary one."""
        for group in self.groups:
            if name in group.names or name == group.name:
                return group
        return None


@dataclass(frozen=True, slots=True)
class Heard:
    """The last thing the recognizer produced, settled or still forming."""

    partial: str | None = None
    text: str | None = None
    matched: str | None = None
    nearest: str | None = None

    @property
    def missed(self) -> bool:
        """Whether the last settled utterance matched nothing."""
        return self.text is not None and self.matched is None


def spoken_group_name(name: str, override: str | None = None) -> str:
    """The phrase a group is opened by, or "" when it has none."""
    source = override if override is not None else name.removeprefix("::")
    words = normalize(source.replace("_", " ").replace("-", " "))
    phrase = spoken_phrase(words)
    if not phrase or not all(character.isalpha() or character == " " for character in phrase):
        return ""
    return phrase


def nearest_pattern(utterance: str, commands: Iterable[Command]) -> str | None:
    """The command pattern that came closest to utterance, if anything did."""
    templates = {command.pattern.normalized: command.pattern.template for command in commands}
    closest = difflib.get_close_matches(utterance, templates, n=1, cutoff=NEAREST_CUTOFF)
    return templates[closest[0]] if closest else None


def _entry(command: Command) -> Entry:
    """One command as a display shows it, split on its first word."""
    _, _, tail = command.pattern.normalized.partition(" ")
    return Entry(phrase=command.pattern.template, response=command.response, tail=tail)


def _first_word(entry: Entry) -> str:
    """The word a family is named after, taken from the normalized phrase."""
    normalized = normalize(entry.phrase).lower()
    head, _, _ = normalized.partition(" ")
    return head


def _by_first_word(entries: Sequence[Entry]) -> dict[str, list[Entry]]:
    """Entries bucketed by first word, in the order the words first appear."""
    buckets: dict[str, list[Entry]] = {}
    for entry in entries:
        buckets.setdefault(_first_word(entry), []).append(entry)
    return buckets


def _merge_families(buckets: dict[str, list[Entry]]) -> list[list[str]]:
    """First words grouped into the tables they belong to."""
    order = {word: index for index, word in enumerate(buckets)}
    candidates = sorted(buckets, key=lambda word: (-len(buckets[word]), order[word]))

    families: list[list[str]] = []
    columns: list[set[str]] = []
    for word in candidates:
        tails = {entry.tail for entry in buckets[word]}
        if len(buckets[word]) >= MIN_MERGE_SIZE:
            for family, known in zip(families, columns, strict=True):
                if len(buckets[family[0]]) >= MIN_MERGE_SIZE and tails <= known:
                    family.append(word)
                    break
            else:
                families.append([word])
                columns.append(set(tails))
            continue
        families.append([word])
        columns.append(set(tails))

    verified: list[list[str]] = []
    for family in families:
        if len(family) > 1 and _aligned_table({word: buckets[word] for word in family}) is None:
            verified.extend([word] for word in family)
        else:
            verified.append(family)
    return verified


def _aligned_table(buckets: dict[str, list[Entry]]) -> Table | None:
    """A table whose rows share their column labels, or None if they do not."""
    columns: list[str] = []
    for entries in buckets.values():
        for entry in entries:
            if entry.tail not in columns:
                columns.append(entry.tail)
    if len(columns) < MIN_TABLE_COLUMNS:
        return None

    cells = tuple(
        tuple(next((e for e in entries if e.tail == column), None) for column in columns)
        for entries in buckets.values()
    )
    filled = sum(cell is not None for row in cells for cell in row)
    if filled / (len(cells) * len(columns)) < MIN_ALIGNED_FILL:
        return None
    return Table(row_labels=tuple(buckets), cells=cells, column_labels=tuple(columns))


def _ragged_table(buckets: dict[str, list[Entry]]) -> Table | None:
    """A table whose rows share a first word each but no columns, or None."""
    if len(buckets) < MIN_RAGGED_ROWS:
        return None
    total = sum(len(entries) for entries in buckets.values())
    in_families = sum(len(entries) for entries in buckets.values() if len(entries) > 1)
    if not total or in_families / total < MIN_RAGGED_SHARE:
        return None
    return Table(
        row_labels=tuple(buckets),
        cells=tuple(tuple(entries) for entries in buckets.values()),
    )


def detect_table(entries: Sequence[Entry]) -> Table | None:
    """The two-dimensional shape entries fall into, or None to list them."""
    buckets = _by_first_word(entries)
    if len(buckets) < MIN_TABLE_ROWS:
        return None
    return _aligned_table(buckets) or _ragged_table(buckets)


def _group(
    names: Sequence[str],
    entries: Sequence[Entry],
    source: str,
    navigate: str,
    overrides: Mapping[str, GroupDisplay] | None = None,
) -> Group:
    """One group, with its table detected and its navigation phrases generated."""
    said = overrides or {}
    phrases = tuple(
        f"{navigate} {phrase}"
        for name in names
        if navigate
        and not said.get(name, _PLAIN).hide
        and (phrase := spoken_group_name(name, said.get(name, _PLAIN).say))
    )
    return Group(
        names=tuple(names),
        entries=tuple(entries),
        source=source,
        table=detect_table(entries),
        phrases=phrases,
    )


def group_commands(
    commands: Iterable[Command],
    *,
    mode_name: str,
    navigate: str = DEFAULT_NAVIGATE,
    overrides: Mapping[str, GroupDisplay] | None = None,
) -> tuple[Group, ...]:
    """A mode's commands, sorted into the groups a display shows."""
    imported: dict[str, list[Entry]] = {}
    sources: dict[str, str] = {}
    inline: list[Entry] = []
    inline_source = ""

    for command in commands:
        origin = command.origin
        entry = _entry(command)
        if origin is None or origin.group == mode_name:
            inline.append(entry)
            if origin is not None and not inline_source:
                inline_source = origin.source
            continue
        imported.setdefault(origin.group, []).append(entry)
        sources.setdefault(origin.group, origin.source)

    groups = [
        _group([name], entries, sources.get(name, ""), navigate, overrides)
        for name, entries in imported.items()
    ]

    buckets = _by_first_word(inline)
    families = _merge_families(buckets)
    singletons = [
        entry
        for family in families
        for word in family
        if len(family) == 1 and len(buckets[word]) == 1
        for entry in buckets[word]
    ]
    named = [
        _group(
            family,
            [e for word in family for e in buckets[word]],
            inline_source,
            navigate,
            overrides,
        )
        for family in families
        if len(family) > 1 or len(buckets[family[0]]) > 1
    ]
    named.sort(key=lambda group: (-group.count, group.name))
    groups.extend(named)

    if singletons:
        groups.append(_group([MISC], singletons, inline_source, navigate, overrides))

    return tuple(group for group in groups if not _hidden(group, overrides))


def _hidden(group: Group, overrides: Mapping[str, GroupDisplay] | None) -> bool:
    """Whether this group is gone entirely, rather than one of its names."""
    said = overrides or {}
    return bool(group.names) and all(said.get(name, _PLAIN).hide for name in group.names)


def build_routes(
    mode: ModeSettings,
    *,
    modes: Sequence[str],
) -> tuple[Route, ...]:
    """Every way out of mode, and what to say or do for each."""
    routes = [
        Route(target=name, kind=RouteKind.NAME, by=spoken_phrase(name))
        for name in modes
        if name != mode.name
    ]

    switching: list[tuple[RouteKind, str, str]] = [
        (RouteKind.COMMAND, command.pattern.template, command.response) for command in mode.commands
    ]
    switching.extend(
        (RouteKind.GESTURE, edge_label(name, edge), response)
        for name, binding in mode.gestures.items()
        for edge, response in binding.responses
    )
    switching.extend(
        (RouteKind.PEDAL, edge_label(name, edge), response)
        for name, binding in mode.pedals.items()
        for edge, response in binding.responses
    )
    for kind, label, response in switching:
        for layer, target in _mode_targets(response, mode.aliases):
            routes.append(Route(target=target, kind=kind, by=label, layer=layer))
    return tuple(routes)


def edge_label(name: str, edge: str) -> str:
    """How one edge is named on screen: "left", or "left released"."""
    return name if edge == "press" else f"{name} released"


def _mode_targets(response: str, aliases: dict[str, str]) -> list[tuple[Layer | None, str]]:
    """The layers and modes one response switches, in the order it switches them."""
    expanded = apply_aliases(response, aliases)
    if "mode(" not in expanded:
        return []
    try:
        tokens = validate_response(expanded)
    except Exception:  # noqa: BLE001 - the loader already rejected this; a display never raises
        return []
    return [
        parse_target(token.argument)
        for token in tokens
        if isinstance(token, VerbToken) and token.verb == "mode"
    ]


def build_view(
    mode: ModeSettings,
    *,
    modes: Sequence[str],
    previous_keyword: str | None = None,
    navigate: str | None = DEFAULT_NAVIGATE,
    overrides: Mapping[str, GroupDisplay] | None = None,
) -> ModeView:
    """Everything a display can say about mode, worked out once when the config loads."""
    navigable = navigate is not None and not any(
        command.pattern.has_rest for command in mode.commands
    )
    return ModeView(
        mode=mode.name,
        groups=group_commands(
            mode.commands,
            mode_name=mode.name,
            navigate=navigate if navigable and navigate else "",
            overrides=overrides,
        ),
        routes=build_routes(mode, modes=modes),
        pedals=tuple(
            EdgeAction(name=name, press=binding.press or "", release=binding.release or "")
            for name, binding in mode.pedals.items()
        ),
        gestures=tuple(
            EdgeAction(name=name, press=binding.press or "", release=binding.release or "")
            for name, binding in mode.gestures.items()
        ),
        previous_keyword=previous_keyword,
        navigable=navigable,
        runnable=mode.kind is not ModeKind.IMPORT,
    )


def colliding_phrases(view: ModeView, mode: ModeSettings, reserved: Iterable[str]) -> list[str]:
    """Generated navigation phrases that a mode already spends on something else."""
    taken = {command.pattern.normalized.lower() for command in mode.commands}
    taken.update(spoken_phrase(name) for name in reserved)
    return [phrase for phrase in view.phrases if phrase in taken]


def navigation_phrases(mode: ModeSettings, settings: AppSettings) -> tuple[str, ...]:
    """Every generated phrase one mode's recognizer has to know."""
    return build_view(
        mode,
        modes=(),
        navigate=settings.overlay.navigate,
        overrides=settings.overlay.groups,
    ).phrases


def check_display(settings: AppSettings) -> None:
    """Reject or report what only this side of the layering can see, at load time."""
    for name, mode in settings.modes.items():
        view = build_view(
            mode,
            modes=(),
            navigate=settings.overlay.navigate,
            overrides=settings.overlay.groups,
        )
        taken = colliding_phrases(view, mode, settings.reserved_phrases)
        if taken:
            raise ConfigError(
                f"Mode {name!r} would generate the navigation phrase(s) "
                f"{', '.join(repr(phrase) for phrase in taken)}, which the mode already "
                f"uses. Whichever is reached first wins and the other is dead config. "
                f"Change 'overlay.sheet.navigate', or give the group a different spoken "
                f"name with 'overlay.groups.<name>.say'."
            )
        _warn_about_unknown_groups(view, mode, name)


def _warn_about_unknown_groups(view: ModeView, mode: ModeSettings, name: str) -> None:
    known = {label for group in view.groups for label in (*group.names, group.name)}
    for where, response in mode.labelled_responses():
        for wanted in _hud_groups(response, mode.aliases):
            if wanted not in known:
                logger.warning(
                    "%s of mode %r opens the group %r, which mode %r has not got. The "
                    "sheet will show its index instead. Groups here: %s.",
                    where,
                    name,
                    wanted,
                    name,
                    ", ".join(sorted(known)) or "(none)",
                )


def _hud_groups(response: str, aliases: dict[str, str]) -> list[str]:
    """The groups one response asks a display to open; aliases applied first."""
    expanded = apply_aliases(response, aliases)
    if "hud(" not in expanded:
        return []
    try:
        tokens = validate_response(expanded)
    except Exception:  # noqa: BLE001 - the loader already rejected this; this never raises
        return []
    return [
        group
        for token in tokens
        if isinstance(token, VerbToken) and token.verb == "hud"
        if (group := hud_group(token.argument))
    ]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The whole picture, comparable as a whole."""

    layers: LayerState = field(default_factory=LayerState)
    sleep: SleepView = field(default_factory=SleepView)
    held: HeldInput = field(default_factory=HeldInput)
    heard: Heard = field(default_factory=Heard)
    open_group: str | None = None
    health: Health = field(default_factory=Health)
    held_gestures: tuple[str, ...] = ()
    meters: tuple[GestureMeter, ...] = ()
    hands: tuple[HandReading, ...] = ()


class Page(StrEnum):
    """Which of the sheet's pages is showing."""

    INDEX = auto()
    GROUP = auto()
    MODES = auto()
    PEDALS = auto()
    GESTURES = auto()


class Sheet:
    """Which page the sheet has open, and what a mode switch does to it."""

    def __init__(self, *, open_at_start: bool = False) -> None:
        self.visible = open_at_start
        self.page = Page.INDEX
        self.group: str | None = None

    def open(self, group: str | None = None) -> None:
        """Show the sheet, at group or at the index."""
        self.visible = True
        self.page = Page.GROUP if group else Page.INDEX
        self.group = group

    def open_pedals(self) -> None:
        """Show the sheet at the pedals page."""
        self.visible = True
        self.page = Page.PEDALS
        self.group = None

    def open_modes(self) -> None:
        """Show the sheet at the modes page."""
        self.visible = True
        self.page = Page.MODES
        self.group = None

    def open_gestures(self) -> None:
        """Show the sheet at the gestures page."""
        self.visible = True
        self.page = Page.GESTURES
        self.group = None

    def close(self) -> None:
        """Hide the sheet, forgetting where it was."""
        self.visible = False
        self.page = Page.INDEX
        self.group = None

    def toggle(self) -> None:
        """What a single configured command does."""
        if self.visible:
            self.close()
        else:
            self.open()

    def follow(self, view: ModeView) -> None:
        """Re-aim at a new mode, keeping the open group when it exists there."""
        if self.group is not None and view.group_called(self.group) is None:
            self.group = None
            if self.page is Page.GROUP:
                self.page = Page.INDEX
