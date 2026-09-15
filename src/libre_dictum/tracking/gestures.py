from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..meters import GestureMeter, Reading

logger = logging.getLogger(__name__)

Scores = Mapping[str, float]

GestureDefinition = Mapping[str, Any]

HOLD_KEY = "hold"

FACE_FEATURES: tuple[str, ...] = (
    "_neutral",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "eyeWideLeft",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawOpen",
    "jawRight",
    "mouthClose",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthFunnel",
    "mouthLeft",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthPucker",
    "mouthRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "noseSneerLeft",
    "noseSneerRight",
)


@dataclass(frozen=True, slots=True)
class FeatureCondition:
    """One feature's activation window and its release value."""

    feature: str
    minimum: float | None = None
    maximum: float | None = None
    release: float | None = None

    @classmethod
    def from_config(cls, feature: str, limits: Mapping[str, float]) -> FeatureCondition:
        return cls(
            feature=feature,
            minimum=limits.get("min"),
            maximum=limits.get("max"),
            release=limits.get("release"),
        )

    def holds(self, score: float) -> bool:
        """Whether the score is inside the activation window."""
        above_minimum = self.minimum is None or score >= self.minimum
        below_maximum = self.maximum is None or score <= self.maximum
        return above_minimum and below_maximum

    def released(self, score: float) -> bool:
        """Whether the score has fallen back far enough to re-arm the gesture."""
        if self.minimum is not None and score <= self._release_value(self.minimum):
            return True
        return self.maximum is not None and score >= self._release_value(self.maximum)

    def _release_value(self, threshold: float) -> float:
        """Where the gesture re-arms; the activation threshold when none is configured."""
        return self.release if self.release is not None else threshold

    def describe(self, score: float) -> str:
        requirements = []
        if self.minimum is not None:
            requirements.append(f">{self.minimum}")
        if self.maximum is not None:
            requirements.append(f"<{self.maximum}")
        return f"{self.feature}: {score:.2f} ({','.join(requirements)})"

    def reading(self, scores: Scores) -> Reading:
        """This condition's live value, for a display."""
        return Reading(
            feature=self.feature,
            score=scores.get(self.feature),
            minimum=self.minimum,
            maximum=self.maximum,
        )


class GestureRecognizer:
    """Tracks which gestures are active and reports the ones that just fired."""

    def __init__(self, definitions: Mapping[str, GestureDefinition]) -> None:
        self.conditions: dict[str, tuple[FeatureCondition, ...]] = {
            name: tuple(
                FeatureCondition.from_config(feature, limits)
                for feature, limits in features.items()
                if feature != HOLD_KEY
            )
            for name, features in definitions.items()
        }
        self.holds: dict[str, float] = {
            name: float(features.get(HOLD_KEY, 0)) / 1000.0
            for name, features in definitions.items()
        }
        self.suppressors: dict[str, frozenset[str]] = _suppressors(self.conditions)
        self._order: tuple[str, ...] = tuple(
            sorted(self.conditions, key=lambda name: -len(self.conditions[name]))
        )
        self.active: set[str] = set()
        self._holding_since: dict[str, float] = {}

    def update(self, scores: Scores, now: float = 0.0) -> tuple[list[str], list[str]]:
        """Feed one frame of scores; return the gestures that just fired and just released."""
        fired: list[str] = []
        released: list[str] = []
        for name in self._order:
            conditions = self.conditions[name]
            if self.active & self.suppressors[name]:
                self._release(name, released)
                self._holding_since.pop(name, None)
                continue

            if any(condition.feature not in scores for condition in conditions):
                self._release(name, released)
                self._holding_since.pop(name, None)
                continue

            if name in self.active:
                if any(c.released(scores.get(c.feature, 0.0)) for c in conditions):
                    self._release(name, released)
                continue

            if not all(c.holds(scores.get(c.feature, 0.0)) for c in conditions):
                self._holding_since.pop(name, None)
                continue

            since = self._holding_since.setdefault(name, now)
            if now - since >= self.holds[name]:
                self._holding_since.pop(name, None)
                self.active.add(name)
                fired.append(name)
        return fired, released

    def _release(self, name: str, released: list[str]) -> None:
        """Drop one gesture from the active set, noting it only if it was in there."""
        if name in self.active:
            self.active.discard(name)
            released.append(name)

    def reset(self) -> None:
        """Forget which gestures are active, and any dwell in progress."""
        self.active.clear()
        self._holding_since.clear()

    def meters(self, scores: Scores) -> tuple[GestureMeter, ...]:
        """Every gesture's live reading, in the order the configuration declares them."""
        return tuple(
            GestureMeter(
                name=name,
                readings=tuple(condition.reading(scores) for condition in conditions),
                active=name in self.active,
                hold=self.holds[name],
            )
            for name, conditions in self.conditions.items()
        )

    def describe(self, scores: Scores, names: Iterable[str]) -> str:
        """One line of live scores for ht_debug_gestures."""
        parts = []
        for name in names:
            conditions = self.conditions.get(name)
            if conditions is None:
                parts.append(f"{name}: {_reading(scores.get(name))}")
                continue
            state = "[ACTIVE]" if name in self.active else "[IDLE]"
            features = " | ".join(
                condition.describe(scores.get(condition.feature, 0.0)) for condition in conditions
            )
            parts.append(f"{state} {name} -> {features}")
        return " || ".join(parts)


def _suppressors(
    conditions: Mapping[str, tuple[FeatureCondition, ...]],
) -> dict[str, frozenset[str]]:
    """Per gesture, the gestures that ask for strictly more than it does."""
    features = {
        name: frozenset(condition.feature for condition in group)
        for name, group in conditions.items()
    }
    return {
        name: frozenset(other for other, wider in features.items() if wider > mine)
        for name, mine in features.items()
    }


def _reading(score: float | None) -> str:
    """One score for the debug line, or `--` when unreported."""
    return "--" if score is None else f"{score:.2f}"
