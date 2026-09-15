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

from ..settings import HeadTrackingSettings
from .expressions import (
    DEFAULT_ROUNDS,
    DEFAULT_SCRIPT,
    FACE_KIND,
    GATES,
    PASSES,
    REST,
    SCHEMA_VERSION,
    SKIP_AFTER,
    SKIP_WARNING,
    Baseline,
    FaceFrame,
    Gate,
    Pass,
    Rest,
    attempting,
    build_plan,
    encode,
    gate,
    instructions,
    read_frames,
    rejection,
    segmenter,
    still_owed,
)
from .gestures import FACE_FEATURES
from .takes import FRAMES_FILE, SESSION_FILE, Prompt
from .tracker import FaceLandmarker, blendshapes_from, extract_yaw_pitch, face_options, open_camera

logger = logging.getLogger(__name__)

_WARMUP_FRAMES = 45

_REDRAW_SECONDS = 0.1

_RESUME_GAP = 5.0

_SKIPPED_FILE = "skipped.json"


def capture_expressions(
    settings: HeadTrackingSettings,
    directory: Path,
    *,
    rounds: int = DEFAULT_ROUNDS,
    script: Sequence[tuple[str, str]] = DEFAULT_SCRIPT,
    passes: Sequence[Pass] = PASSES,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Record a guided session into directory."""
    directory.mkdir(parents=True, exist_ok=True)
    frames_path = directory / FRAMES_FILE

    plan = build_plan([label for label, _ in script], rounds, passes=passes)
    done, skipped, baseline, resume_at = _already_recorded(frames_path, directory)
    todo = still_owed(plan, done, skipped)

    if not todo:
        print(f"{frames_path} already holds every take in the plan. Nothing to record.")
        return 0
    if done or skipped:
        print(f"Resuming: {len(done)} of {len(plan)} takes recorded", end="")
        print(f", {', '.join(sorted(skipped))} skipped.\n" if skipped else ".\n")
    if baseline.measured:
        print(f"Idle baseline re-read from {baseline.frames} frames of the rest pass.\n")

    _write_session(directory / SESSION_FILE, settings, script, passes, rounds, len(done), baseline)

    try:
        device = open_camera(settings.camera, settings.capture)
    except Exception as exc:  # noqa: BLE001 - a capture tool reports rather than traces
        print(f"no camera: {exc}", file=sys.stderr)
        return 1

    try:
        with (
            FaceLandmarker.create_from_options(face_options(settings)) as landmarker,
            frames_path.open("a", encoding="utf-8") as sink,
        ):
            session = _Session(
                landmarker=landmarker,
                device=device,
                sink=sink,
                directory=directory,
                settings=settings,
                script=script,
                passes=passes,
                rounds=rounds,
                instructions=instructions(script, passes),
                baseline=baseline,
                skipped=set(skipped),
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


def _already_recorded(
    path: Path, directory: Path
) -> tuple[list[tuple[str, int]], tuple[str, ...], Baseline, float]:
    """What the recording already holds: its takes, its skips, its baseline, its clock."""
    skipped = _read_skipped(directory)
    if not path.is_file():
        return [], skipped, Baseline(), 0.0

    seen: dict[tuple[str, int], None] = {}
    rest: list[FaceFrame] = []
    last = 0.0
    with path.open(encoding="utf-8") as handle:
        for frame in read_frames(handle):
            last = max(last, frame.at)
            if frame.take:
                seen.setdefault((frame.label, frame.take), None)
            if frame.label == REST and frame.take:
                rest.append(frame)
    return list(seen), skipped, Baseline.measure(rest), last + _RESUME_GAP


def _read_skipped(directory: Path) -> tuple[str, ...]:
    """The performances a previous run gave up on, so a resume does not ask again."""
    try:
        payload = json.loads((directory / _SKIPPED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    return tuple(str(label) for label in payload) if isinstance(payload, list) else ()


def _write_session(
    path: Path,
    settings: HeadTrackingSettings,
    script: Sequence[tuple[str, str]],
    passes: Sequence[Pass],
    rounds: int,
    already: int,
    baseline: Baseline,
) -> None:
    """Notes beside the recording: what was asked for, and what the model was."""
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA_VERSION,
                "kind": FACE_KIND,
                "recorded": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "model": settings.model_path,
                "min_detection_confidence": settings.min_detection_confidence,
                "min_tracking_confidence": settings.min_tracking_confidence,
                "rounds": rounds,
                "script": [list(entry) for entry in script],
                "passes": [asdict(one) for one in passes],
                "features": list(FACE_FEATURES),
                "gates": {label: list(features) for label, features in GATES.items()},
                "baseline": dict(sorted(baseline.levels.items())),
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
        settings: HeadTrackingSettings,
        script: Sequence[tuple[str, str]],
        passes: Sequence[Pass],
        rounds: int,
        instructions: dict[str, str],
        baseline: Baseline,
        skipped: set[str],
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
        self._settings = settings
        self._script = script
        self._passes = passes
        self._rounds = rounds
        self._instructions = instructions
        self._baseline = baseline
        self._skipped = skipped
        self._clock_base = clock_base
        self._pending_resume = resumed
        self._total = total
        self._already = already

        self._clock = clock
        self._begun = clock()
        self._last_timestamp_ms = 0
        self._drawn = 0
        self._last_drawn_at = 0.0
        self._buffer: list[FaceFrame] = []
        self._note = ""
        self._spent: list[float] = []

    def run(self, todo: Sequence[Prompt]) -> int:
        """Work through the prompts."""
        print(f"Recording to {self._directory}. Ctrl-C stops; re-run to resume.\n")
        marker, self._pending_resume = self._pending_resume, False
        self._warm_up()
        self._pending_resume = marker

        for index, prompt in enumerate(todo):
            if prompt.label in self._skipped:
                continue
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
            if self._read() is None:
                return
        self._buffer.clear()

    def _timed_take(self, prompt: Prompt, index: int) -> bool:
        """Record a fixed stretch."""
        start: float | None = None
        while True:
            frame = self._read(label=prompt.label, take=prompt.take)
            if frame is None:
                return False
            start = frame.at if start is None else start
            self._draw(prompt, index, frame, left=prompt.seconds - (frame.at - start))
            if frame.at - start >= prompt.seconds:
                if prompt.label == REST:
                    self._adopt_baseline()
                self._note = f"{prompt.label}: {prompt.seconds:.0f}s recorded"
                self._flush()
                return True

    def _adopt_baseline(self) -> None:
        """Measure the idle levels off the rest pass just recorded, and store them."""
        self._baseline = Baseline.measure(
            frame for frame in self._buffer if frame.label == REST and frame.take
        )
        _write_session(
            self._directory / SESSION_FILE,
            self._settings,
            self._script,
            self._passes,
            self._rounds,
            self._already,
            self._baseline,
        )

    def _gated_take(self, prompt: Prompt, index: int) -> bool:
        """Wait for the expression, record until the face goes back to rest, keep the span."""
        if not self._baseline.measured:
            print(
                "\nno rest pass in the recording, so there is nothing to gate against. "
                "Record the rest pass first.",
                file=sys.stderr,
            )
            return False

        spans = segmenter()
        rest = Rest()
        features = GATES.get(prompt.label, ())
        while True:
            frame = self._read(label=prompt.label)
            if frame is None:
                return False
            readings = gate(frame.scores, self._baseline, features)
            active = attempting(readings)
            closed = spans.feed(frame.at, active)
            rest.feed(frame.at, present=frame.present, active=active)
            self._draw(prompt, index, frame, spans=spans, readings=readings, rest=rest)

            if closed is None:
                if not spans.recording and rest.held(frame.at) >= SKIP_AFTER:
                    self._skip(prompt)
                    return True
                continue
            if closed.kept:
                self._keep(prompt, closed.segment.start, closed.segment.end)
                self._note = _kept_note(prompt, self._buffer, closed.segment.start)
                self._flush()
                return True
            self._note = rejection(prompt, closed.segment.seconds) or self._note
            self._flush()
            rest.reset()

    def _skip(self, prompt: Prompt) -> None:
        """Give up on a performance, and on every remaining take of it."""
        self._skipped.add(prompt.label)
        (self._directory / _SKIPPED_FILE).write_text(
            json.dumps(sorted(self._skipped), indent=2) + "\n", encoding="utf-8"
        )
        self._note = f"{prompt.label}: skipped -- no more takes of it will be asked for"
        self._flush()

    def _read(self, *, label: str = "", take: int = 0) -> FaceFrame | None:
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

        matrices = result.facial_transformation_matrixes
        yaw, pitch = extract_yaw_pitch(matrices[0]) if matrices else (0.0, 0.0)
        frame = FaceFrame(
            at=self._clock_base + self._clock() - self._begun,
            scores=blendshapes_from(result),
            yaw=yaw,
            pitch=pitch,
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
        frame: FaceFrame,
        *,
        spans: Any = None,
        readings: Sequence[Gate] = (),
        rest: Rest | None = None,
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

        self._redraw(
            [
                progress,
                "",
                f"  NEXT   {prompt.label}",
                f"         {self._instructions.get(prompt.label, '')}",
                "",
                self._state(frame, spans, rest, left),
                *(f"  gate   {_gate_line(reading)}" for reading in readings),
                "",
                f"  last: {self._note}" if self._note else "",
            ]
        )

    def _state(self, frame: FaceFrame, spans: Any, rest: Rest | None, left: float | None) -> str:
        """The one line saying what the session thinks is happening."""
        if left is not None:
            return f"  recording   {left:.0f}s left"
        if not frame.present:
            return "  no face     nothing in shot -- waiting, for as long as it takes"
        if spans is not None and spans.recording:
            return f"  RECORDING   {frame.at - spans.started:.1f}s"
        if spans is not None and not spans.armed:
            return "  settling    let your face go for a moment"
        held = rest.held(frame.at) if rest is not None else 0.0
        if held >= SKIP_AFTER - SKIP_WARNING:
            return f"  waiting     skipping this one in {SKIP_AFTER - held:.0f}s"
        return "  waiting     make the expression when you are ready"

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


def _gate_line(reading: Gate) -> str:
    """One gate feature, its live value, and the level that would open it."""
    score = "--" if reading.score is None else f"{reading.score:.2f}"
    mark = " OPEN" if reading.open else ""
    return f"{reading.feature:<20} {score:>5}  opens at {reading.opens_at:.2f}{mark}"


def _kept_note(prompt: Prompt, buffered: Sequence[FaceFrame], start: float) -> str:
    """What to say about a take that was kept."""
    inside = [frame for frame in buffered if frame.at >= start and frame.take == prompt.take]
    if not inside:
        return f"{prompt.label} take {prompt.take}: kept"
    seconds = inside[-1].at - inside[0].at
    lost = sum(1 for frame in inside if not frame.present)
    note = f"{prompt.label} take {prompt.take}: kept, {seconds:.1f}s"
    return note + (f", {lost} frames with no face" if lost else "")
