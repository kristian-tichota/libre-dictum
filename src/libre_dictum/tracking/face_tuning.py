from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from .expressions import IDLE_QUANTILE, Baseline, FaceFrame, normalised, pass_labels
from .gestures import FACE_FEATURES, HOLD_KEY, GestureDefinition
from .tuning import Scored, Summary, number, render_blocks, render_matrix, wrapped
from .tuning import summarise as summarise_scores

WINDOWS: tuple[float, ...] = (0.2, 0.3, 0.4, 0.5, 0.7, 1.0)

MIN_MARGIN = 0.05

_BIAS = 0.65

_RELEASE_RISE = 0.25

_MATRIX_ROWS = 18

_QUIET = 0.02

UNMEASURED = Baseline()


def score(frames: Iterable[FaceFrame], baseline: Baseline = UNMEASURED) -> list[Scored]:
    """A face recording as the frames the shared statistics run on, normalised."""
    levels = baseline.levels
    return [
        Scored(
            at=frame.at,
            label=frame.label,
            take=frame.take,
            resumed=frame.resumed,
            scores=normalised(frame.scores, levels) if frame.scores else frame.scores,
        )
        for frame in frames
    ]


def windows_for(definitions: Mapping[str, GestureDefinition]) -> tuple[float, ...]:
    """The dwells to measure at: WINDOWS, plus any the candidate already asks for."""
    holds = {
        float(definition.get(HOLD_KEY, 0)) / 1000.0
        for definition in definitions.values()
        if float(definition.get(HOLD_KEY, 0)) > 0.0
    }
    return tuple(sorted(set(WINDOWS) | holds))


def summarise(
    scored: Sequence[Scored],
    *,
    windows: Sequence[float] = WINDOWS,
    passes: Sequence[str] = pass_labels(),
) -> Summary:
    """Reduce a scored recording, with the performances first and the passes last."""
    summary = summarise_scores(scored, windows=windows, features=FACE_FEATURES)
    ordered = [label for label in summary.labels if label not in passes]
    ordered += [label for label in summary.labels if label in passes]
    return replace(summary, labels=tuple(ordered))


def matrix_features(
    summary: Summary, definitions: Mapping[str, GestureDefinition]
) -> tuple[str, ...]:
    """The features for a matrix row: everything the candidate names, plus the movers."""
    named = {
        feature
        for definition in definitions.values()
        for feature in definition
        if feature != HOLD_KEY
    }
    spread = {
        feature: _spread([summary.cell(label, feature).peak for label in summary.labels])
        for feature in summary.features
    }
    movers = sorted(summary.features, key=lambda feature: -spread[feature])[:_MATRIX_ROWS]
    chosen = named | set(movers)
    return tuple(feature for feature in summary.features if feature in chosen)


def _spread(values: Sequence[float]) -> float:
    return max(values) - min(values) if values else 0.0


@dataclass(frozen=True, slots=True)
class PassInfo:
    """What one label's takes looked like, apart from their scores."""

    takes: int
    frames: int
    shortest: float
    longest: float
    median_seconds: float
    lost: int
    yaw_span: float
    pitch_span: float


def diagnose(frames: Sequence[FaceFrame]) -> dict[str, PassInfo]:
    """Per label, what its takes looked like."""
    grouped: dict[str, dict[int, list[FaceFrame]]] = {}
    for frame in frames:
        if frame.label and frame.take:
            grouped.setdefault(frame.label, {}).setdefault(frame.take, []).append(frame)

    diagnostics = {}
    for label, takes in grouped.items():
        spans = [takes[key] for key in sorted(takes)]
        lengths = [span[-1].at - span[0].at for span in spans]
        seen = [frame for span in spans for frame in span if frame.present]
        diagnostics[label] = PassInfo(
            takes=len(spans),
            frames=sum(len(span) for span in spans),
            shortest=min(lengths, default=0.0),
            longest=max(lengths, default=0.0),
            median_seconds=statistics.median(lengths) if lengths else 0.0,
            lost=sum(1 for span in spans for frame in span if not frame.present),
            yaw_span=_spread([frame.yaw for frame in seen]),
            pitch_span=_spread([frame.pitch for frame in seen]),
        )
    return diagnostics


@dataclass(frozen=True, slots=True)
class Bound:
    """One condition's proposed limit, and the room either side of it."""

    gesture: str
    feature: str
    low: bool
    value: float | None
    release: float | None
    own: float
    rival: str
    rival_value: float
    margin: float
    hold: float


@dataclass(frozen=True, slots=True)
class Proposal:
    """A candidate ht_custom_gestures read off the recording, with its bounds."""

    definitions: dict[str, GestureDefinition]
    bounds: tuple[Bound, ...]
    untouched: tuple[tuple[str, str], ...]
    baseline: Mapping[str, float] = MappingProxyType({})


def propose(
    summary: Summary,
    definitions: Mapping[str, GestureDefinition],
    baseline: Baseline,
) -> Proposal:
    """Thresholds and a dwell per gesture, from the label of the same name."""
    proposed: dict[str, GestureDefinition] = {}
    bounds: list[Bound] = []
    untouched: list[tuple[str, str]] = []

    for name, definition in definitions.items():
        if name not in summary.labels:
            proposed[name] = dict(definition)
            untouched.append((name, "the recording holds no takes under this name"))
            continue

        hold, measured, separated = _at_best_dwell(summary, name, definition)
        bounds.extend(measured)
        proposed[name] = _definition_from(definition, measured, hold if separated else None)
        blocked = [bound.feature for bound in measured if bound.value is None]
        if blocked:
            untouched.append(
                (
                    name,
                    f"no room at any dwell for {', '.join(blocked)}; those conditions and "
                    f"its dwell are unchanged. Closest at {hold * 1000:.0f} ms",
                )
            )
    return Proposal(
        definitions=proposed,
        bounds=tuple(bounds),
        untouched=tuple(untouched),
        baseline={name: round(level, 4) for name, level in baseline.moving(above=_QUIET)},
    )


def _at_best_dwell(
    summary: Summary, label: str, definition: GestureDefinition
) -> tuple[float, tuple[Bound, ...], bool]:
    """The shortest dwell at which every condition has room, and the bounds there."""
    attempts = [
        (window, _bounds_at(summary, label, definition, window)) for window in summary.windows
    ]
    for window, bounds in attempts:
        if bounds and all(bound.value is not None for bound in bounds):
            return window, bounds, True
    if not attempts:
        return summary.window, (), False
    window, bounds = max(attempts, key=lambda attempt: _worst_margin(attempt[1]))
    return window, bounds, False


def _worst_margin(bounds: Sequence[Bound]) -> float:
    """The condition with least room, which is what decides whether a dwell separates."""
    return min((bound.margin for bound in bounds), default=0.0)


def _bounds_at(
    summary: Summary,
    label: str,
    definition: GestureDefinition,
    window: float,
) -> tuple[Bound, ...]:
    """Every condition of one gesture, measured at one dwell."""
    bounds = []
    for feature, limits in definition.items():
        if feature == HOLD_KEY or not isinstance(limits, Mapping):
            continue
        for key in ("min", "max"):
            if key in limits:
                bounds.append(_bound(summary, label, feature, window, low=key == "max"))
    return tuple(bounds)


def _bound(
    summary: Summary,
    label: str,
    feature: str,
    window: float,
    *,
    low: bool,
) -> Bound:
    """One condition at one dwell."""
    cell = summary.cell(label, feature)
    rival, rival_value = _rival_at(summary, label, feature, window)
    own = cell.worst_low(window) if low else cell.worst_high(window)
    margin = (rival_value - own) if low else (own - rival_value)
    value = None
    if margin > MIN_MARGIN:
        raw = own + (1.0 - _BIAS) * margin if low else rival_value + _BIAS * margin
        value = round(raw, 2)
    return Bound(
        gesture=label,
        feature=feature,
        low=low,
        value=value,
        release=(None if value is None else _release(value, rival_value, low=low)),
        own=own,
        rival=rival,
        rival_value=rival_value,
        margin=margin,
        hold=window,
    )


def _rival_at(summary: Summary, label: str, feature: str, window: float) -> tuple[str, float]:
    """The other label that sustained this feature highest at that dwell, and by how much."""
    others = [
        (other, max(summary.cell(other, feature).at(window), default=0.0))
        for other in summary.labels
        if other != label
    ]
    return max(others, key=lambda pair: pair[1]) if others else ("--", 0.0)


def _definition_from(
    definition: GestureDefinition, bounds: Sequence[Bound], hold: float | None
) -> GestureDefinition:
    """One gesture's proposed block: the measured limits, and the rest left alone."""
    built: dict[str, Any] = {}
    if hold is not None:
        built[HOLD_KEY] = round(hold * 1000)
    elif HOLD_KEY in definition:
        built[HOLD_KEY] = definition[HOLD_KEY]
    measured = {(bound.feature, bound.low): bound for bound in bounds}
    for feature, limits in definition.items():
        if feature == HOLD_KEY or not isinstance(limits, Mapping):
            continue
        block: dict[str, float] = {}
        for key in ("min", "max"):
            if key not in limits:
                continue
            bound = measured.get((feature, key == "max"))
            if bound is None or bound.value is None or bound.release is None:
                block[key] = float(limits[key])
                if "release" in limits:
                    block.setdefault("release", float(limits["release"]))
                continue
            block[key] = bound.value
            block.setdefault("release", bound.release)
        built[feature] = block
    return built


def _release(value: float, rival: float, *, low: bool) -> float:
    """Where a condition re-arms, measured up from rest rather than down from the threshold."""
    if low:
        return round(min(1.0, max(value + 0.05, value + 0.5 * (rival - value))), 2)
    return round(max(0.0, min(value - 0.05, _RELEASE_RISE * value)), 2)


def render(
    summary: Summary,
    definitions: Mapping[str, GestureDefinition],
    diagnostics: Mapping[str, PassInfo],
    baseline: Baseline,
    *,
    proposal: Proposal | None = None,
    passes: Sequence[str] = pass_labels(),
) -> str:
    """The whole report: takes, baseline, matrix, per-gesture blocks, proposal."""
    parts = [
        render_passes(summary, diagnostics, passes=passes),
        render_baseline(baseline),
        render_matrix(replace(summary, features=matrix_features(summary, definitions))),
        render_blocks(summary, definitions, skip=tuple(passes)),
    ]
    if proposal is not None:
        parts.append(render_proposal(proposal))
    return "\n\n".join(part for part in parts if part)


def render_passes(
    summary: Summary,
    diagnostics: Mapping[str, PassInfo],
    *,
    passes: Sequence[str] = pass_labels(),
) -> str:
    """Per label: takes, duration, face lost, and head turn."""
    header = (
        f"{'label':<16} {'kind':>5} {'takes':>5} {'seconds':>16} {'frames':>6} "
        f"{'no face':>8} {'yaw':>6} {'pitch':>6}"
    )
    lines = ["## takes", "", header]
    for label in summary.labels:
        info = diagnostics.get(label)
        if info is None:
            continue
        seconds = f"{info.shortest:.1f}-{info.longest:.1f} ({info.median_seconds:.1f})"
        lines.append(
            f"{label:<16} {'pass' if label in passes else 'held':>5} {info.takes:>5} "
            f"{seconds:>16} {info.frames:>6} {f'{info.lost}':>8} "
            f"{info.yaw_span:>6.1f} {info.pitch_span:>6.1f}"
        )
    lines += [
        "",
        *wrapped(
            "`no face` counts frames inside a take that the landmarker did not find a face "
            "in. A gesture built out of `max` conditions would hold through every one of "
            "them if absence read as zero, so a column that is not zero is a recording to "
            "distrust. `yaw` and `pitch` are the widest swing in degrees, which is the "
            "confound the hand session had no equivalent of."
        ),
    ]
    return "\n".join(lines)


def render_baseline(baseline: Baseline) -> str:
    """What this face sits at when it is not doing anything."""
    moving = baseline.moving(above=_QUIET)
    lines = [
        f"## baseline -- the {IDLE_QUANTILE:.0%} resting level, over {baseline.frames} "
        "frames of the rest pass",
        "",
        *wrapped(
            "Every feature not listed sits under "
            f"{_QUIET:.2f} at rest and constrains nothing. A `release` proposed below is "
            "measured up from this level rather than down from its threshold, which is the "
            "one number a guessed configuration could not have had."
        ),
        "",
    ]
    if not moving:
        return "\n".join([*lines, "nothing above the floor -- a very still face."])
    lines += [f"{feature:<24} {level:.3f}" for feature, level in moving]
    return "\n".join(lines)


def render_proposal(proposal: Proposal) -> str:
    """The proposed configuration as one fragment, and the room behind every number."""
    lines = [
        "## proposed ht_face_baseline and ht_custom_gestures",
        "",
        *wrapped(
            "Paste it whole, or replay it first with `--candidate`: this proposes and the "
            "replay decides. `own` is the worst take of the gesture's own performance, "
            "`rival` the most any other label sustained at the same dwell, and `hold` the "
            "shortest dwell at which every condition still had room."
        ),
        "",
        *wrapped(
            "**Both halves, and they move together.** Every threshold below is a rise from "
            "the resting level in the same block, so the same numbers against a different "
            "`ht_face_baseline` are a different gesture. Nothing at load can tell a "
            "migrated threshold from a stale one -- they are all numbers in [0, 1] -- so "
            "replacing one half and keeping the other is the one mistake this cannot catch "
            "for you. `--face-replay` reports it as 0/6."
        ),
        "",
        *wrapped(
            "The `elsewhere` column in the blocks above answers a different question and "
            "will often name a different label: for a `lo` row it is whichever label sinks "
            "*lowest*, the one an exclusion would fail to catch, where `rival` here is the "
            "one an exclusion is *for*. A `--` means there was no room at any dwell, and "
            "the condition was left exactly as it came in."
        ),
        "",
        "```json",
        json.dumps(
            {
                "ht_face_baseline": dict(proposal.baseline),
                "ht_custom_gestures": proposal.definitions,
            },
            indent=2,
        ),
        "```",
        "",
        f"{'gesture':<18} {'feature':<20} {'dir':>4} {'set':>5} {'rel':>5} "
        f"{'own':>5} {'rival':>5}  worst rival",
    ]
    for bound in proposal.bounds:
        release = "--" if bound.release is None else number(bound.release)
        lines.append(
            f"{bound.gesture:<18} {bound.feature:<20} {'lo' if bound.low else 'hi':>4} "
            f"{('--' if bound.value is None else number(bound.value)):>5} {release:>5} "
            f"{number(bound.own):>5} {number(bound.rival_value):>5}  "
            f"{bound.rival} ({bound.margin:+.2f})"
        )
    if proposal.untouched:
        lines += ["", *(f"- `{name}` -- {why}" for name, why in proposal.untouched)]
    return "\n".join(lines)


def face_definitions_of(payload: Mapping[str, Any]) -> dict[str, GestureDefinition]:
    """ht_custom_gestures out of a config fragment, keeping only the facial gestures."""
    block = payload.get("ht_custom_gestures", {})
    if not isinstance(block, Mapping):
        return {}
    known = set(FACE_FEATURES) | {HOLD_KEY}
    return {
        name: definition
        for name, definition in block.items()
        if isinstance(definition, Mapping) and all(key in known for key in definition)
    }
