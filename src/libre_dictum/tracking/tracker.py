from __future__ import annotations

import logging
import math
import sys
import threading
import time
from collections.abc import Callable, Sequence

import cv2
import mediapipe as mp
import numpy as np

from ..errors import CameraError
from ..meters import GestureMeter, HandReading
from ..settings import CaptureSettings, HandTrackingSettings, HeadTrackingSettings
from ..smoothing import Neutral, OneEuroFilter
from .cameras import candidates, describe
from .expressions import normalised
from .gestures import GestureRecognizer
from .hands import Hand, PrimaryHand, hand_readings, user_side
from .hands import scores as hand_scores
from .v4l2 import enumerate_cameras, resolve

logger = logging.getLogger(__name__)

BaseOptions = mp.tasks.BaseOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
RunningMode = mp.tasks.vision.RunningMode

_DEBUG_INTERVAL_SECONDS = 0.1

METER_INTERVAL_SECONDS = 0.1

RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0)

FRAME_TIMEOUT_SECONDS = 2.0

_READ_RETRY_SECONDS = 0.05

MAX_FRAME_INTERVAL = 0.1


def _fourcc_name(code: float) -> str:
    """Decode OpenCV's numeric FOURCC back into its four characters."""
    value = int(code)
    return "".join(chr((value >> shift) & 0xFF) for shift in (0, 8, 16, 24)).strip()


def _describe_capture(fourcc: str, width: float, height: float, fps: float) -> str:
    return f"{fourcc or 'default'}/{int(width)}x{int(height)}/{fps:.0f}fps"


def _apply_capture_settings(capture: cv2.VideoCapture, settings: CaptureSettings) -> None:
    """Request capture properties, leaving any unset one at the driver's default."""
    if settings.fourcc:
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*settings.fourcc))
    if settings.width:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, settings.width)
    if settings.height:
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.height)
    if settings.fps:
        capture.set(cv2.CAP_PROP_FPS, settings.fps)

    accepted = _describe_capture(
        _fourcc_name(capture.get(cv2.CAP_PROP_FOURCC)),
        capture.get(cv2.CAP_PROP_FRAME_WIDTH),
        capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
        capture.get(cv2.CAP_PROP_FPS),
    )
    requested = _describe_capture(
        settings.fourcc or "",
        settings.width or capture.get(cv2.CAP_PROP_FRAME_WIDTH),
        settings.height or capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
        settings.fps or capture.get(cv2.CAP_PROP_FPS),
    )

    if accepted == requested:
        logger.info("Camera capturing at %s", accepted)
    else:
        logger.warning("Camera rejected %s; it is capturing at %s instead", requested, accepted)


def extract_yaw_pitch(transformation_matrix: np.ndarray) -> tuple[float, float]:
    """Yaw and pitch in degrees from a facial transformation matrix."""
    matrix = np.array(transformation_matrix, dtype=np.float64).reshape(4, 4)
    forward = matrix[:3, :3] @ np.array([0.0, 0.0, 1.0], dtype=np.float64)

    yaw = math.degrees(math.atan2(forward[0], forward[2]))
    pitch = math.degrees(math.atan2(-forward[1], math.hypot(forward[0], forward[2])))
    return yaw, pitch


def open_camera(camera: int | str | None, capture_settings: CaptureSettings) -> cv2.VideoCapture:
    """Open the configured camera, or whichever video node hands over a frame."""
    nodes = enumerate_cameras()
    refused: list[str] = []

    for node in candidates(nodes, resolve(camera)):
        capture = cv2.VideoCapture(node.index, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            refused.append(f"{node.describe()} would not open")
            continue

        _apply_capture_settings(capture, capture_settings)
        if not capture.read()[0]:
            capture.release()
            refused.append(f"{node.describe()} opened but delivered no frame")
            continue

        logger.info("Camera %s", node.describe())
        return capture

    raise CameraError(
        f"no camera would deliver a frame: {'; '.join(refused)}. Check that nothing "
        f"else holds the device. Video devices present: {describe(nodes)}"
    )


def face_options(settings: HeadTrackingSettings):  # noqa: ANN201 - mediapipe options type
    """The face landmarker a session runs and a recording is made against."""
    return FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=settings.model_path),
        running_mode=RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=settings.min_detection_confidence,
        min_tracking_confidence=settings.min_tracking_confidence,
        output_facial_transformation_matrixes=True,
        output_face_blendshapes=True,
    )


def blendshapes_from(result) -> dict[str, float]:  # noqa: ANN001 - mediapipe result type
    """Mediapipe's blendshape result as a plain mapping, empty when no face was found."""
    if not result.face_blendshapes:
        return {}
    categories = result.face_blendshapes[0]
    return {category.category_name: category.score for category in categories}


def hand_options(settings: HandTrackingSettings):  # noqa: ANN201 - mediapipe options type
    return HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=settings.model_path),
        running_mode=RunningMode.VIDEO,
        num_hands=settings.max_hands,
        min_hand_detection_confidence=settings.min_detection_confidence,
        min_hand_presence_confidence=settings.min_presence_confidence,
        min_tracking_confidence=settings.min_tracking_confidence,
    )


def hands_from(result) -> list[Hand]:  # noqa: ANN001 - mediapipe result type
    """Mediapipe's hand result as Hand objects, with the sides put right."""
    handedness = list(result.handedness)
    hands = []
    for index, landmarks in enumerate(result.hand_landmarks):
        labels = handedness[index] if index < len(handedness) else ()
        label = labels[0].category_name if labels else ""
        hands.append(
            Hand(
                landmarks=[(point.x, point.y, point.z) for point in landmarks],
                side=user_side(label),
            )
        )
    return hands


class FaceRotationTracker:
    """Feeds head rotation and gesture events to callbacks from its own thread."""

    def __init__(
        self,
        settings: HeadTrackingSettings,
        *,
        rotation_callback: Callable[[float, float, float, float, float], None],
        gesture_callback: Callable[[str, bool], None] | None = None,
        held_callback: Callable[[tuple[str, ...]], None] | None = None,
        meter_callback: (
            Callable[[tuple[GestureMeter, ...], tuple[HandReading, ...]], None] | None
        ) = None,
        wants_meters: Callable[[], bool] = lambda: False,
        retry_delays: Sequence[float] = RETRY_DELAYS,
    ) -> None:
        self.settings = settings
        self.rotation_callback = rotation_callback
        self.gesture_callback = gesture_callback
        self.held_callback = held_callback
        self.meter_callback = meter_callback
        self.wants_meters = wants_meters
        self.gestures = GestureRecognizer(settings.gesture_definitions)
        self.face_baseline = dict(settings.face_baseline)
        self.failure: BaseException | None = None
        self.hand_failure: BaseException | None = None

        self.neutral = Neutral(settings.offset_x, settings.offset_y)
        self._smoothing = OneEuroFilter(settings.filter_min_cutoff, settings.filter_beta)
        self._pose: tuple[float, float] | None = None
        self._last_pose_at: float | None = None

        self._primary = PrimaryHand()
        self._hand_scores: dict[str, float] = {}
        self._hands: tuple[HandReading, ...] = ()
        self._frames_seen = 0

        self._retry_delays = tuple(retry_delays)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_debug = 0.0
        self._last_meters = 0.0
        self._held: tuple[str, ...] = ()

    def recenter(self) -> None:
        """Take the head's current pose as neutral."""
        pose = self._pose
        if pose is None:
            logger.info("recenter(): no face seen yet, so neutral is unchanged")
            return
        self.neutral.recenter(*pose)
        logger.info("Neutral is now yaw %.1f, pitch %.1f", *pose)

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.failure = None
        self._thread = threading.Thread(target=self._run, name="head-tracking", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "Head tracking thread is still running after %.0fs; abandoning it", timeout
                )
        self._thread = None

    @property
    def running(self) -> bool:
        """Whether the tracking thread is alive and has not been asked to stop."""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        """Track until asked to stop, retrying a camera that may yet come back."""
        attempts = len(self._retry_delays) + 1
        for attempt, delay in enumerate((0.0, *self._retry_delays), start=1):
            if self._stop.wait(delay):
                return

            try:
                self._track()
                return
            except CameraError as exc:
                self.failure = exc
                logger.error("Head tracking: %s (attempt %d of %d)", exc, attempt, attempts)
            except BaseException as exc:  # noqa: BLE001 - a dead camera is not fatal
                self.failure = exc
                logger.exception("Head tracking stopped; the pointer will not move")
                return

        logger.error(
            "Head tracking gave up after %d attempts; say the reload phrase to try again",
            attempts,
        )

    def _track(self) -> None:
        capture = self._open_camera()
        try:
            self._pump_frames(capture)
        finally:
            capture.release()

    def _open_camera(self) -> cv2.VideoCapture:
        return open_camera(self.settings.camera, self.settings.capture)

    def _pump_frames(self, capture: cv2.VideoCapture) -> None:
        options = face_options(self.settings)

        last_timestamp_ms = 0
        last_frame = time.monotonic()
        with FaceLandmarker.create_from_options(options) as landmarker:
            hands = self._open_hand_landmarker()
            try:
                while not self._stop.is_set():
                    ok, frame_bgr = capture.read()
                    if not ok:
                        if time.monotonic() - last_frame > FRAME_TIMEOUT_SECONDS:
                            raise CameraError("the camera stopped returning frames")
                        self._stop.wait(_READ_RETRY_SECONDS)
                        continue

                    last_frame = time.monotonic()
                    if self.failure is not None:
                        logger.info("Head tracking recovered; the pointer is live again")
                        self.failure = None

                    mp_image = mp.Image(
                        image_format=mp.ImageFormat.SRGB,
                        data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB),
                    )
                    # Mediapipe requires strictly increasing timestamps.
                    timestamp_ms = max(int(time.monotonic() * 1000), last_timestamp_ms + 1)
                    last_timestamp_ms = timestamp_ms

                    if hands is not None and not self._read_hands(hands, mp_image, timestamp_ms):
                        hands.close()
                        hands = None
                    self._frames_seen += 1

                    self._handle_result(landmarker.detect_for_video(mp_image, timestamp_ms))
            finally:
                if hands is not None:
                    hands.close()

    def _open_hand_landmarker(self):  # noqa: ANN202 - mediapipe landmarker type
        """The hand model, or None when hands are off or the model would not load."""
        settings = self.settings.hands
        if settings is None:
            return None
        try:
            return HandLandmarker.create_from_options(hand_options(settings))
        except BaseException as exc:  # noqa: BLE001 - a dead hand model is not fatal
            self.hand_failure = exc
            logger.exception(
                "Hand scores are off: %s would not load. Head tracking is unaffected",
                settings.model_path,
            )
            return None

    def _read_hands(self, landmarker, image, timestamp_ms: int) -> bool:  # noqa: ANN001
        """Score the hands in one frame, on the strided frames."""
        settings = self.settings.hands
        if settings is None or self._frames_seen % settings.stride != 0:
            return True

        try:
            result = landmarker.detect_for_video(image, timestamp_ms)
        except BaseException as exc:  # noqa: BLE001 - a dead hand model is not fatal
            self.hand_failure = exc
            self._hand_scores = {}
            self._hands = ()
            self._primary.reset()
            logger.exception("Hand scores stopped; facial gestures and the pointer carry on")
            return False

        ordered = self._primary.order(hands_from(result))
        self._hand_scores = hand_scores(ordered, calibration=settings.calibration)
        self._hands = hand_readings(ordered)
        return True

    def _handle_result(self, result) -> None:  # noqa: ANN001 - mediapipe result type
        if result.face_landmarks and result.facial_transformation_matrixes:
            self._report_rotation(*extract_yaw_pitch(result.facial_transformation_matrixes[0]))

        blendshapes = result.face_blendshapes[0] if result.face_blendshapes else ()
        self._handle_gestures(blendshapes)

    def _report_rotation(self, raw_yaw: float, raw_pitch: float) -> None:
        """Smooth the pose, measure the interval, and report the offset from neutral."""
        now = time.monotonic()
        previous = self._last_pose_at
        self._last_pose_at = now

        dt = 0.0 if previous is None else now - previous
        if dt > MAX_FRAME_INTERVAL:
            self._smoothing.reset()
            dt = 0.0

        yaw, pitch = self._smoothing.filter(raw_yaw, raw_pitch, dt)
        self._pose = (yaw, pitch)
        self.rotation_callback(*self.neutral.relative(yaw, pitch), dt, yaw, pitch)

    def _handle_gestures(self, blendshapes) -> None:  # noqa: ANN001 - mediapipe result type
        """One mapping, one recognizer."""
        categories = getattr(blendshapes, "categories", blendshapes)
        scores = normalised(
            {category.category_name: category.score for category in categories},
            self.face_baseline,
        )
        scores.update(self._hand_scores)

        self._write_debug_line(scores)
        fired, released = self.gestures.update(scores, time.monotonic())
        if self.gesture_callback is not None:
            for gesture in released:
                logger.debug("Gesture released: %s", gesture)
                self.gesture_callback(gesture, False)
            for gesture in fired:
                logger.debug("Gesture fired: %s", gesture)
                self.gesture_callback(gesture, True)

        self._report_held()
        self._report_meters(scores)

    def _report_held(self) -> None:
        """Say which gestures are down, whenever that changes."""
        held = tuple(sorted(self.gestures.active))
        if held == self._held or self.held_callback is None:
            return
        self._held = held
        self.held_callback(held)

    def _report_meters(self, scores: dict[str, float]) -> None:
        """One frame of live readings, if anything is drawing them."""
        if self.meter_callback is None or not self.wants_meters():
            return
        now = time.monotonic()
        if now - self._last_meters < METER_INTERVAL_SECONDS:
            return
        self._last_meters = now
        self.meter_callback(self.gestures.meters(scores), self._hands)

    def _write_debug_line(self, scores: dict[str, float]) -> None:
        """Rewrite one terminal line with live scores for the watched gestures."""
        if not self.settings.debug_gestures or not sys.stdout.isatty():
            return
        now = time.monotonic()
        if now - self._last_debug < _DEBUG_INTERVAL_SECONDS:
            return

        line = self.gestures.describe(scores, self.settings.debug_gestures)
        if line:
            sys.stdout.write("\r\033[K" + line)
            sys.stdout.flush()
            self._last_debug = now
