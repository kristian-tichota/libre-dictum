from __future__ import annotations

import json
import logging
import sys
import textwrap
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..overlay import protocol
from ..overlay.service import HudService
from ..settings import AppSettings, HeadTrackingSettings
from .screenmap import (
    DEFAULT_SIDE,
    INSET,
    MAX_ANGLE_DEGREES,
    MAX_SIDE,
    TRAVEL_DEGREES,
    Sample,
    ScreenMap,
    Settling,
    grid_of,
)
from .tracker import FaceLandmarker, extract_yaw_pitch, face_options, open_camera

logger = logging.getLogger(__name__)

GIVE_UP_SECONDS = 20.0

BETWEEN_DOTS = 0.6

LEAD_IN = 2.5

IMPLAUSIBLE = MAX_ANGLE_DEGREES

LOOSE_PX = 90.0

REPORT_SCREEN = (1920, 1080)

DISPLAY_TIMEOUT = 5.0


def calibrate_screen(
    settings: AppSettings,
    *,
    side: int = DEFAULT_SIDE,
    socket_path: Path | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Run the dot session and print the configuration it measured."""
    try:
        grid = grid_of(side)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    head_tracking = settings.head_tracking
    if head_tracking is None:
        print("head tracking is off, so there is no pose to calibrate", file=sys.stderr)
        return 1

    service = HudService(socket_path or protocol.default_socket_path())
    try:
        service.show()
    except OSError as exc:
        print(
            f"cannot reach the display socket ({exc}). A running session already holds it; "
            "stop it first, then start 'libre-dictum-hud' beside this.",
            file=sys.stderr,
        )
        return 1

    if not _wait_for_display(service):
        print(
            "no display connected, so there would be no dots to look at. Start "
            "'libre-dictum-hud' and run this again.",
            file=sys.stderr,
        )
        service.hide()
        return 1

    try:
        device = open_camera(head_tracking.camera, head_tracking.capture)
    except Exception as exc:  # noqa: BLE001 - a capture tool reports rather than traces
        print(f"no camera: {exc}", file=sys.stderr)
        service.hide()
        return 1

    print(
        f"Look at each of the {len(grid)} dots in turn and hold still.\n"
        "Point your NOSE at it, not just your eyes -- pointing the head is the thing being\n"
        "measured. Ctrl-C stops.\n"
    )
    try:
        with FaceLandmarker.create_from_options(face_options(head_tracking)) as landmarker:
            samples = _run(landmarker, device, service, grid, clock, head_tracking)
    except KeyboardInterrupt:
        print("\nStopped. Nothing was written.")
        return 0
    finally:
        device.release()
        service.hide()

    return _report(samples, len(grid), side)


def _run(
    landmarker: Any,
    device: Any,
    service: HudService,
    grid: Sequence[tuple[float, float]],
    clock: Callable[[], float],
    settings: HeadTrackingSettings,
) -> list[Sample]:
    """One pass over the dots, returning the ones that settled."""
    import cv2  # noqa: PLC0415 - the camera extra, imported where the camera is
    import mediapipe as mp  # noqa: PLC0415

    samples: list[Sample] = []
    for index, (x, y) in enumerate(grid, start=1):
        first = index == 1
        settling = Settling(
            travel=0.0 if first else TRAVEL_DEGREES,
            min_cutoff_hz=settings.filter_min_cutoff,
            beta=settings.filter_beta,
        )
        _publish(service, x, y, index, len(grid), 0.0)
        if first:
            _drain(device, clock, LEAD_IN)

        started = clock()
        translation = (0.0, 0.0, 0.0)
        recorded = False

        while clock() - started < GIVE_UP_SECONDS:
            ok, frame = device.read()
            if not ok:
                continue
            image = mp.Image(
                image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            )
            result = landmarker.detect_for_video(image, int(clock() * 1000))
            pose = _pose_of(result)
            now = clock()

            if pose is None:
                settling.reset()
                _publish(service, x, y, index, len(grid), 0.0)
                continue

            yaw, pitch, translation = pose
            held = settling.feed(now, yaw, pitch)
            _publish(service, x, y, index, len(grid), min(1.0, held / settling.hold))
            if held >= settling.hold:
                recorded = True
                break

        held_pose = settling.pose
        if recorded and held_pose is not None:
            yaw, pitch = held_pose
            samples.append(Sample(x=x, y=y, yaw=yaw, pitch=pitch, translation=translation))
            print(f"  dot {index} of {len(grid)}  yaw {yaw:6.1f}°  pitch {pitch:6.1f}°")
            _publish(service, x, y, index, len(grid), 1.0)
        else:
            print(f"  dot {index} of {len(grid)}  -- never settled, skipped")
            _publish(service, x, y, index, len(grid), 0.0)

        _drain(device, clock, BETWEEN_DOTS)

    service.set_dot(None)
    return samples


def _pose_of(result: Any) -> tuple[float, float, tuple[float, float, float]] | None:
    """Yaw, pitch and head position from one landmarker result, or None."""
    if not result.face_landmarks or not result.facial_transformation_matrixes:
        return None
    matrix = result.facial_transformation_matrixes[0]
    yaw, pitch = extract_yaw_pitch(matrix)
    if abs(yaw) > IMPLAUSIBLE or abs(pitch) > IMPLAUSIBLE:
        return None
    flat = [float(v) for v in getattr(matrix, "flatten", lambda: matrix)()]
    translation = (flat[3], flat[7], flat[11]) if len(flat) >= 12 else (0.0, 0.0, 0.0)
    return yaw, pitch, translation


def _publish(
    service: HudService, x: float, y: float, index: int, total: int, settled: float
) -> None:
    service.set_dot(protocol.CalibrationDot(x=x, y=y, index=index, total=total, settled=settled))


def _wait_for_display(service: HudService, timeout: float = DISPLAY_TIMEOUT) -> bool:
    """Whether something is on the other end of the socket to draw the dots."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service.connected:
            return True
        time.sleep(0.05)
    return service.connected


def _drain(device: Any, clock: Callable[[], float], seconds: float) -> None:
    """Read and discard frames for the given number of seconds."""
    until = clock() + seconds
    while clock() < until:
        if not device.read()[0]:
            time.sleep(0.01)


def _report(samples: Sequence[Sample], asked: int, side: int) -> int:
    """Fit the settled samples, then print the mapping and the block to paste."""
    print()
    try:
        mapping = ScreenMap.fit(samples)
    except ValueError as exc:
        print(f"cannot fit a mapping: {exc}", file=sys.stderr)
        return 1

    width, height = REPORT_SCREEN
    expected = mapping.expected_error_px(width, height)
    print(f"Fitted from {len(samples)} of {asked} dots.\n")
    _say(
        f"Residual {mapping.residual_px(width, height):.0f} px on {width}x{height} -- the "
        f"error on the {len(samples)} dots the fit was fitted to. The mapping itself should "
        f"land within about {expected:.0f} px, which is the number that matters: eight "
        f"coefficients from {len(samples)} dots leaves {2 * len(samples) - 8} degrees of "
        "freedom, so the residual counts noise the fit has already absorbed."
    )
    _say_what_the_head_covered(samples, width, height)

    if expected > LOOSE_PX:
        _say(
            "That is loose. The usual causes are looking at a dot with the eyes rather than "
            "the head, and moving between settling and the next dot. Worth running again."
        )
    if side < MAX_SIDE:
        _say(
            f"More dots would help: --calibrate-screen {side + 1} fits the same eight "
            f"coefficients from {(side + 1) ** 2} dots rather than {side**2}. Expect the "
            "residual itself to go *up* -- more dots is more noise sampled, spread over the "
            "same eight coefficients, which is the whole reason it is not the number above."
        )

    _say(
        "If you have more than one monitor, this needs 'ht_screen_bounds' beside it. The dots "
        "were drawn on one screen and the pointer is spread across all of them, so without it "
        "a mapping this good lands on the wrong one. Nothing here can see your monitors -- "
        "docs/reference/configuration.md#two-monitors says where to read them off."
    )
    print("Paste this into config.json:\n")
    print(json.dumps({"ht_screen_calibration": mapping.as_config()}, indent=2))
    return 0


def _say(text: str) -> None:
    """One wrapped paragraph."""
    print(textwrap.fill(text, width=78) + "\n")


def _say_what_the_head_covered(samples: Sequence[Sample], width: int, height: int) -> None:
    """Print the angle the dots covered and the pixels a degree it yields."""
    across = _spread(samples, lambda s: (s.x, s.yaw))
    down = _spread(samples, lambda s: (s.y, s.pitch))
    if across <= 0 or down <= 0:
        return

    covered = 1.0 - 2 * INSET
    per_degree = (covered * width / across, covered * height / down)
    tight = "across" if per_degree[0] > per_degree[1] else "up and down"
    _say(
        f"Your head covered {across:.0f}° across and {down:.0f}° down over those dots, which "
        f"is {per_degree[0]:.0f} px a degree across and {per_degree[1]:.0f} px a degree down. "
        f"The narrow axis sets the floor, so pointing further {tight} with the head rather "
        "than the eyes gives more range than any other change here."
    )


def _spread(samples: Sequence[Sample], of: Callable[[Sample], tuple[float, float]]) -> float:
    """Degrees the head covered on one axis, by row and column means."""
    lines: dict[float, list[float]] = {}
    for sample in samples:
        where, angle = of(sample)
        lines.setdefault(where, []).append(angle)
    means = [sum(angles) / len(angles) for angles in lines.values()]
    return max(means) - min(means) if len(means) > 1 else 0.0
