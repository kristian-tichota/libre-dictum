from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields

from ..meters import HandReading

logger = logging.getLogger(__name__)

Point = tuple[float, float, float]

_IMAGE_IS_MIRRORED = False

_HANDEDNESS_ASSUMES_MIRRORED = False

_OPPOSITE_SIDE = {"left": "right", "right": "left"}

WRIST = 0
MIDDLE_MCP = 9

FINGER_CHAINS: dict[str, tuple[int, int, int, int]] = {
    "Thumb": (1, 2, 3, 4),
    "Index": (5, 6, 7, 8),
    "Middle": (9, 10, 11, 12),
    "Ring": (13, 14, 15, 16),
    "Little": (17, 18, 19, 20),
}

INDEX_MCP = 5
LITTLE_MCP = 17

_FOLDING_FINGERS = ("Index", "Middle", "Ring", "Little")


@dataclass(frozen=True, slots=True)
class Calibration:
    """Where each score's 0 and 1 sit, in units of the hand's own geometry."""

    folded_extension: float = 0.30
    folded_extension_thumb: float = 0.62
    pinch_closed: float = 0.16
    pinch_open: float = 0.60
    spread_closed: float = 0.16
    spread_open: float = 0.55
    palm_area_face_on: float = 0.42
    near_span: float = 0.14
    far_span: float = 0.07

    def problems(self) -> list[str]:
        """Why this calibration would not work, or an empty list."""
        found = []
        for name, high, low in (
            ("pinch_closed/pinch_open", self.pinch_open, self.pinch_closed),
            ("spread_closed/spread_open", self.spread_closed, self.spread_open),
        ):
            if high == low:
                found.append(f"{name} are both {high:g}, which pins the score it feeds at 0")
        if self.near_span == self.far_span:
            found.append(
                f"near_span/far_span are both {self.near_span:g}, which pins handNear at 0"
            )
        for name, value in (
            ("folded_extension", self.folded_extension),
            ("folded_extension_thumb", self.folded_extension_thumb),
        ):
            if value == 1.0:
                found.append(f"{name} is 1.0, which makes every finger read as fully folded")
        if self.palm_area_face_on == 0.0:
            found.append("palm_area_face_on is 0, which pins handPalmVisible at 0")
        return found


CALIBRATION_KEYS: tuple[str, ...] = tuple(field.name for field in fields(Calibration))

CALIBRATION = Calibration()

BASE_FEATURES: tuple[str, ...] = (
    "handPresent",
    "handIsLeft",
    "handIsRight",
    "handThumbCurl",
    "handIndexCurl",
    "handMiddleCurl",
    "handRingCurl",
    "handLittleCurl",
    "handFist",
    "handOpen",
    "handPinchIndex",
    "handPinchMiddle",
    "handSpread",
    "handPalmVisible",
    "handHigh",
    "handNear",
    "handPointingUp",
    "handPointingDown",
    "handPointingLeft",
    "handPointingRight",
    "handThumbPointingUp",
    "handThumbPointingDown",
    "handThumbPointingLeft",
    "handThumbPointingRight",
)

_IDENTITY_FEATURES = frozenset({"handPresent", "handIsLeft", "handIsRight"})

SHAPE_FEATURES: tuple[str, ...] = tuple(
    name for name in BASE_FEATURES if name not in _IDENTITY_FEATURES
)


def side_feature(side: str, name: str) -> str:
    """("left", "handFist") -> "leftHandFist"."""
    return f"{side.lower()}{name[0].upper()}{name[1:]}"


def user_side(label: str) -> str:
    """Mediapipe's handedness label, as the side the *user* would call it."""
    lowered = label.strip().lower()
    if not _HANDEDNESS_ASSUMES_MIRRORED or _IMAGE_IS_MIRRORED:
        return lowered
    return _OPPOSITE_SIDE.get(lowered, lowered)


HAND_FEATURES: tuple[str, ...] = (
    *BASE_FEATURES,
    *(side_feature(side, name) for side in ("left", "right") for name in SHAPE_FEATURES),
    "bothHandsPresent",
)


@dataclass(frozen=True, slots=True)
class Hand:
    """One detected hand: its 21 landmarks, and which of the user's hands it is."""

    landmarks: Sequence[Point]
    side: str = ""

    @property
    def known_side(self) -> str:
        """ "left", "right", or "" when the landmarker would not say."""
        lowered = self.side.strip().lower()
        return lowered if lowered in ("left", "right") else ""

    def valid(self) -> bool:
        """Whether there are enough landmarks to score."""
        return len(self.landmarks) >= 21


class PrimaryHand:
    """Which hand the unprefixed scores describe, kept the same one from frame to frame."""

    def __init__(self) -> None:
        self._side = ""

    def order(self, hands: Sequence[Hand]) -> list[Hand]:
        """The usable hands, primary first."""
        usable = [hand for hand in hands if hand.valid()]
        if not usable:
            return usable

        held = next((hand for hand in usable if hand.known_side == self._side), None)
        primary = held if held is not None and self._side else max(usable, key=_palm_span)
        self._side = primary.known_side
        return [primary, *(hand for hand in usable if hand is not primary)]

    def reset(self) -> None:
        """Forget which hand was primary; the next frame chooses afresh."""
        self._side = ""


@dataclass(frozen=True, slots=True)
class Ratios:
    """The raw measurements a score is derived from, before any calibration is applied."""

    extension: dict[str, float]
    pinch_index: float
    pinch_middle: float
    spread: float
    palm_area: float
    palm_span: float
    height: float


def ratios(hand: Hand) -> Ratios | None:
    """The raw measurements behind one hand's scores, or None for a dropped hand."""
    if not hand.valid():
        return None
    points = hand.landmarks
    palm = _palm_span(hand)
    return Ratios(
        extension={name: _extension(points, chain) for name, chain in FINGER_CHAINS.items()},
        pinch_index=_gap(points, FINGER_CHAINS["Index"][3], palm),
        pinch_middle=_gap(points, FINGER_CHAINS["Middle"][3], palm),
        spread=_mean_tip_gap(points, palm),
        palm_area=_palm_area(points, palm),
        palm_span=palm,
        height=_unit(1.0 - points[WRIST][1]),
    )


def scores(hands: Iterable[Hand], *, calibration: Calibration = CALIBRATION) -> dict[str, float]:
    """Named scores for the visible hands, ready for GestureRecognizer."""
    usable = [hand for hand in hands if hand.valid()]
    if not usable:
        return {}

    result: dict[str, float] = {
        "handPresent": 1.0,
        "bothHandsPresent": 1.0 if len(usable) > 1 else 0.0,
        "handIsLeft": 0.0,
        "handIsRight": 0.0,
    }
    result.update(
        {side_feature(side, name): 0.0 for side in ("left", "right") for name in SHAPE_FEATURES}
    )

    primary = usable[0]
    shape = _shape_scores(primary, calibration)
    result.update(shape)
    if (side := primary.known_side) != "":
        result[f"handIs{side.capitalize()}"] = 1.0

    for hand in usable:
        if (side := hand.known_side) == "":
            continue
        per_hand = shape if hand is primary else _shape_scores(hand, calibration)
        result.update({side_feature(side, name): score for name, score in per_hand.items()})

    return result


def _shape_scores(hand: Hand, calibration: Calibration) -> dict[str, float]:
    """Every score that describes one hand's shape, unsuffixed."""
    points = hand.landmarks
    palm = _palm_span(hand)

    curls = {
        name: _curl(points, chain, calibration, thumb=name == "Thumb")
        for name, chain in FINGER_CHAINS.items()
    }
    folding = [curls[name] for name in _FOLDING_FINGERS]

    result = {f"hand{name}Curl": value for name, value in curls.items()}
    result["handFist"] = min(folding)
    result["handOpen"] = 1.0 - max(folding)
    result["handPinchIndex"] = _pinch(points, FINGER_CHAINS["Index"][3], palm, calibration)
    result["handPinchMiddle"] = _pinch(points, FINGER_CHAINS["Middle"][3], palm, calibration)
    result["handSpread"] = _spread(points, palm, calibration)
    result["handPalmVisible"] = _palm_visible(points, palm, calibration)
    result["handHigh"] = _high(points)
    result["handNear"] = _near(palm, calibration)
    result.update(_pointing(points, FINGER_CHAINS["Index"], "handPointing"))
    result.update(_pointing(points, FINGER_CHAINS["Thumb"], "handThumbPointing"))
    return result


def hand_readings(hands: Sequence[Hand]) -> tuple[HandReading, ...]:
    """The raw measurements behind each visible hand, primary first."""
    readings = []
    for index, hand in enumerate(hand for hand in hands if hand.valid()):
        raw = ratios(hand)
        if raw is None:
            continue
        readings.append(
            HandReading(
                side=hand.known_side,
                primary=index == 0,
                span=raw.palm_span,
                extension=tuple(raw.extension.get(name, 0.0) for name in FINGER_CHAINS),
                pinch_index=raw.pinch_index,
                pinch_middle=raw.pinch_middle,
                spread=raw.spread,
                palm_area=raw.palm_area,
            )
        )
    return tuple(readings)


def report(hands: Sequence[Hand], *, calibration: Calibration = CALIBRATION) -> str:
    """Every score for the hands in one frame, and the raw ratio behind each one."""
    if not (ordered := [hand for hand in hands if hand.valid()]):
        return "no hand in frame"

    scored = scores(ordered, calibration=calibration)
    lines = [
        "  ".join(
            f"{name} {scored[name]:.2f}"
            for name in ("handPresent", "handIsLeft", "handIsRight", "bothHandsPresent")
        )
    ]
    for index, hand in enumerate(ordered):
        lines.extend(_hand_report(hand, index, scored, calibration))
    return "\n".join(lines)


def _hand_report(
    hand: Hand, index: int, _scored: dict[str, float], calibration: Calibration
) -> list[str]:
    """The block for one hand: its shape scores, then the ratios they came from."""
    side = hand.known_side
    shape = _shape_scores(hand, calibration)
    raw = ratios(hand)
    if raw is None:
        return []

    names = ["hand*"] if index == 0 else []
    if side:
        names.append(f"{side}Hand*")
    label = f"{side or 'unknown'} hand" + (" (primary)" if index == 0 else "")
    return [
        f"{label} -> {', '.join(names)}   span {raw.palm_span:.3f}",
        "  scores  " + _pairs(shape, ("handFist", "handOpen", "handSpread", "handPalmVisible")),
        "          " + _pairs(shape, ("handHigh", "handNear")),
        "          " + _pairs(shape, ("handPinchIndex", "handPinchMiddle")),
        "          curl   " + _pairs(shape, tuple(f"hand{name}Curl" for name in FINGER_CHAINS)),
        "          point  "
        + _pairs(shape, tuple(f"handPointing{d}" for d in ("Up", "Down", "Left", "Right"))),
        "  ratios  extension  " + _pairs(raw.extension, tuple(FINGER_CHAINS)),
        f"          pinch  index {raw.pinch_index:.3f}  middle {raw.pinch_middle:.3f}"
        f"    spread {raw.spread:.3f}    palmArea {raw.palm_area:.3f}"
        f"    height {raw.height:.3f}",
    ]


def _pairs(values: dict[str, float], names: tuple[str, ...]) -> str:
    """name value pairs on one line, with the shared hand prefix dropped."""
    return "  ".join(f"{_short(name)} {values.get(name, 0.0):.3f}" for name in names)


def _short(name: str) -> str:
    """handPinchIndex -> pinchIndex: what the line's own label does not already say."""
    trimmed = name.removeprefix("hand").removeprefix("Pointing").removesuffix("Curl")
    return trimmed[0].lower() + trimmed[1:] if trimmed else name


def _palm_span(hand: Hand) -> float:
    """Wrist to middle knuckle: the hand's own yardstick, and how it is sorted by size."""
    points = hand.landmarks
    return _distance(points[WRIST], points[MIDDLE_MCP]) if hand.valid() else 0.0


def _extension(points: Sequence[Point], chain: tuple[int, int, int, int]) -> float:
    """How straight one finger is: the tip's distance from the knuckle over its bone length."""
    knuckle, middle, end, tip = (points[index] for index in chain)
    bones = _distance(knuckle, middle) + _distance(middle, end) + _distance(end, tip)
    return _distance(knuckle, tip) / bones if bones > 0.0 else 0.0


def _gap(points: Sequence[Point], tip: int, palm: float) -> float:
    """Thumb tip to one fingertip, over the palm span."""
    return _distance(points[FINGER_CHAINS["Thumb"][3]], points[tip]) / palm if palm > 0.0 else 0.0


def _mean_tip_gap(points: Sequence[Point], palm: float) -> float:
    """Mean distance between neighbouring fingertips, over the palm span."""
    if palm <= 0.0:
        return 0.0
    tips = [FINGER_CHAINS[name][3] for name in _FOLDING_FINGERS]
    gaps = [
        _distance(points[left], points[right]) for left, right in zip(tips, tips[1:], strict=False)
    ]
    return sum(gaps) / len(gaps) / palm


def _palm_area(points: Sequence[Point], palm: float) -> float:
    """Projected knuckle-triangle area over the palm span squared."""
    if palm <= 0.0:
        return 0.0
    return _triangle_area(points[WRIST], points[INDEX_MCP], points[LITTLE_MCP]) / (palm * palm)


def _curl(
    points: Sequence[Point],
    chain: tuple[int, int, int, int],
    calibration: Calibration,
    *,
    thumb: bool,
) -> float:
    """How far one finger is folded: 0 straight, 1 folded as far as it goes."""
    folded = calibration.folded_extension_thumb if thumb else calibration.folded_extension
    return _between(_extension(points, chain), high=1.0, low=folded)


def _pinch(points: Sequence[Point], tip: int, palm: float, calibration: Calibration) -> float:
    """How closed the gap is between the thumb tip and one fingertip."""
    if palm <= 0.0:
        return 0.0
    gap = _gap(points, tip, palm)
    return _between(gap, high=calibration.pinch_open, low=calibration.pinch_closed)


def _spread(points: Sequence[Point], palm: float, calibration: Calibration) -> float:
    """How splayed the four fingers are, from the mean gap between neighbouring tips."""
    if palm <= 0.0:
        return 0.0
    mean = _mean_tip_gap(points, palm)
    return _between(mean, high=calibration.spread_closed, low=calibration.spread_open)


def _palm_visible(points: Sequence[Point], palm: float, calibration: Calibration) -> float:
    """How square the palm is to the camera: 1 face-on, 0 edge-on."""
    if palm <= 0.0:
        return 0.0
    return _between(_palm_area(points, palm), high=0.0, low=calibration.palm_area_face_on)


def _high(points: Sequence[Point]) -> float:
    """How far up the frame the hand sits."""
    return _unit(1.0 - points[WRIST][1])


def _near(palm: float, calibration: Calibration) -> float:
    """How close the hand is, from its apparent size."""
    return _between(palm, high=calibration.far_span, low=calibration.near_span)


def _pointing(
    points: Sequence[Point], chain: tuple[int, int, int, int], prefix: str
) -> dict[str, float]:
    """Which way one digit points, as four one-sided scores named <prefix><Direction>."""
    base, tip = points[chain[0]], points[chain[3]]
    dx, dy = tip[0] - base[0], tip[1] - base[1]
    length = math.hypot(dx, dy)
    directions = ("Up", "Down", "Left", "Right")
    if length <= 0.0:
        return dict.fromkeys((f"{prefix}{name}" for name in directions), 0.0)

    dx, dy = dx / length, dy / length
    if not _IMAGE_IS_MIRRORED:
        dx = -dx
    return {
        f"{prefix}Up": _unit(-dy),
        f"{prefix}Down": _unit(dy),
        f"{prefix}Right": _unit(dx),
        f"{prefix}Left": _unit(-dx),
    }


def _distance(first: Point, second: Point) -> float:
    """Euclidean distance in all three axes."""
    return math.dist(first, second)


def _triangle_area(first: Point, second: Point, third: Point) -> float:
    """Area of a triangle projected on the image plane, ignoring depth."""
    return (
        abs(
            (second[0] - first[0]) * (third[1] - first[1])
            - (third[0] - first[0]) * (second[1] - first[1])
        )
        / 2.0
    )


def _unit(value: float) -> float:
    """Clamp to [0, 1], which every score promises."""
    return min(max(value, 0.0), 1.0) + 0.0


def _between(value: float, *, high: float, low: float) -> float:
    """Map value onto [0, 1], scoring 0 at high and 1 at low."""
    if high == low:
        return 0.0
    return _unit((high - value) / (high - low))
