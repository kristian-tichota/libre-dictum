from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__
from .app import DEFAULT_CONFIG_DIR, Application
from .errors import AlreadyRunningError, CameraError, ConfigError, DeviceError
from .overlay.protocol import SOCKET_ENV, default_socket_path
from .tracking.expressions import DEFAULT_ROUNDS as DEFAULT_FACE_ROUNDS
from .tracking.screenmap import DEFAULT_SIDE, MAX_SIDE
from .tracking.takes import DEFAULT_ROUNDS, FRAMES_FILE, SESSION_FILE

if TYPE_CHECKING:
    from .settings import HandTrackingSettings, HeadTrackingSettings
    from .tracking.expressions import FaceFrame
    from .tracking.hands import Calibration
    from .tracking.takes import Frame

logger = logging.getLogger(__name__)

HAND_MODEL_FILE = "hand_landmarker.task"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="libre-dictum",
        description="Voice control and dictation that types straight into the kernel.",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help=f"directory holding config.json and scripts/ (default: {DEFAULT_CONFIG_DIR})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="how much to report, case-insensitive (default: INFO)",
    )
    parser.add_argument(
        "--no-hud",
        action="store_false",
        dest="hud",
        help=(
            "do not publish what the on-screen display draws. The socket is created "
            f"readable only by you at {default_socket_path()} (${SOCKET_ENV} overrides "
            "it), and carries the last thing you said. This turns off the socket, not the "
            "display's phrases -- set them to null in the 'overlay' block for that"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        const="DEBUG",
        dest="log_level",
        help="shorthand for --log-level DEBUG",
    )
    parser.add_argument(
        "--cameras",
        action="store_true",
        help=(
            "list every video device and say which one head tracking would open, then "
            "exit. Opens no camera, claims no session, and needs neither a config file "
            "nor the head-tracking extra"
        ),
    )
    parser.add_argument(
        "--hand-scores",
        nargs="?",
        const="",
        metavar="MODEL",
        help=(
            "print live hand scores and the raw ratios behind them, until interrupted. "
            "This is how a hand gesture's thresholds get chosen -- and how the score "
            "calibration is measured, since a ratio is what a calibration constant is "
            f"set from. Takes the {HAND_MODEL_FILE} given here, else "
            f"'ht_hand_model_path', else the {HAND_MODEL_FILE} beside the face model. "
            "Claims no session and types nothing"
        ),
    )
    parser.add_argument(
        "--gesture-capture",
        type=Path,
        metavar="DIR",
        help=(
            "record a guided hand-gesture session into DIR, for tuning thresholds against "
            "real hands instead of guesses. Paced by the hand: a take starts when one "
            "comes into shot and ends when it leaves, so there is nothing to press. Ctrl-C "
            "is safe and re-running the same DIR resumes. Claims no session and types "
            "nothing"
        ),
    )
    parser.add_argument(
        "--gesture-report",
        type=Path,
        metavar="DIR",
        help=(
            "reduce a recorded session to one page: which features tell the gestures "
            "apart, what the worst take of each managed, the raw ratios a "
            "'ht_hand_calibration' is set from, and a replay of the candidate config"
        ),
    )
    parser.add_argument(
        "--gesture-replay",
        type=Path,
        metavar="DIR",
        help=(
            "run a candidate configuration back over a recorded session and report what "
            "fired, what was missed and what went off during the wrong gesture. The way to "
            "try a threshold without performing anything"
        ),
    )
    parser.add_argument(
        "--face-capture",
        type=Path,
        metavar="DIR",
        help=(
            "record a guided facial-gesture session into DIR. A face never leaves the "
            "frame, so a take is paced by the face leaving rest and returning to it -- "
            "which is measured first, in a rest pass, because blendshapes idle non-zero. "
            "Sitting still gives up on a performance; walking away costs nothing. Ctrl-C "
            "is safe and re-running the same DIR resumes. Claims no session and types "
            "nothing"
        ),
    )
    parser.add_argument(
        "--face-report",
        type=Path,
        metavar="DIR",
        help=(
            "reduce a recorded facial session to one page: what this face sits at when "
            "idle, which blendshapes tell the expressions apart, what the worst take of "
            "each managed, a proposed 'ht_custom_gestures' with a measured 'hold', and a "
            "replay of the candidate config"
        ),
    )
    parser.add_argument(
        "--face-replay",
        type=Path,
        metavar="DIR",
        help=(
            "run a candidate configuration back over a recorded facial session and report "
            "what fired, what was missed, and what went off during the wrong expression -- "
            "or during the minute of talking, which is the one that matters"
        ),
    )
    parser.add_argument(
        "--calibrate-screen",
        nargs="?",
        type=_grid_side,
        const=DEFAULT_SIDE,
        default=None,
        metavar="SIDE",
        help=(
            f"measure where a head angle points on the screen: dots to look at and hold "
            f"still on, {DEFAULT_SIDE}x{DEFAULT_SIDE} of them unless SIDE says otherwise "
            f"({DEFAULT_SIDE} to {MAX_SIDE}). More dots fit the same eight coefficients from "
            "more samples, so the mapping chases the noise less -- at about four seconds a "
            "dot, and a worse-looking residual. Prints the 'ht_screen_calibration' block to "
            "paste. Needs libre-dictum-hud running beside it, and no session"
        ),
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        metavar="FILE",
        help=(
            "a JSON fragment holding 'ht_custom_gestures' and/or 'ht_hand_calibration' for "
            "any of the four report and replay modes to use instead of the live config. This "
            "is what makes a threshold cheap to try: edit the fragment, replay, read"
        ),
    )
    parser.add_argument(
        "--rounds",
        type=int,
        metavar="N",
        help=(
            f"takes of each gesture for --gesture-capture (default: {DEFAULT_ROUNDS}) or "
            f"--face-capture (default: {DEFAULT_FACE_ROUNDS}, because facial muscles "
            "fatigue faster than a hand)"
        ),
    )
    parser.add_argument(
        "--stride",
        type=int,
        metavar="N",
        help=(
            "replay every Nth recorded frame for --gesture-replay, matching what a "
            "session's 'ht_hand_stride' feeds the recognizer (default: whatever "
            "the config says). Not a face setting: the face model runs on every frame, so "
            "--face-replay already replays at the rate a session runs at"
        ),
    )
    parser.add_argument("--version", action="version", version=f"libre-dictum {__version__}")
    return parser.parse_args(argv)


def _list_cameras() -> int:
    """Print the video devices and the camera value that would pin the chosen one."""
    from .tracking.cameras import candidates, describe
    from .tracking.v4l2 import enumerate_cameras, stable_path

    nodes = enumerate_cameras()
    print(f"video devices: {describe(nodes)}")

    try:
        chosen = candidates(nodes, None)[0]
    except CameraError as exc:
        print(f"none usable:   {exc}")
        return 1

    print(f"would open:    {chosen.describe()}")

    name = chosen.card.split(":")[0].strip()
    print(f'pin it with:   "camera": {json.dumps(name or chosen.index)}')
    if by_id := stable_path(chosen.index):
        print(f'           or: "camera": {json.dumps(by_id)}')
    return 0


def _watch_hand_scores(config_dir: Path, model_override: str) -> int:
    """Run the hand model on the configured camera and print what it scores."""
    from .tracking.probe import watch_hands

    resolved = _hand_tracking(config_dir, model_override)
    if resolved is None:
        return 1
    hands, head_tracking = resolved
    return watch_hands(hands, camera=head_tracking.camera, capture=head_tracking.capture)


def _capture_gestures(config_dir: Path, directory: Path, rounds: int | None) -> int:
    """Record a guided session."""
    from .tracking.capture import capture_gestures

    resolved = _hand_tracking(config_dir, "")
    if resolved is None:
        return 1
    hands, head_tracking = resolved
    return capture_gestures(
        hands,
        directory,
        camera=head_tracking.camera,
        capture=head_tracking.capture,
        rounds=DEFAULT_ROUNDS if rounds is None else rounds,
    )


def _hand_tracking(
    config_dir: Path, model_override: str
) -> tuple[HandTrackingSettings, HeadTrackingSettings] | None:
    """The settings every camera-side hand tool runs on, or None with the reason printed."""
    from .settings import HandTrackingSettings, load_settings

    settings = load_settings(config_dir)
    if (head_tracking := settings.head_tracking) is None:
        logger.error(
            "'enable_head_tracking' is off, so no camera is configured. Turn it on, or run "
            "--cameras to see what is there"
        )
        return None

    hands: HandTrackingSettings | None = head_tracking.hands
    if model_override:
        hands = (
            replace(hands, model_path=model_override)
            if hands is not None
            else HandTrackingSettings(model_path=model_override)
        )
    elif hands is None and (found := _hand_model_beside(head_tracking.model_path)):
        hands = HandTrackingSettings(model_path=found)

    if hands is None:
        logger.error(
            "no hand model. 'ht_hand_enabled' is off, there is no %s beside the face model, "
            "and no path was given. Either pass '--hand-scores path/to/%s', or set "
            "'ht_hand_enabled' and 'ht_hand_model_path'.",
            HAND_MODEL_FILE,
            HAND_MODEL_FILE,
        )
        return None

    return hands, head_tracking


def _gesture_report(config_dir: Path, directory: Path, candidate: Path | None) -> int:
    """Reduce a recorded session to one page, and end it with the candidate's replay."""
    from .tracking import tuning

    frames = _recording(directory)
    if frames is None:
        return 1
    calibration, definitions = _candidate(config_dir, candidate)

    scored = tuning.score(frames, calibration=calibration)
    summary = tuning.summarise(scored, windows=tuning.windows_for(definitions))
    report = tuning.render(
        summary, definitions, proposal=tuning.propose(summary, calibration=calibration)
    )
    if definitions:
        stride = _stride(config_dir, directory)
        report += "\n\n" + tuning.render_replay(tuning.replay(scored, definitions, stride=stride))
    print(report)
    return 0


def _gesture_replay(
    config_dir: Path, directory: Path, candidate: Path | None, stride: int | None
) -> int:
    """Run a candidate configuration back over a recording and say what it did."""
    from .tracking import tuning

    frames = _recording(directory)
    if frames is None:
        return 1
    calibration, definitions = _candidate(config_dir, candidate)
    if not definitions:
        logger.error(
            "no hand gestures to replay: neither %s nor the config defines any in "
            "'ht_custom_gestures'",
            candidate or "the candidate",
        )
        return 1

    scored = tuning.score(frames, calibration=calibration)
    chosen = stride if stride is not None else _stride(config_dir, directory)
    print(tuning.render_replay(tuning.replay(scored, definitions, stride=chosen)))
    return 0


def _capture_expressions(config_dir: Path, directory: Path, rounds: int | None) -> int:
    """Record a guided facial session, on the camera and model a session would open."""
    from .tracking.expressions import DEFAULT_ROUNDS as FACE_ROUNDS
    from .tracking.face_capture import capture_expressions

    settings = _head_tracking(config_dir)
    if settings is None:
        return 1
    return capture_expressions(
        settings, directory, rounds=FACE_ROUNDS if rounds is None else rounds
    )


def _head_tracking(config_dir: Path) -> HeadTrackingSettings | None:
    """The settings every face-side tool runs on, or None with the reason printed."""
    from .settings import load_settings

    settings = load_settings(config_dir).head_tracking
    if settings is None:
        logger.error(
            "'enable_head_tracking' is off, so no camera and no face model are configured. "
            "Turn it on, or run --cameras to see what is there"
        )
        return None
    return settings


def _face_report(config_dir: Path, directory: Path, candidate: Path | None) -> int:
    """Reduce a recorded facial session to one page, and end it with the candidate's replay."""
    from .tracking import face_tuning, tuning

    frames = _face_recording(directory)
    if frames is None:
        return 1
    levels, definitions = _face_candidate(config_dir, candidate)

    measured = _baseline(frames)
    scored = face_tuning.score(frames, measured)
    summary = face_tuning.summarise(scored, windows=face_tuning.windows_for(definitions))
    report = face_tuning.render(
        summary,
        definitions,
        face_tuning.diagnose(frames),
        measured,
        proposal=face_tuning.propose(summary, definitions, measured),
    )
    if definitions:
        as_configured = face_tuning.score(frames, _levels_as(levels))
        report += "\n\n" + tuning.render_replay(tuning.replay(as_configured, definitions))
    print(report)
    return 0


def _face_replay(config_dir: Path, directory: Path, candidate: Path | None) -> int:
    """Run a candidate configuration back over a facial recording and say what it did."""
    from .tracking import face_tuning, tuning

    frames = _face_recording(directory)
    if frames is None:
        return 1
    levels, definitions = _face_candidate(config_dir, candidate)
    if not definitions:
        logger.error(
            "no facial gestures to replay: neither %s nor the config defines any in "
            "'ht_custom_gestures'",
            candidate or "the candidate",
        )
        return 1
    scored = face_tuning.score(frames, _levels_as(levels))
    print(tuning.render_replay(tuning.replay(scored, definitions)))
    return 0


def _grid_side(value: str) -> int:
    """A --calibrate-screen argument, refused before any configuration load."""
    try:
        side = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of dots") from None
    if not DEFAULT_SIDE <= side <= MAX_SIDE:
        raise argparse.ArgumentTypeError(
            f"a calibration grid is {DEFAULT_SIDE} to {MAX_SIDE} dots a side; {side} is not"
        )
    return side


def _calibrate_screen(config_dir: Path, side: int) -> int:
    """Measure where a head angle points, and print the block to paste."""
    from .settings import load_settings
    from .tracking.screen_capture import calibrate_screen

    return calibrate_screen(load_settings(config_dir), side=side)


def _face_recording(directory: Path) -> list[FaceFrame] | None:
    """Every frame of a recorded facial session, or None after saying what is missing."""
    from .tracking.expressions import read_frames

    path = directory / FRAMES_FILE
    if not _is_kind(directory, face=True):
        return None
    if not path.is_file():
        logger.error("no recording at %s. Record one with --face-capture %s", path, directory)
        return None
    with path.open(encoding="utf-8") as handle:
        frames = list(read_frames(handle))
    if not frames:
        logger.error("%s is empty", path)
        return None
    logger.info("%d frames from %s", len(frames), path)
    return frames


def _baseline(frames: Sequence[FaceFrame]):  # noqa: ANN202 - the pure module owns the type
    """The idle levels, re-measured from the recording's own rest pass."""
    from .tracking.expressions import REST, Baseline

    return Baseline.measure(frame for frame in frames if frame.label == REST and frame.take)


def _face_candidate(config_dir: Path, path: Path | None) -> tuple[dict[str, float], dict[str, Any]]:
    """The resting levels and the facial gestures to analyse: a fragment, else the config."""
    from .settings import face_baseline
    from .tracking.face_tuning import face_definitions_of

    source = path if path is not None else config_dir / "config.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError(f"{source} is not a JSON object")
    return face_baseline(payload), face_definitions_of(payload)


def _levels_as(levels: Mapping[str, float]):  # noqa: ANN202 - the pure module owns the type
    """Configured resting levels as the Baseline the scorer takes."""
    from .tracking.expressions import Baseline

    return Baseline(levels=dict(levels))


def _is_kind(directory: Path, *, face: bool) -> bool:
    """Whether the recording in that directory is the kind the caller can read."""
    from .tracking.expressions import FACE_KIND, HAND_KIND, recording_kind

    try:
        payload = json.loads((directory / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    found = recording_kind(payload) if isinstance(payload, dict) else HAND_KIND
    if (found == FACE_KIND) == face:
        return True
    logger.error(
        "%s holds a %s recording. Read it with --%s-report instead",
        directory,
        found,
        "face" if found == FACE_KIND else "gesture",
    )
    return False


def _recording(directory: Path) -> list[Frame] | None:
    """Every frame of a recorded session, or None after saying what is missing."""
    from .tracking.takes import read_frames

    path = directory / FRAMES_FILE
    if not _is_kind(directory, face=False):
        return None
    if not path.is_file():
        logger.error("no recording at %s. Record one with --gesture-capture %s", path, directory)
        return None
    with path.open(encoding="utf-8") as handle:
        frames = list(read_frames(handle))
    if not frames:
        logger.error("%s is empty", path)
        return None
    logger.info("%d frames from %s", len(frames), path)
    return frames


def _candidate(config_dir: Path, path: Path | None) -> tuple[Calibration, dict[str, Any]]:
    """The calibration and hand gestures to analyse under: a fragment, else the live config."""
    from .settings import hand_calibration
    from .tracking.tuning import definitions_of

    source = path if path is not None else config_dir / "config.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ConfigError(f"{source} is not a JSON object")
    return hand_calibration(payload), definitions_of(payload)


def _stride(config_dir: Path, directory: Path) -> int:
    """How many recorded frames a session would have skipped between two hand results."""
    for source, key in (
        (directory / SESSION_FILE, "stride_in_session"),
        (config_dir / "config.json", "ht_hand_stride"),
    ):
        try:
            value = json.loads(source.read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError):
            continue
        if isinstance(value, int) and value > 0:
            return value
    return 1


def _hand_model_beside(face_model_path: str) -> str | None:
    """hand_landmarker.task in the directory the face model came from, if it is there."""
    candidate = Path(face_model_path).expanduser().parent / HAND_MODEL_FILE
    if not candidate.is_file():
        return None
    logger.info("Using %s, found beside the face model", candidate)
    return str(candidate)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.cameras:
        return _list_cameras()

    try:
        if args.hand_scores is not None:
            return _watch_hand_scores(args.config_dir, args.hand_scores)
        if args.gesture_capture:
            return _capture_gestures(args.config_dir, args.gesture_capture, args.rounds)
        if args.gesture_report:
            return _gesture_report(args.config_dir, args.gesture_report, args.candidate)
        if args.gesture_replay:
            return _gesture_replay(
                args.config_dir, args.gesture_replay, args.candidate, args.stride
            )
        if args.face_capture:
            return _capture_expressions(args.config_dir, args.face_capture, args.rounds)
        if args.face_report:
            return _face_report(args.config_dir, args.face_report, args.candidate)
        if args.face_replay:
            return _face_replay(args.config_dir, args.face_replay, args.candidate)
        if args.calibrate_screen is not None:
            return _calibrate_screen(args.config_dir, args.calibrate_screen)
    except (ConfigError, CameraError, OSError, ValueError) as exc:
        logger.error("%s", exc)
        return 1

    try:
        app = Application.from_config_dir(args.config_dir, hud=args.hud)
    except (AlreadyRunningError, ConfigError, DeviceError) as exc:
        logger.error("%s", exc)
        return 1

    def request_shutdown(number: int, _frame: object) -> None:
        signal.signal(number, signal.SIG_DFL)  # a second interrupt then kills the process
        logger.info("Received %s, shutting down", signal.Signals(number).name)
        app.shutdown()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, request_shutdown)

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
