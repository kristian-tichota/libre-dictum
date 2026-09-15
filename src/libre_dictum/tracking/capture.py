from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, TextIO

import cv2
import mediapipe as mp

from ..settings import CaptureSettings, HandTrackingSettings
from .hands import ratios
from .takes import (
    DEFAULT_NEUTRAL_SECONDS,
    DEFAULT_ROUNDS,
    DEFAULT_SCRIPT,
    FRAMES_FILE,
    NEUTRAL,
    NEUTRAL_INSTRUCTION,
    SCHEMA_VERSION,
    SESSION_FILE,
    Frame,
    Prompt,
    Segmenter,
    build_plan,
    encode,
    read_frames,
    remaining,
)
from .tracker import HandLandmarker, hand_options, hands_from, open_camera

logger = logging.getLogger(__name__)

_WARMUP_FRAMES = 45

_REDRAW_SECONDS = 0.1

_RESUME_GAP = 5.0


def capture_gestures(
    hands: HandTrackingSettings,
    directory: Path,
    *,
    camera: int | str | None = None,
    capture: CaptureSettings | None = None,
    rounds: int = DEFAULT_ROUNDS,
    neutral_seconds: float = DEFAULT_NEUTRAL_SECONDS,
    script: Sequence[tuple[str, str]] = DEFAULT_SCRIPT,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Record a guided session into directory."""
    directory.mkdir(parents=True, exist_ok=True)
    frames_path = directory / FRAMES_FILE

    plan = build_plan([label for label, _ in script], rounds, neutral_seconds=neutral_seconds)
    instructions = {**dict(script), NEUTRAL: NEUTRAL_INSTRUCTION}
    done, resume_at = _already_recorded(frames_path)
    todo = remaining(plan, done)

    if not todo:
        print(f"{frames_path} already holds every take in the plan. Nothing to record.")
        return 0
    if done:
        print(f"Resuming: {len(done)} of {len(plan)} takes already recorded.\n")

    _write_session(directory / SESSION_FILE, hands, script, rounds, neutral_seconds, len(done))

    try:
        device = open_camera(camera, capture or CaptureSettings())
    except Exception as exc:  # noqa: BLE001 - a capture tool reports rather than traces
        print(f"no camera: {exc}", file=sys.stderr)
        return 1

    try:
        with (
            HandLandmarker.create_from_options(hand_options(hands)) as landmarker,
            frames_path.open("a", encoding="utf-8") as sink,
        ):
            session = _Session(
                landmarker=landmarker,
                device=device,
                sink=sink,
                directory=directory,
                instructions=instructions,
                clock_base=resume_at,
                resumed=bool(done),
                total=len(plan),
                already=len(done),
                clock=clock,
            )
            return session.run(todo)
    except KeyboardInterrupt:
        print("\n\nStopped. Re-run the same directory to carry on where this left off.")
        return 0
    finally:
        device.release()


def _already_recorded(path: Path) -> tuple[list[tuple[str, int]], float]:
    """Which takes the file already holds, and the timestamp to carry on from."""
    if not path.is_file():
        return [], 0.0
    seen: dict[tuple[str, int], None] = {}
    last = 0.0
    with path.open(encoding="utf-8") as handle:
        for frame in read_frames(handle):
            last = max(last, frame.at)
            if frame.take:
                seen.setdefault((frame.label, frame.take), None)
    return list(seen), last + _RESUME_GAP


def _write_session(
    path: Path,
    hands: HandTrackingSettings,
    script: Sequence[tuple[str, str]],
    rounds: int,
    neutral_seconds: float,
    already: int,
) -> None:
    """Notes beside the recording: what was asked for, and what scored it at the time."""
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "recorded": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "model": hands.model_path,
                "stride_in_session": hands.stride,
                "rounds": rounds,
                "neutral_seconds": neutral_seconds,
                "script": [list(entry) for entry in script],
                "calibration_at_capture": asdict(hands.calibration),
                "resumed_from": already,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class _Session:
    """One run of the capture loop: prompts in, frames out, a status block in between."""

    def __init__(
        self,
        *,
        landmarker: Any,
        device: cv2.VideoCapture,
        sink: TextIO,
        directory: Path,
        instructions: dict[str, str],
        clock_base: float,
        resumed: bool,
        total: int,
        already: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._landmarker = landmarker
        self._device = device
        self._sink = sink
        self._directory = directory
        self._instructions = instructions
        self._clock_base = clock_base
        self._pending_resume = resumed
        self._total = total
        self._already = already

        self._clock = clock
        self._begun = clock()
        self._last_timestamp_ms = 0
        self._drawn = 0
        self._last_drawn_at = 0.0
        self._buffer: list[Frame] = []
        self._note = ""
        self._spent: list[float] = []

    def run(self, todo: Sequence[Prompt]) -> int:
        """Work through the prompts."""
        print(f"Recording to {self._directory}. Ctrl-C stops; re-run to resume.\n")
        marker, self._pending_resume = self._pending_resume, False
        self._warm_up()
        self._pending_resume = marker

        for index, prompt in enumerate(todo):
            began = self._clock()
            if prompt.timed:
                if not self._timed_take(prompt, index):
                    return 1
            elif not self._gated_take(prompt, index):
                return 1
            self._spent.append(self._clock() - began)

        self._redraw(["", "Done. Every take in the plan is recorded.", ""])
        return 0

    def _warm_up(self) -> None:
        """Run the model on a few dozen frames before asking for anything."""
        for _ in range(_WARMUP_FRAMES):
            frame = self._read()
            if frame is None:
                return
        self._buffer.clear()

    def _gated_take(self, prompt: Prompt, index: int) -> bool:
        """Record one hand-present span, keeping it if long enough."""
        segmenter = Segmenter()
        while True:
            frame = self._read(label=prompt.label)
            if frame is None:
                return False
            closed = segmenter.feed(frame.at, frame.present)
            self._draw(prompt, index, segmenter, frame)
            if closed is None:
                continue
            if closed.kept:
                self._keep(prompt, closed.segment.start, closed.segment.end)
                self._note = _kept_note(prompt, self._buffer, closed.segment.start)
                self._flush()
                return True
            self._note = f"{prompt.label}: too quick ({closed.segment.seconds:.1f}s) -- again"
            self._flush()

    def _timed_take(self, prompt: Prompt, index: int) -> bool:
        """Record a fixed stretch, hand or no hand."""
        start: float | None = None
        while True:
            frame = self._read(label=prompt.label, take=prompt.take)
            if frame is None:
                return False
            start = frame.at if start is None else start
            self._draw(prompt, index, None, frame, left=prompt.seconds - (frame.at - start))
            if frame.at - start >= prompt.seconds:
                self._note = f"{prompt.label}: {prompt.seconds:.0f}s recorded"
                self._flush()
                return True

    def _read(self, *, label: str = "", take: int = 0) -> Frame | None:
        """One landmarked frame, buffered."""
        ok, frame_bgr = self._device.read()
        if not ok:
            print("\nthe camera stopped returning frames", file=sys.stderr)
            return None

        image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB),
        )
        timestamp_ms = max(int(time.monotonic() * 1000), self._last_timestamp_ms + 1)
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(image, timestamp_ms)

        frame = Frame(
            at=self._clock_base + self._clock() - self._begun,
            hands=tuple(hands_from(result)),
            label=label,
            take=take,
            resumed=self._pending_resume,
        )
        self._pending_resume = False
        self._buffer.append(frame)
        return frame

    def _keep(self, prompt: Prompt, start: float, end: float) -> None:
        """Tag the buffered frames inside the span as this take."""
        self._buffer = [
            replace(frame, take=prompt.take) if start <= frame.at <= end else frame
            for frame in self._buffer
        ]

    def _flush(self) -> None:
        """Append the buffer to the recording and clear it."""
        if not self._buffer:
            return
        self._sink.write("".join(f"{encode(frame)}\n" for frame in self._buffer))
        self._sink.flush()
        self._buffer.clear()

    def _draw(
        self,
        prompt: Prompt,
        index: int,
        segmenter: Segmenter | None,
        frame: Frame,
        *,
        left: float | None = None,
    ) -> None:
        now = time.monotonic()
        if now - self._last_drawn_at < _REDRAW_SECONDS:
            return
        self._last_drawn_at = now

        position = self._already + index + 1
        progress = f"take {position}/{self._total}"
        if prompt.round_number:
            progress = f"round {prompt.round_number} - {progress}"
        if (estimate := self._remaining_minutes(index)) is not None:
            progress += f" - ~{estimate:.0f} min left"

        if left is not None:
            state = f"  recording   {left:.0f}s left"
        elif segmenter is not None and segmenter.recording:
            hands = len(frame.hands)
            state = (
                f"  RECORDING   {frame.at - segmenter.started:.1f}s   "
                f"{hands} hand{'s' if hands != 1 else ''} in shot"
            )
        elif segmenter is not None and segmenter.armed:
            state = "  waiting     bring your hand into shot"
        else:
            state = "  settling    hold on, clearing the frame"

        self._redraw(
            [
                progress,
                "",
                f"  NEXT   {prompt.label}",
                f"         {self._instructions.get(prompt.label, '')}",
                "",
                state,
                "",
                f"  last: {self._note}" if self._note else "",
            ]
        )

    def _remaining_minutes(self, index: int) -> float | None:
        """An estimate of the time left, from the takes so far."""
        if len(self._spent) < 3:
            return None
        typical = sorted(self._spent)[len(self._spent) // 2]
        return typical * (self._total - self._already - index - 1) / 60.0

    def _redraw(self, lines: Sequence[str]) -> None:
        """Overwrite the last block in place."""
        if self._drawn:
            sys.stdout.write(f"\033[{self._drawn}A\033[J")
        sys.stdout.write("".join(f"{line}\n" for line in lines))
        sys.stdout.flush()
        self._drawn = len(lines)


def _kept_note(prompt: Prompt, buffered: Sequence[Frame], start: float) -> str:
    """What to say about a take that was kept."""
    inside = [frame for frame in buffered if frame.at >= start and frame.take == prompt.take]
    if not inside:
        return f"{prompt.label} take {prompt.take}: kept"
    seconds = inside[-1].at - inside[0].at
    note = f"{prompt.label} take {prompt.take}: kept, {seconds:.1f}s"
    if "pinch" in prompt.label and (cycles := _pinch_cycles(inside)) is not None:
        note += f", {cycles} cycle{'s' if cycles != 1 else ''}"
    return note


def _pinch_cycles(frames: Sequence[Frame]) -> int | None:
    """How many times the thumb closed onto the index, from the raw gap alone."""
    gaps = [
        raw.pinch_index
        for frame in frames
        for hand in frame.hands[:1]
        if (raw := ratios(hand)) is not None
    ]
    if len(gaps) < 5:
        return None
    midpoint = (min(gaps) + max(gaps)) / 2.0
    if max(gaps) - min(gaps) < 0.1:
        return 0
    closed = [gap < midpoint for gap in gaps]
    return sum(1 for before, after in zip(closed, closed[1:], strict=False) if after and not before)
