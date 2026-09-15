from __future__ import annotations

import json
import statistics
import textwrap
from collections import deque
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .gestures import HOLD_KEY, GestureDefinition, GestureRecognizer
from .hands import (
    CALIBRATION,
    CALIBRATION_KEYS,
    FINGER_CHAINS,
    HAND_FEATURES,
    SHAPE_FEATURES,
    Calibration,
    PrimaryHand,
    Ratios,
    ratios,
    scores,
    side_feature,
)
from .takes import NEUTRAL, Frame

WINDOW = 0.2

LONG_WINDOW = 0.4

_MAX_GAP = 0.35

_EDGE = 0.02

MATRIX_FEATURES: tuple[str, ...] = (
    *(side_feature(side, name) for side in ("left", "right") for name in SHAPE_FEATURES),
    "bothHandsPresent",
)

_CELL = 5

_PROSE = 92

_USEFUL = 0.02

_FLAT = 0.10


Sample = tuple[float, float]


def held_above(samples: Sequence[Sample], window: float) -> float:
    """The highest level the series stayed at or above for window seconds."""
    if not samples:
        return 0.0
    if window <= 0.0:
        return max(value for _, value in samples)

    minima: deque[int] = deque()
    best: float | None = None
    start = 0
    for end, (at, value) in enumerate(samples):
        while minima and samples[minima[-1]][1] >= value:
            minima.pop()
        minima.append(end)
        while start < end and at - samples[start + 1][0] >= window:
            if minima[0] == start:
                minima.popleft()
            start += 1
        if at - samples[start][0] >= window:
            level = samples[minima[0]][1]
            best = level if best is None else max(best, level)
    return 0.0 if best is None else best


def held_below(samples: Sequence[Sample], window: float) -> float:
    """The lowest level the series stayed at or below for window seconds."""
    if not samples:
        return 0.0
    return -held_above([(at, -value) for at, value in samples], window)


def runs(frames: Sequence[Frame]) -> tuple[tuple[Frame, ...], ...]:
    """Split frames wherever the recording is not continuous."""
    grouped: list[list[Frame]] = []
    for frame in frames:
        if not grouped or frame.resumed or frame.at - grouped[-1][-1].at > _MAX_GAP:
            grouped.append([frame])
        else:
            grouped[-1].append(frame)
    return tuple(tuple(group) for group in grouped)


@dataclass(frozen=True, slots=True)
class Scored:
    """One recorded frame with the scores it would have produced in a session."""

    at: float
    label: str
    take: int
    resumed: bool
    scores: Mapping[str, float]
    measurements: tuple[tuple[str, Ratios], ...] = ()
    clipped: bool = False


def score(frames: Iterable[Frame], *, calibration: Calibration = CALIBRATION) -> list[Scored]:
    """Re-derive every frame's scores under one calibration."""
    primary = PrimaryHand()
    scored = []
    for frame in frames:
        if frame.resumed:
            primary.reset()
        ordered = primary.order(frame.hands)
        measurements = []
        for hand in ordered:
            raw = ratios(hand)
            if raw is not None:
                measurements.append((hand.known_side, raw))
        scored.append(
            Scored(
                at=frame.at,
                label=frame.label,
                take=frame.take,
                resumed=frame.resumed,
                scores=scores(ordered, calibration=calibration),
                measurements=tuple(measurements),
                clipped=any(_clipped(hand.landmarks) for hand in ordered),
            )
        )
    return scored


def _clipped(landmarks: Sequence[tuple[float, float, float]]) -> bool:
    """Whether any landmark is within _EDGE of the frame border."""
    return any(not (_EDGE < axis < 1.0 - _EDGE) for point in landmarks for axis in point[:2])


@dataclass(frozen=True, slots=True)
class Cell:
    """One feature during one label: the per-take figures, and the whole label."""

    highs: Mapping[float, tuple[float, ...]]
    lows: Mapping[float, tuple[float, ...]]
    peak: float
    trough: float

    def at(self, window: float) -> tuple[float, ...]:
        """Per take, the level held for that dwell."""
        return self.highs.get(window, ())

    def low_at(self, window: float) -> tuple[float, ...]:
        return self.lows.get(window, ())

    def worst_high(self, window: float) -> float:
        """The take that reached least."""
        return min(self.at(window), default=0.0)

    def worst_low(self, window: float) -> float:
        """The take that stayed least low."""
        return max(self.low_at(window), default=0.0)


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """What a label's takes looked like, apart from their scores."""

    takes: int
    frames: int
    shortest: float
    longest: float
    median_seconds: float
    two_handed: int
    min_span: float
    clipped: int


@dataclass(frozen=True, slots=True)
class Summary:
    """Everything the report is rendered from."""

    labels: tuple[str, ...]
    features: tuple[str, ...]
    cells: Mapping[tuple[str, str], Cell]
    diagnostics: Mapping[str, Diagnostics]
    measurements: Mapping[str, Mapping[str, tuple[float, ...]]]
    windows: tuple[float, ...]

    @property
    def window(self) -> float:
        """The reference dwell: what a cell means when no gesture has said otherwise."""
        return self.windows[0]

    @property
    def long_window(self) -> float:
        """The longest dwell measured."""
        return self.windows[-1]

    def cell(self, label: str, feature: str) -> Cell:
        return self.cells.get((label, feature), Cell({}, {}, 0.0, 0.0))


def summarise(
    scored: Sequence[Scored],
    *,
    windows: Sequence[float] = (WINDOW, LONG_WINDOW),
    features: Sequence[str] = MATRIX_FEATURES,
) -> Summary:
    """Reduce a scored recording to per-label, per-feature statistics."""
    ordered = tuple(sorted(set(windows))) or (WINDOW,)
    labels = _ordered_labels(scored)
    by_label = {label: [frame for frame in scored if frame.label == label] for label in labels}
    takes = {label: _takes(by_label[label]) for label in labels}

    cells: dict[tuple[str, str], Cell] = {}
    for label in labels:
        stretches = runs(_frames(by_label[label]))
        for feature in features:
            per_take = [_series(take, feature) for take in takes[label]]
            over_label = [_series_of(by_label[label], stretch, feature) for stretch in stretches]
            cells[(label, feature)] = Cell(
                highs={w: tuple(held_above(s, w) for s in per_take) for w in ordered},
                lows={w: tuple(held_below(s, w) for s in per_take) for w in ordered},
                peak=max((held_above(p, ordered[0]) for p in over_label), default=0.0),
                trough=min((held_below(p, ordered[0]) for p in over_label), default=0.0),
            )

    return Summary(
        labels=labels,
        features=tuple(features),
        cells=cells,
        diagnostics={label: _diagnostics(takes[label]) for label in labels},
        measurements={label: _measurements(label, takes[label]) for label in labels},
        windows=ordered,
    )


def windows_for(definitions: Mapping[str, GestureDefinition]) -> tuple[float, ...]:
    """The dwells to measure: the two references, plus every gesture's own hold."""
    holds = {
        float(definition.get(HOLD_KEY, 0)) / 1000.0
        for definition in definitions.values()
        if float(definition.get(HOLD_KEY, 0)) > 0.0
    }
    return tuple(sorted({WINDOW, LONG_WINDOW} | holds))


def _ordered_labels(scored: Sequence[Scored]) -> tuple[str, ...]:
    """Every label that has a take, first-seen order, with the neutral pass last."""
    seen = [frame.label for frame in scored if frame.label and frame.take]
    ordered = list(dict.fromkeys(seen))
    if NEUTRAL in ordered:
        ordered.append(ordered.pop(ordered.index(NEUTRAL)))
    return tuple(ordered)


def _takes(frames: Sequence[Scored]) -> tuple[tuple[Scored, ...], ...]:
    """One label's frames grouped by take number, in the order they were performed."""
    grouped: dict[int, list[Scored]] = {}
    for frame in frames:
        if frame.take:
            grouped.setdefault(frame.take, []).append(frame)
    return tuple(tuple(grouped[key]) for key in sorted(grouped))


def _frames(scored: Sequence[Scored]) -> tuple[Frame, ...]:
    """The timestamp and resume flag of each scored frame."""
    return tuple(Frame(at=frame.at, resumed=frame.resumed) for frame in scored)


def _series(take: Sequence[Scored], feature: str) -> list[Sample]:
    """One feature over one take."""
    return [(frame.at, frame.scores[feature]) for frame in take if feature in frame.scores]


def _series_of(frames: Sequence[Scored], stretch: Sequence[Frame], feature: str) -> list[Sample]:
    """One feature over one continuous stretch of a label."""
    if not stretch:
        return []
    first, last = stretch[0].at, stretch[-1].at
    return [
        (frame.at, frame.scores[feature])
        for frame in frames
        if first <= frame.at <= last and feature in frame.scores
    ]


def _diagnostics(takes: Sequence[Sequence[Scored]]) -> Diagnostics:
    """What the takes looked like: length, distance, and whether the hand was all in shot."""
    if not takes:
        return Diagnostics(0, 0, 0.0, 0.0, 0.0, 0, 0.0, 0)
    lengths = [take[-1].at - take[0].at for take in takes]
    spans = [
        measurement.palm_span
        for take in takes
        for frame in take
        for _, measurement in frame.measurements
    ]
    return Diagnostics(
        takes=len(takes),
        frames=sum(len(take) for take in takes),
        shortest=min(lengths),
        longest=max(lengths),
        median_seconds=statistics.median(lengths),
        two_handed=sum(1 for take in takes if any(len(f.measurements) > 1 for f in take)),
        min_span=min(spans, default=0.0),
        clipped=sum(1 for take in takes if any(frame.clipped for frame in take)),
    )


RATIO_NAMES: tuple[str, ...] = (
    *(f"ext{name}" for name in FINGER_CHAINS),
    "pinchIndex",
    "pinchMiddle",
    "spread",
    "palmArea",
    "span",
    "height",
)


def _measurements(label: str, takes: Sequence[Sequence[Scored]]) -> dict[str, tuple[float, ...]]:
    """Per ratio, one value per take: that take's median, off the side the label names."""
    wanted = "left" if label.startswith("left") else "right" if label.startswith("right") else ""
    collected: dict[str, list[float]] = {name: [] for name in RATIO_NAMES}
    for take in takes:
        per_take: dict[str, list[float]] = {name: [] for name in RATIO_NAMES}
        for frame in take:
            chosen = _pick(frame.measurements, wanted)
            if chosen is None:
                continue
            for name, value in _ratio_values(chosen).items():
                per_take[name].append(value)
        for name, values in per_take.items():
            if values:
                collected[name].append(statistics.median(values))
    return {name: tuple(values) for name, values in collected.items()}


def _pick(measurements: Sequence[tuple[str, Ratios]], side: str) -> Ratios | None:
    """The named side's measurements, else the primary hand's."""
    for name, measurement in measurements:
        if side and name == side:
            return measurement
    return measurements[0][1] if measurements and not side else None


def _ratio_values(measurement: Ratios) -> dict[str, float]:
    """One hand's measurements under the names RATIO_NAMES uses."""
    values = {f"ext{name}": measurement.extension.get(name, 0.0) for name in FINGER_CHAINS}
    values["pinchIndex"] = measurement.pinch_index
    values["pinchMiddle"] = measurement.pinch_middle
    values["spread"] = measurement.spread
    values["palmArea"] = measurement.palm_area
    values["span"] = measurement.palm_span
    values["height"] = measurement.height
    return values


@dataclass(frozen=True, slots=True)
class Proposal:
    """A calibration read off the recording, and one line per constant saying from what."""

    calibration: Calibration
    sources: tuple[tuple[str, str], ...]
    missing: tuple[str, ...]


def propose(summary: Summary, *, calibration: Calibration = CALIBRATION) -> Proposal:
    """A Calibration derived from the labels that reach each constant's extreme."""
    fists = _labels_like(summary, "fist")
    opens = _labels_like(summary, "open")
    pinches = _labels_like(summary, "pinch")

    values: dict[str, float] = {}
    sources: list[tuple[str, str]] = []
    missing: list[str] = []

    def record(key: str, value: float | None, source: str) -> None:
        if value is None:
            missing.append(f"{key} -- nothing in the recording measures it")
            return
        values[key] = round(value, 3)
        sources.append((key, source))

    folding = tuple(f"ext{name}" for name in ("Index", "Middle", "Ring", "Little"))
    record(
        "folded_extension",
        _median_of(summary, fists, folding),
        f"median fingertip extension over {_names(fists)} takes",
    )
    record(
        "folded_extension_thumb",
        _median_of(summary, fists, ("extThumb",)),
        f"median thumb extension over {_names(fists)} takes",
    )
    record(
        "pinch_closed",
        _median_trough(summary, pinches),
        f"median of each {_names(pinches)} take's closest thumb-to-index gap",
    )
    record(
        "pinch_open",
        _median_of(summary, opens, ("pinchIndex",)),
        f"median thumb-to-index gap over {_names(opens)} takes",
    )
    record(
        "spread_closed",
        _median_of(summary, fists, ("spread",)),
        f"median fingertip gap over {_names(fists)} takes",
    )
    record(
        "spread_open",
        _median_of(summary, opens, ("spread",)),
        f"median fingertip gap over {_names(opens)} takes",
    )
    record(
        "palm_area_face_on",
        _median_of(summary, opens, ("palmArea",)),
        f"median knuckle-triangle area over {_names(opens)} takes",
    )
    return _without_refusals(calibration, values, sources, missing)


def _without_refusals(
    calibration: Calibration,
    values: dict[str, float],
    sources: list[tuple[str, str]],
    missing: list[str],
) -> Proposal:
    """Back out any derived constant that would make the proposal unloadable."""
    for _ in CALIBRATION_KEYS:
        problems = replace(calibration, **values).problems()
        if not problems:
            break
        for problem in problems:
            for key in list(values):
                if key in problem:
                    del values[key]
                    missing.append(f"{key} -- would not load: {problem}")
    kept = set(values)
    return Proposal(
        calibration=replace(calibration, **values),
        sources=tuple((key, why) for key, why in sources if key in kept),
        missing=tuple(missing),
    )


def _labels_like(summary: Summary, word: str) -> tuple[str, ...]:
    """Every label whose name contains word, in report order."""
    return tuple(label for label in summary.labels if word in label)


def _names(labels: Sequence[str]) -> str:
    return ", ".join(labels) if labels else "no"


def _median_of(summary: Summary, labels: Sequence[str], names: Sequence[str]) -> float | None:
    """The median across every take of every named label, over the named ratios."""
    gathered = [
        value
        for label in labels
        for name in names
        for value in summary.measurements.get(label, {}).get(name, ())
    ]
    return statistics.median(gathered) if gathered else None


def _median_trough(summary: Summary, labels: Sequence[str]) -> float | None:
    """The median, across pinch takes, of the closest the thumb and index came."""
    gathered = [
        value
        for label in labels
        for value in summary.measurements.get(label, {}).get("pinchIndex", ())
    ]
    if not gathered:
        return None
    ordered = sorted(gathered)
    lower = ordered[: max(1, len(ordered) // 2)]
    return statistics.median(lower)


@dataclass(frozen=True, slots=True)
class Fire:
    """One gesture going active, and where in the recording it happened."""

    gesture: str
    at: float
    label: str
    take: int
    duration: float | None


@dataclass(frozen=True, slots=True)
class Replay:
    """Every firing in one pass, and the takes it was measured against."""

    fires: tuple[Fire, ...]
    takes: Mapping[str, tuple[tuple[int, float, float], ...]]
    gestures: tuple[str, ...]
    stride: int

    def during(self, label: str, take: int) -> tuple[Fire, ...]:
        """Every firing that *started* inside that take."""
        return tuple(f for f in self.fires if f.label == label and f.take == take)

    def covering(self, label: str, start: float, end: float) -> tuple[Fire, ...]:
        """Every firing of any gesture that was active at some point during that span."""
        return tuple(
            fire
            for fire in self.fires
            if fire.label == label
            and fire.at <= end
            and (fire.duration is None or fire.at + fire.duration >= start)
        )


def replay(
    scored: Sequence[Scored],
    definitions: Mapping[str, GestureDefinition],
    *,
    stride: int = 1,
) -> Replay:
    """Run the recording through the real recognizer and note every firing."""
    recognizer = GestureRecognizer(definitions)
    fires: list[Fire] = []
    open_fires: dict[str, int] = {}

    for index, frame in enumerate(scored):
        if frame.resumed:
            recognizer.reset()
            open_fires.clear()
        if stride > 1 and index % stride:
            continue
        started, ended = recognizer.update(frame.scores, frame.at)
        for name in started:
            open_fires[name] = len(fires)
            fires.append(Fire(name, frame.at, frame.label, frame.take, None))
        for name in ended:
            position = open_fires.pop(name, None)
            if position is not None:
                fires[position] = replace(fires[position], duration=frame.at - fires[position].at)

    return Replay(
        fires=tuple(fires),
        takes={
            label: tuple(
                (take[0].take, take[0].at, take[-1].at)
                for take in _takes([f for f in scored if f.label == label])
            )
            for label in _ordered_labels(scored)
        },
        gestures=tuple(definitions),
        stride=stride,
    )


def render(
    summary: Summary,
    definitions: Mapping[str, GestureDefinition] | None = None,
    *,
    proposal: Proposal | None = None,
) -> str:
    """The whole report: takes, matrix, per-gesture blocks, ratios, proposed calibration."""
    parts = [
        _render_takes(summary),
        render_matrix(summary),
        render_blocks(summary, definitions or {}),
        render_ratios(summary),
    ]
    if proposal is not None:
        parts.append(render_proposal(proposal))
    return "\n\n".join(part for part in parts if part)


def _render_takes(summary: Summary) -> str:
    header = f"{'label':<16} {'takes':>5} {'seconds':>16} {'frames':>6} {'2 hands':>7} "
    header += f"{'min span':>8} {'clipped':>7}"
    lines = ["## takes", "", header]
    for label in summary.labels:
        info = summary.diagnostics[label]
        seconds = f"{info.shortest:.1f}-{info.longest:.1f} ({info.median_seconds:.1f})"
        lines.append(
            f"{label:<16} {info.takes:>5} {seconds:>16} {info.frames:>6} "
            f"{f'{info.two_handed}/{info.takes}':>7} {info.min_span:>8.3f} "
            f"{f'{info.clipped}/{info.takes}':>7}"
        )
    return "\n".join(lines)


def render_matrix(summary: Summary) -> str:
    """Feature against label, each cell the peak that feature *held* under that label."""
    columns = _columns(summary.labels)
    width = max((len(name) for name in columns.values()), default=6)
    rows, flat = [], []
    for feature in summary.features:
        values = [summary.cell(label, feature).peak for label in summary.labels]
        if values and max(values) - min(values) < _FLAT:
            flat.append(_short(feature))
            continue
        cells = " ".join(f"{number(value):>{width}}" for value in values)
        rows.append(f"{_short(feature):<20} {cells}")

    if not rows:
        return ""
    name_width = 20
    headings = " ".join(f"{columns[label]:>{width}}" for label in summary.labels)
    head = " " * name_width + " " + headings
    legend = "`L.` is `leftHand`, `R.` is `rightHand`. " if _abbreviated(rows) else ""
    lines = [
        f"## matrix -- highest level held for {summary.window * 1000:.0f} ms, per label",
        "",
        *wrapped(
            legend + "A cell is the peak reached *and held* anywhere under that label, "
            "approach included -- so a `min` threshold below a cell will also fire during "
            "that label's gesture."
        ),
        "",
        head,
        *rows,
    ]
    shortened = [f"{head} = {label}" for label, head in columns.items() if head != label]
    if shortened:
        lines += ["", *wrapped("columns: " + ", ".join(shortened))]
    if flat:
        lines += [
            "",
            *wrapped(f"flat across every label (<{_FLAT:.2f} spread), dropped: " + ", ".join(flat)),
        ]
    return "\n".join(lines)


def _abbreviated(rows: Sequence[str]) -> bool:
    """Whether any row name was shortened, and so needs the legend that explains it."""
    return any(row.startswith(("L.", "R.", ".")) for row in rows)


def render_blocks(
    summary: Summary,
    definitions: Mapping[str, GestureDefinition],
    *,
    skip: Collection[str] = (NEUTRAL,),
) -> str:
    """One block per label: per-take values for the features that gesture leans on."""
    blocks = []
    for label in summary.labels:
        if label in skip:
            continue
        definition = definitions.get(label, {})
        named = _named_features(definition)
        window = _dwell(summary, definition)
        ranked = _ranked(summary, label, named, window)
        if not ranked:
            continue
        info = summary.diagnostics[label]
        header = (
            f"### {label}   {info.takes} takes, {info.shortest:.1f}-{info.longest:.1f}s, "
            f"held {window * 1000:.0f} ms"
            + ("   (no gesture of this name in the candidate)" if not named else "")
        )
        width = max(len(_short(feature)) for feature, _, _ in ranked)
        columns = (
            f"{'feature':<{width}}  dir  {'per take':<{_CELL * info.takes}} "
            f"{'worst':>6} {'@' + f'{summary.long_window * 1000:.0f}ms':>7}  "
            f"{'elsewhere':<20} {'margin':>6}"
        )
        rows = [
            _block_row(summary, label, feature, low, margin, width, window)
            for feature, low, margin in ranked
        ]
        blocks.append("\n".join([header, "", columns, *rows]))

    if not blocks:
        return ""
    preamble = "\n".join(
        [
            "## per gesture -- every take, for the features that gesture leans on",
            "",
            *wrapped(
                "`dir` is which way the condition points: `hi` wants a `min`, `lo` wants a "
                "`max`. `worst` is the take that came closest to failing, `elsewhere` the "
                "strongest label pulling the other way, and `margin` the room between them."
            ),
        ]
    )
    return "\n\n".join([preamble, *blocks])


def _block_row(
    summary: Summary,
    label: str,
    feature: str,
    low: bool,
    margin: float,
    width: int,
    window: float,
) -> str:
    cell = summary.cell(label, feature)
    values = cell.low_at(window) if low else cell.at(window)
    worst = cell.worst_low(window) if low else cell.worst_high(window)
    long = "--" if low else number(cell.worst_high(summary.long_window))
    rival, rival_value = _rival(summary, label, feature, low)
    run = " ".join(f"{number(value):>{_CELL - 1}}" for value in values)
    return (
        f"{_short(feature):<{width}}  {'lo ' if low else 'hi '}  {run:<{_CELL * len(values)}} "
        f"{number(worst):>6} {long:>7}  {f'{number(rival_value)} {rival}':<20} "
        f"{margin:>+6.2f}"
    )


def _named_features(definition: GestureDefinition) -> tuple[str, ...]:
    return tuple(key for key in definition if key != HOLD_KEY)


def _dwell(summary: Summary, definition: GestureDefinition) -> float:
    """The dwell to judge one gesture at: its own hold, else the reference."""
    hold = float(definition.get(HOLD_KEY, 0)) / 1000.0
    return hold if hold in summary.windows else summary.window


def _ranked(
    summary: Summary, label: str, named: Sequence[str], window: float, limit: int = 6
) -> tuple[tuple[str, bool, float], ...]:
    """The features to show for one label: (name, wants_a_max, margin)."""
    scored: list[tuple[str, bool, float]] = []
    for feature in summary.features:
        cell = summary.cell(label, feature)
        if not cell.at(window):
            continue
        _, high_rival = _rival(summary, label, feature, low=False)
        _, low_rival = _rival(summary, label, feature, low=True)
        high_margin = cell.worst_high(window) - high_rival
        low_margin = low_rival - cell.worst_low(window)
        low = low_margin > high_margin
        scored.append((feature, low, max(high_margin, low_margin)))

    useful = [row for row in scored if row[2] > _USEFUL]
    chosen = {feature for feature, _, _ in sorted(useful, key=lambda row: -row[2])[:limit]}
    chosen.update(named)
    order = {feature: index for index, (feature, _, _) in enumerate(scored)}
    kept = [row for row in scored if row[0] in chosen]
    return tuple(sorted(kept, key=lambda row: (-row[2], order[row[0]])))


def _rival(summary: Summary, label: str, feature: str, low: bool) -> tuple[str, float]:
    """The other label that most gets in this one's way, and by how much."""
    others = [other for other in summary.labels if other != label]
    if not others:
        return "--", 0.0
    if low:
        return min(((o, summary.cell(o, feature).peak) for o in others), key=lambda p: p[1])
    return max(((o, summary.cell(o, feature).peak) for o in others), key=lambda p: p[1])


def render_ratios(summary: Summary) -> str:
    """The raw measurements per label, as a table."""
    columns = _columns(summary.labels)
    width = max((len(name) for name in columns.values()), default=6)
    head = f"{'ratio':<12} " + " ".join(f"{columns[n]:>{width}}" for n in summary.labels)
    rows = []
    for name in RATIO_NAMES:
        per_label = [summary.measurements.get(n, {}).get(name, ()) for n in summary.labels]
        cells = " ".join(f"{_median(values):>{width}}" for values in per_label)
        widest = max((max(v) - min(v) for v in per_label if len(v) > 1), default=0.0)
        rows.append(f"{name:<12} {cells}  +-{widest:.3f}")
    return "\n".join(
        [
            "## ratios -- median across takes; the last column is the widest take-to-take gap",
            "",
            *wrapped(
                "Off the hand the label names, else the primary one. Distances are over the "
                "hand's own palm span, `palmArea` over its square, `span` in frame widths. "
                "These, not the scores, are what `ht_hand_calibration` is set from."
            ),
            "",
            head,
            *rows,
        ]
    )


def render_proposal(proposal: Proposal) -> str:
    """The proposed calibration as the JSON block that goes in the config, plus its whys."""
    block = {key: getattr(proposal.calibration, key) for key, _ in proposal.sources}
    lines = [
        "## proposed ht_hand_calibration",
        "",
        "```json",
        json.dumps({"ht_hand_calibration": block}, indent=2),
        "```",
        "",
        *(f"- `{key}` -- {why}" for key, why in proposal.sources),
    ]
    lines += [f"- left alone: `{key}`" for key in proposal.missing]
    return "\n".join(lines)


def render_replay(result: Replay) -> str:
    """Per label: did the gesture of that name fire, once, and did anything else."""
    rate = "every frame" if result.stride == 1 else f"every {result.stride} frames"
    lines = [
        f"## replay -- the candidate config over the recording, {rate}",
        "",
        *wrapped(
            "`fired` counts takes the gesture was active during, whenever it started. "
            "`when` is the median gap between the take starting and the gesture firing, so "
            "a negative one fired on the way in, before the shape was finished. `double` "
            "counts takes it fired more than once in -- a fault for a held shape, and the "
            "whole point of a repeated pinch."
        ),
        "",
        f"{'label':<16} {'takes':>5} {'fired':>6} {'when':>7} {'held':>6} {'double':>6}  "
        "cross-fire",
    ]
    for label, takes in result.takes.items():
        expected = label if label in result.gestures else ""
        hits = [
            [f for f in result.covering(label, start, end) if f.gesture == expected]
            for _, start, end in takes
        ]
        latencies = [
            fires[0].at - start for fires, (_, start, _) in zip(hits, takes, strict=True) if fires
        ]
        durations = [f.duration for fires in hits for f in fires if f.duration is not None]
        cross: dict[str, int] = {}
        for _, start, end in takes:
            for fire in result.covering(label, start, end):
                if fire.gesture != expected:
                    cross[fire.gesture] = cross.get(fire.gesture, 0) + 1
        fired = sum(1 for fires in hits if fires)
        lines.append(
            f"{label:<16} {len(takes):>5} "
            f"{(f'{fired}/{len(takes)}' if expected else '--'):>6} "
            f"{(f'{statistics.median(latencies) * 1000:+.0f}ms' if latencies else '--'):>7} "
            f"{(f'{statistics.median(durations):.1f}s' if durations else '--'):>6} "
            f"{sum(1 for fires in hits if len(fires) > 1):>6}  "
            f"{', '.join(f'{name} x{count}' for name, count in sorted(cross.items())) or '--'}"
        )

    outside = [f for f in result.fires if not f.take]
    if outside:
        counted: dict[str, int] = {}
        for fire in outside:
            counted[fire.gesture] = counted.get(fire.gesture, 0) + 1
        lines += [
            "",
            *wrapped(
                "between takes (the approach, the retreat and the pauses): "
                + ", ".join(f"{name} x{count}" for name, count in sorted(counted.items()))
            ),
        ]
    return "\n".join(lines)


def _short(name: str) -> str:
    """rightHandThumbPointingUp -> R.ThumbPointingUp, so a row fits on a line."""
    for prefix, mark in (("leftHand", "L."), ("rightHand", "R."), ("hand", ".")):
        if name.startswith(prefix):
            return mark + name[len(prefix) :]
    return name


def _columns(labels: Sequence[str]) -> dict[str, str]:
    """Short, unique column headings."""
    shortened = {
        label: label.replace("left_", "l.").replace("right_", "r.").replace("both_", "b.")
        for label in labels
    }
    for width in range(6, 40):
        headings = {label: name[:width] for label, name in shortened.items()}
        if len(set(headings.values())) == len(headings):
            return headings
    return shortened


def wrapped(text: str) -> list[str]:
    """Wrap prose to a width a terminal can hold."""
    return textwrap.wrap(text, width=_PROSE) or [""]


def number(value: float) -> str:
    """0.73 as .73: the leading zero says nothing and costs a column."""
    text = f"{value:.2f}"
    return text[1:] if text.startswith("0.") else text


def _median(values: Sequence[float]) -> str:
    """The middle of a label's takes, or `--` when none of them measured it."""
    return f"{statistics.median(values):.3f}" if values else "--"


def definitions_of(payload: Mapping[str, Any]) -> dict[str, GestureDefinition]:
    """ht_custom_gestures out of a config fragment, keeping only the hand gestures."""
    block = payload.get("ht_custom_gestures", {})
    if not isinstance(block, Mapping):
        return {}
    known = set(HAND_FEATURES) | {HOLD_KEY}
    return {
        name: definition
        for name, definition in block.items()
        if isinstance(definition, Mapping) and all(key in known for key in definition)
    }
