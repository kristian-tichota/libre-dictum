from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from .hands import Hand, Point

FRAMES_FILE = "frames.jsonl"
SESSION_FILE = "session.json"

SCHEMA_VERSION = 1

NEUTRAL = "neutral"

_PLACES = 4


@dataclass(frozen=True, slots=True)
class Frame:
    """One landmarked frame, and what the session was asking for when it arrived."""

    at: float
    hands: tuple[Hand, ...] = ()
    label: str = ""
    take: int = 0
    resumed: bool = False

    @property
    def present(self) -> bool:
        """Whether any usable hand was landmarked."""
        return any(hand.valid() for hand in self.hands)


def encode(frame: Frame) -> str:
    """One frame as a JSON line, compactly enough that ten minutes stays a few megabytes."""
    payload: dict[str, Any] = {"t": round(frame.at, 3)}
    if frame.label:
        payload["l"] = frame.label
    if frame.take:
        payload["n"] = frame.take
    if frame.resumed:
        payload["r"] = 1
    if frame.hands:
        payload["h"] = [
            {
                "s": hand.side,
                "p": [round(axis, _PLACES) for point in hand.landmarks for axis in point],
            }
            for hand in frame.hands
        ]
    return json.dumps(payload, separators=(",", ":"))


def decode(line: str) -> Frame:
    """One JSON line back into a Frame."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not a recorded frame: {exc}") from exc
    if not isinstance(payload, dict) or "t" not in payload:
        raise ValueError("not a recorded frame: no timestamp")

    hands = []
    for entry in payload.get("h", ()):
        flat = [float(axis) for axis in entry.get("p", ())]
        points: list[Point] = [
            (flat[base], flat[base + 1], flat[base + 2]) for base in range(0, len(flat) - 2, 3)
        ]
        hands.append(Hand(landmarks=points, side=str(entry.get("s", ""))))
    return Frame(
        at=float(payload["t"]),
        hands=tuple(hands),
        label=str(payload.get("l", "")),
        take=int(payload.get("n", 0)),
        resumed=bool(payload.get("r", 0)),
    )


def read_frames(lines: Iterable[str]) -> Iterator[Frame]:
    """Decode a recording, skipping blank lines."""
    for line in lines:
        if line.strip():
            yield decode(line)


@dataclass(frozen=True, slots=True)
class Prompt:
    """One thing the session asks for: a gesture and which repetition of it this is."""

    label: str
    take: int
    round_number: int = 0
    seconds: float = 0.0

    @property
    def timed(self) -> bool:
        """Whether this prompt runs on a clock."""
        return self.seconds > 0.0


def build_plan(
    labels: Sequence[str], rounds: int, *, neutral_seconds: float = 0.0
) -> tuple[Prompt, ...]:
    """One take of each label, repeated rounds times, then the neutral pass."""
    plan = [
        Prompt(label=label, take=index + 1, round_number=index + 1)
        for index in range(max(rounds, 0))
        for label in labels
    ]
    if neutral_seconds > 0.0:
        plan.append(Prompt(label=NEUTRAL, take=1, seconds=neutral_seconds))
    return tuple(plan)


def remaining(plan: Sequence[Prompt], done: Iterable[tuple[str, int]]) -> tuple[Prompt, ...]:
    """The prompts a resumed session still owes, given the takes already in the file."""
    complete = set(done)
    return tuple(prompt for prompt in plan if (prompt.label, prompt.take) not in complete)


@dataclass(frozen=True, slots=True)
class Segment:
    """A hand-present span, by the clock."""

    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class Closed:
    """A span that ended, and whether it was kept."""

    segment: Segment
    kept: bool


@dataclass
class Segmenter:
    """Turns "is there a hand" into takes, with a grace period at each end."""

    enter: float = 0.12
    exit: float = 0.40
    minimum: float = 0.35
    settle: float = 0.40

    _empty_since: float | None = field(default=None, init=False)
    _present_since: float | None = field(default=None, init=False)
    _last_present: float = field(default=0.0, init=False)
    _started: float | None = field(default=None, init=False)
    _armed: bool = field(default=False, init=False)

    @property
    def armed(self) -> bool:
        """Whether the frame has been empty long enough for a prompt to be live."""
        return self._armed

    @property
    def recording(self) -> bool:
        """Whether a take is open."""
        return self._started is not None

    @property
    def started(self) -> float:
        """When the open take began, or 0.0 when none is open."""
        return self._started or 0.0

    def reset(self) -> None:
        """Forget everything; the next prompt starts from an empty frame again."""
        self._empty_since = None
        self._present_since = None
        self._last_present = 0.0
        self._started = None
        self._armed = False

    def feed(self, at: float, present: bool) -> Closed | None:
        """One frame's presence."""
        if present:
            self._last_present = at
            self._empty_since = None
            if self._present_since is None:
                self._present_since = at
            if self._started is None and self._armed and at - self._present_since >= self.enter:
                self._started = self._present_since
            return None

        self._present_since = None
        if self._empty_since is None:
            self._empty_since = at

        if self._started is not None:
            if at - self._last_present < self.exit:
                return None
            segment = Segment(start=self._started, end=self._last_present)
            self._started = None
            self._armed = False
            self._empty_since = at
            return Closed(segment=segment, kept=segment.seconds >= self.minimum)

        self._armed = self._armed or at - self._empty_since >= self.settle
        return None


DEFAULT_SCRIPT: tuple[tuple[str, str], ...] = (
    ("left_fist", "left hand up from below, close it into a fist, hold, take it away"),
    ("right_fist", "right hand up from below, close it into a fist, hold, take it away"),
    ("left_pinch", "left hand up, pinch thumb to index a few times, take it away"),
    ("right_pinch", "right hand up, pinch thumb to index a few times, take it away"),
    ("left_thumbs_up", "left hand up in a thumbs-up, hold, take it away"),
    ("right_thumbs_up", "right hand up in a thumbs-up, hold, take it away"),
    ("left_open", "left hand up, open and flat, hold, take it away"),
    ("right_open", "right hand up, open and flat, hold, take it away"),
    ("both_open", "both hands up, open and flat, hold, take them away"),
)

NEUTRAL_INSTRUCTION = (
    "no gesture at all -- rest your hands, reach for the mouse, type, scratch your face"
)

DEFAULT_ROUNDS = 8

DEFAULT_NEUTRAL_SECONDS = 60.0
