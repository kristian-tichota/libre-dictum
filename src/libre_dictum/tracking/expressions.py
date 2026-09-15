from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .gestures import FACE_FEATURES
from .takes import Prompt, Segmenter, remaining

FACE_KIND = "face"

HAND_KIND = "hands"

SCHEMA_VERSION = 1

_PLACES = 3


@dataclass(frozen=True, slots=True)
class FaceFrame:
    """One frame of blendshapes, head pose and the current prompt."""

    at: float
    scores: Mapping[str, float] = field(default_factory=dict)
    yaw: float = 0.0
    pitch: float = 0.0
    label: str = ""
    take: int = 0
    resumed: bool = False

    @property
    def present(self) -> bool:
        """Whether a face was landmarked at all."""
        return bool(self.scores)


def encode(frame: FaceFrame) -> str:
    """One frame as a JSON line, as an array in FACE_FEATURES order."""
    payload: dict[str, Any] = {"t": round(frame.at, 3)}
    if frame.label:
        payload["l"] = frame.label
    if frame.take:
        payload["n"] = frame.take
    if frame.resumed:
        payload["r"] = 1
    if frame.scores:
        payload["y"] = round(frame.yaw, 2)
        payload["p"] = round(frame.pitch, 2)
        payload["b"] = [round(frame.scores.get(name, 0.0), _PLACES) for name in FACE_FEATURES]
    return json.dumps(payload, separators=(",", ":"))


def decode(line: str) -> FaceFrame:
    """One JSON line back into a FaceFrame."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not a recorded frame: {exc}") from exc
    if not isinstance(payload, dict) or "t" not in payload:
        raise ValueError("not a recorded frame: no timestamp")

    values = payload.get("b", ())
    if values and len(values) != len(FACE_FEATURES):
        raise ValueError(
            f"recorded {len(values)} blendshapes, this build knows {len(FACE_FEATURES)}: "
            "the recording was made against a different face model"
        )
    return FaceFrame(
        at=float(payload["t"]),
        scores={
            name: float(value) for name, value in zip(FACE_FEATURES, values, strict=bool(values))
        },
        yaw=float(payload.get("y", 0.0)),
        pitch=float(payload.get("p", 0.0)),
        label=str(payload.get("l", "")),
        take=int(payload.get("n", 0)),
        resumed=bool(payload.get("r", 0)),
    )


def recording_kind(notes: Mapping[str, Any]) -> str:
    """Which tool wrote a recording, from its session.json."""
    return str(notes.get("kind", HAND_KIND))


def read_frames(lines: Iterable[str]) -> Iterator[FaceFrame]:
    """Decode a recording, skipping blank lines."""
    for line in lines:
        if line.strip():
            yield decode(line)


IDLE_QUANTILE = 0.95

ACTIVE_MARGIN = 0.25


@dataclass(frozen=True, slots=True)
class Baseline:
    """What each blendshape sits at on a face that is not doing anything."""

    levels: Mapping[str, float] = field(default_factory=dict)
    frames: int = 0

    @classmethod
    def measure(cls, frames: Iterable[FaceFrame], *, quantile: float = IDLE_QUANTILE) -> Baseline:
        """Read the idle level of every feature off a stretch of frames doing nothing."""
        gathered: dict[str, list[float]] = {}
        counted = 0
        for frame in frames:
            if not frame.present:
                continue
            counted += 1
            for name, value in frame.scores.items():
                gathered.setdefault(name, []).append(value)
        return cls(
            levels={name: _quantile(values, quantile) for name, values in gathered.items()},
            frames=counted,
        )

    @property
    def measured(self) -> bool:
        """Whether there is anything here to gate against."""
        return bool(self.levels)

    def idle(self, feature: str) -> float:
        """This feature's resting level; zero for one the rest pass never saw."""
        return self.levels.get(feature, 0.0)

    def moving(self, *, above: float = 0.02) -> tuple[tuple[str, float], ...]:
        """The features that are not quiet at rest, highest first, and nothing else."""
        return tuple(
            sorted(
                ((name, level) for name, level in self.levels.items() if level > above),
                key=lambda pair: -pair[1],
            )
        )


def normalised(scores: Mapping[str, float], levels: Mapping[str, float]) -> dict[str, float]:
    """Every score as its rise from this face's own resting level, clamped to [0, 1]."""
    if not levels:
        return dict(scores)
    return {name: _rise(value, levels.get(name, 0.0)) for name, value in scores.items()}


def _rise(value: float, idle: float) -> float:
    """One score's distance from its idle level toward 1, clamped."""
    headroom = 1.0 - idle
    if headroom <= 0.0:
        return 0.0
    return min(1.0, max(0.0, (value - idle) / headroom))


def _quantile(values: Sequence[float], quantile: float) -> float:
    """One point in a sorted sample, by nearest rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(quantile * (len(ordered) - 1))))
    return ordered[index]


@dataclass(frozen=True, slots=True)
class Gate:
    """One gate feature's live value and the level that would open it."""

    feature: str
    score: float | None
    opens_at: float

    @property
    def open(self) -> bool:
        return self.score is not None and self.score >= self.opens_at


def gate(
    scores: Mapping[str, float],
    baseline: Baseline,
    features: Sequence[str],
    *,
    margin: float = ACTIVE_MARGIN,
) -> tuple[Gate, ...]:
    """Each gate feature's reading for one frame, against its own idle level."""
    return tuple(
        Gate(
            feature=feature,
            score=scores.get(feature) if scores else None,
            opens_at=_opens_at(baseline.idle(feature), margin),
        )
        for feature in features
    )


def _opens_at(idle: float, margin: float) -> float:
    """The level a gate feature opens at."""
    return min(1.0, idle + margin * max(0.0, 1.0 - idle))


def attempting(readings: Sequence[Gate]) -> bool:
    """Whether the face is having a go at the prompted expression."""
    return any(reading.open for reading in readings)


@dataclass
class Rest:
    """How long the face has been in shot and inactive."""

    since: float | None = None

    def feed(self, at: float, *, present: bool, active: bool) -> None:
        if present and not active:
            self.since = at if self.since is None else self.since
        else:
            self.since = None

    def held(self, at: float) -> float:
        """Seconds of unbroken rest, or zero when the face is away or busy."""
        return 0.0 if self.since is None else at - self.since

    def reset(self) -> None:
        self.since = None


SKIP_AFTER = 12.0

SKIP_WARNING = 5.0

NOTE_FLOOR = 0.3


def rejection(prompt: Prompt, seconds: float) -> str:
    """What to say about a span too short to keep."""
    if seconds < NOTE_FLOOR:
        return ""
    return f"{prompt.label}: too quick ({seconds:.1f}s) -- again"


def segmenter() -> Segmenter:
    """The hand tool's segmenter, with the face's grace periods."""
    return Segmenter(enter=0.10, exit=0.35, minimum=0.5, settle=0.40)


DEFAULT_SCRIPT: tuple[tuple[str, str], ...] = (
    ("smile", "a broad smile, both corners, hold it, then let your face go"),
    ("left_smirk", "pull only the LEFT corner of your mouth up, hold, then relax"),
    ("right_smirk", "pull only the RIGHT corner of your mouth up, hold, then relax"),
    ("brow_raise", "raise both eyebrows, mouth relaxed, hold, then relax"),
    ("pucker", "purse your lips as if to whistle, brows level, hold, then relax"),
    ("pucker_surprise", "purse your lips AND raise your brows, hold, then relax"),
    ("pucker_frown", "purse your lips AND lower your brows, hold, then relax"),
    ("cheek_puff", "puff both cheeks out, hold, then let the air go"),
    ("left_wink", "wink your LEFT eye, right eye open, hold it, then relax"),
    ("right_wink", "wink your RIGHT eye, left eye open, hold it, then relax"),
)

GATES: Mapping[str, tuple[str, ...]] = {
    "smile": ("mouthSmileLeft", "mouthSmileRight"),
    "left_smirk": ("mouthSmileLeft",),
    "right_smirk": ("mouthSmileRight",),
    "brow_raise": ("browInnerUp", "browOuterUpLeft", "browOuterUpRight"),
    "pucker": ("mouthPucker",),
    "pucker_surprise": ("mouthPucker",),
    "pucker_frown": ("mouthPucker",),
    "cheek_puff": ("mouthClose",),
    "left_wink": ("eyeBlinkLeft",),
    "right_wink": ("eyeBlinkRight",),
}


@dataclass(frozen=True, slots=True)
class Pass:
    """A timed stretch of the face doing something ordinary, recorded under its own label."""

    label: str
    seconds: float
    instruction: str


REST = "rest"

PASSES: tuple[Pass, ...] = (
    Pass(
        REST,
        20.0,
        "nothing at all -- sit still, look at the screen, let your face go slack",
    ),
    Pass(
        "talking",
        60.0,
        "talk out loud, as if dictating -- read something, or think out loud",
    ),
    Pass(
        "reading",
        60.0,
        "read the screen silently, scroll, concentrate on something hard",
    ),
    Pass(
        "amused",
        60.0,
        "be amused -- smile naturally, laugh if something is funny, do not perform it",
    ),
    Pass(
        "moving",
        60.0,
        "move your head about -- turn, tilt, lean in and out, face relaxed",
    ),
)

DEFAULT_ROUNDS = 6


def pass_labels(passes: Sequence[Pass] = PASSES) -> tuple[str, ...]:
    """The pass labels, for a report that has to tell a performance from an activity."""
    return tuple(one.label for one in passes)


def build_plan(
    labels: Sequence[str],
    rounds: int,
    *,
    passes: Sequence[Pass] = PASSES,
) -> tuple[Prompt, ...]:
    """The rest pass, then the performances round-robin, then the other passes."""
    first = passes[:1]
    plan = [Prompt(label=one.label, take=1, seconds=one.seconds) for one in first]
    plan += [
        Prompt(label=label, take=index + 1, round_number=index + 1)
        for index in range(max(rounds, 0))
        for label in labels
    ]
    plan += [Prompt(label=one.label, take=1, seconds=one.seconds) for one in passes[1:]]
    return tuple(plan)


def still_owed(
    plan: Sequence[Prompt],
    done: Iterable[tuple[str, int]],
    skipped: Iterable[str] = (),
) -> tuple[Prompt, ...]:
    """The prompts a resumed session still owes."""
    dropped = set(skipped)
    return tuple(prompt for prompt in remaining(plan, done) if prompt.label not in dropped)


def instructions(
    script: Sequence[tuple[str, str]] = DEFAULT_SCRIPT,
    passes: Sequence[Pass] = PASSES,
) -> dict[str, str]:
    """Label to instruction, over both halves of the plan."""
    return {**dict(script), **{one.label: one.instruction for one in passes}}
