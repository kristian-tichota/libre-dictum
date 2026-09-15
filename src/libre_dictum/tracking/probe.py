from __future__ import annotations

import logging
import sys
import time

import cv2
import mediapipe as mp

from ..settings import CaptureSettings, HandTrackingSettings
from .hands import PrimaryHand, report
from .tracker import HandLandmarker, hand_options, hands_from, open_camera

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 0.1


def watch_hands(
    hands: HandTrackingSettings,
    *,
    camera: int | str | None = None,
    capture: CaptureSettings | None = None,
) -> int:
    """Print hand scores until interrupted."""
    print(f"Reading hands from {hands.model_path}. Ctrl-C to stop.\n")
    try:
        capture_device = open_camera(camera, capture or CaptureSettings())
    except Exception as exc:  # noqa: BLE001 - a probe reports rather than traces
        print(f"no camera: {exc}", file=sys.stderr)
        return 1

    primary = PrimaryHand()
    last_timestamp_ms = 0
    last_drawn_at = 0.0
    drawn = 0
    try:
        with HandLandmarker.create_from_options(hand_options(hands)) as landmarker:
            while True:
                ok, frame_bgr = capture_device.read()
                if not ok:
                    print("the camera stopped returning frames", file=sys.stderr)
                    return 1

                image = mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB),
                )
                timestamp_ms = max(int(time.monotonic() * 1000), last_timestamp_ms + 1)
                last_timestamp_ms = timestamp_ms

                result = landmarker.detect_for_video(image, timestamp_ms)
                hands_seen = primary.order(hands_from(result))

                now = time.monotonic()
                if now - last_drawn_at >= _INTERVAL_SECONDS:
                    drawn = _redraw(report(hands_seen, calibration=hands.calibration), drawn)
                    last_drawn_at = now
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        capture_device.release()


def _redraw(block: str, previous_lines: int) -> int:
    """Overwrite the last block in place, and say how many lines this one took."""
    lines = block.splitlines() or [""]
    if previous_lines:
        sys.stdout.write(f"\033[{previous_lines}A\033[J")
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    sys.stdout.flush()
    return len(lines)
