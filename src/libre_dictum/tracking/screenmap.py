from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from ..smoothing import OneEuroFilter

TERMS = 3

COEFFICIENTS = 8

MINIMUM_SAMPLES = 4

TRAVEL_DEGREES = 3.0

MAX_ANGLE_DEGREES = 60.0

_HORIZON = 1e-3

_REFINEMENTS = 40

_CONVERGED = 1e-12


def _in_pixels(fraction: float, width: int, height: int) -> float:
    """A fraction of the screen as pixels, by the diagonal, so neither axis is privileged."""
    return fraction * math.hypot(width, height) / math.sqrt(2.0)


def basis(yaw: float, pitch: float) -> tuple[float, float]:
    """One head pose as ray coordinates on a plane one unit ahead."""
    limit = math.radians(MAX_ANGLE_DEGREES)
    yaw_rad = max(-limit, min(limit, math.radians(yaw)))
    pitch_rad = max(-limit, min(limit, math.radians(pitch)))
    return math.tan(yaw_rad), math.tan(pitch_rad) / math.cos(yaw_rad)


def _project(h: np.ndarray, poses: np.ndarray) -> np.ndarray:
    """Every pose through one coefficient set, as n by 2 screen points."""
    denominator = h[6] * poses[:, 0] + h[7] * poses[:, 1] + 1.0
    denominator = np.where(
        np.abs(denominator) < _HORIZON,
        np.copysign(_HORIZON, np.sign(denominator) + 0.5),
        denominator,
    )
    return np.column_stack(
        [
            (h[0] * poses[:, 0] + h[1] * poses[:, 1] + h[2]) / denominator,
            (h[3] * poses[:, 0] + h[4] * poses[:, 1] + h[5]) / denominator,
        ]
    )


def _refine(h: np.ndarray, poses: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """Polish the DLT's answer into the fit whose residual is the one being reported."""
    best = np.array(h, dtype=np.float64)
    best_cost = float(np.sum((_project(best, poses) - targets) ** 2))
    damping = 1e-6

    for _ in range(_REFINEMENTS):
        predicted = _project(best, poses)
        denominator = best[6] * poses[:, 0] + best[7] * poses[:, 1] + 1.0
        denominator = np.where(np.abs(denominator) < _HORIZON, _HORIZON, denominator)
        u, v, zero, one = poses[:, 0], poses[:, 1], np.zeros(len(poses)), np.ones(len(poses))
        rows_x = (
            np.column_stack(
                [u, v, one, zero, zero, zero, -predicted[:, 0] * u, -predicted[:, 0] * v]
            )
            / denominator[:, None]
        )
        rows_y = (
            np.column_stack(
                [zero, zero, zero, u, v, one, -predicted[:, 1] * u, -predicted[:, 1] * v]
            )
            / denominator[:, None]
        )
        jacobian = np.vstack([rows_x, rows_y])
        error = predicted - targets
        residual = np.concatenate([error[:, 0], error[:, 1]])

        gradient = jacobian.T @ residual
        normal = jacobian.T @ jacobian
        try:
            step = np.linalg.solve(normal + damping * np.eye(COEFFICIENTS), gradient)
        except np.linalg.LinAlgError:
            break

        candidate = best - step
        cost = float(np.sum((_project(candidate, poses) - targets) ** 2))
        if not math.isfinite(cost) or cost >= best_cost:
            damping *= 10.0
            if damping > 1e8:
                break
            continue
        best, improvement, best_cost = candidate, best_cost - cost, cost
        damping = max(damping / 10.0, 1e-12)
        if improvement < _CONVERGED:
            break

    return best


@dataclass(frozen=True, slots=True)
class Sample:
    """One calibration dot."""

    x: float
    y: float
    yaw: float
    pitch: float
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class ScreenBounds:
    """Where the calibrated screen sits inside the space the pointer's device spans."""

    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0

    @property
    def whole_desktop(self) -> bool:
        """Whether this is the identity."""
        return (self.x, self.y, self.width, self.height) == (0.0, 0.0, 1.0, 1.0)

    def into_desktop(self, x: float, y: float) -> tuple[float, float]:
        """A point on the calibrated screen, as a point on the whole desktop."""
        return self.x + x * self.width, self.y + y * self.height

    @classmethod
    def from_pixels(
        cls, x: float, y: float, width: float, height: float, desktop: tuple[float, float]
    ) -> ScreenBounds:
        """The rectangle a compositor reports, as fractions."""
        desktop_width, desktop_height = desktop
        for name, value in (
            ("desktop_width", desktop_width),
            ("desktop_height", desktop_height),
            ("width", width),
            ("height", height),
        ):
            if value <= 0:
                raise ValueError(f"{name} is {value:g}; it is a positive number of pixels")
        if x < 0 or y < 0 or x + width > desktop_width or y + height > desktop_height:
            raise ValueError(
                f"the screen at {x:g},{y:g} sized {width:g}x{height:g} does not fit inside a "
                f"desktop of {desktop_width:g}x{desktop_height:g}. The desktop is every "
                "monitor together, so it is at least as wide as the rightmost edge of any of "
                "them"
            )
        return cls(
            x=x / desktop_width,
            y=y / desktop_height,
            width=width / desktop_width,
            height=height / desktop_height,
        )


WHOLE_DESKTOP = ScreenBounds()


@dataclass(frozen=True, slots=True)
class ScreenMap:
    """Where on the screen the head is pointing, as eight fitted coefficients."""

    x: tuple[float, ...] = ()
    y: tuple[float, ...] = ()
    perspective: tuple[float, float] = (0.0, 0.0)
    residual: float = 0.0
    samples: int = 0
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def measured(self) -> bool:
        """Whether there is a mapping here at all."""
        return len(self.x) == 3 and len(self.y) == 3

    def at(self, yaw: float, pitch: float) -> tuple[float, float]:
        """Where the head is pointing, in normalised screen coordinates, clamped to the screen."""
        if not self.measured:
            return 0.5, 0.5
        x, y = self.project(yaw, pitch)
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def project(self, yaw: float, pitch: float) -> tuple[float, float]:
        """Where the head points, unclamped."""
        u, v = basis(yaw, pitch)
        denominator = self.perspective[0] * u + self.perspective[1] * v + 1.0
        if abs(denominator) < _HORIZON:
            denominator = math.copysign(_HORIZON, denominator or 1.0)
        return (
            (self.x[0] * u + self.x[1] * v + self.x[2]) / denominator,
            (self.y[0] * u + self.y[1] * v + self.y[2]) / denominator,
        )

    def residual_px(self, width: int, height: int) -> float:
        """The fit residual in pixels, for saying out loud."""
        return _in_pixels(self.residual, width, height)

    @property
    def expected_error(self) -> float:
        """How far off this mapping is where it was *not* fitted."""
        spare = 2 * self.samples - COEFFICIENTS
        return math.inf if spare <= 0 else self.residual * math.sqrt(COEFFICIENTS / spare)

    def expected_error_px(self, width: int, height: int) -> float:
        """expected_error, in pixels."""
        return _in_pixels(self.expected_error, width, height)

    @classmethod
    def fit(cls, samples: Sequence[Sample]) -> ScreenMap:
        """Least squares through the dots."""
        if len(samples) < MINIMUM_SAMPLES:
            raise ValueError(
                f"{len(samples)} calibration points is not enough to fit a mapping; "
                f"{MINIMUM_SAMPLES} is the minimum and nine is the usual grid"
            )
        poses = np.array([basis(s.yaw, s.pitch) for s in samples], dtype=np.float64)
        spread = np.column_stack([poses, np.ones(len(samples))])
        if np.linalg.matrix_rank(spread) < 3:
            raise ValueError(
                "the calibration points do not pin down a mapping: they lie in a line, or the "
                "head held the same pose for several dots. Run it again and look at each dot."
            )

        rows, wanted = [], []
        for sample, (u, v) in zip(samples, poses, strict=True):
            rows.append([u, v, 1, 0, 0, 0, -sample.x * u, -sample.x * v])
            rows.append([0, 0, 0, u, v, 1, -sample.y * u, -sample.y * v])
            wanted += [sample.x, sample.y]
        design = np.array(rows, dtype=np.float64)
        if np.linalg.matrix_rank(design) < COEFFICIENTS:
            raise ValueError(
                "the calibration points do not pin down a mapping: they lie in a line, or the "
                "head held the same pose for several dots. Run it again and look at each dot."
            )
        h, *_ = np.linalg.lstsq(design, np.array(wanted, dtype=np.float64), rcond=None)
        h = _refine(h, poses, np.array([(s.x, s.y) for s in samples], dtype=np.float64))

        origin = np.mean([s.translation for s in samples], axis=0)
        mapping = cls(
            x=(float(h[0]), float(h[1]), float(h[2])),
            y=(float(h[3]), float(h[4]), float(h[5])),
            perspective=(float(h[6]), float(h[7])),
            samples=len(samples),
            origin=(float(origin[0]), float(origin[1]), float(origin[2])),
        )
        errors = [math.dist(mapping.project(s.yaw, s.pitch), (s.x, s.y)) for s in samples]
        return replace(mapping, residual=math.sqrt(sum(e * e for e in errors) / len(errors)))

    @classmethod
    def from_geometry(cls, width_mm: float, height_mm: float, distance_mm: float) -> ScreenMap:
        """The mapping a tape measure gives for a square, centred screen."""
        for name, value in (
            ("width_mm", width_mm),
            ("height_mm", height_mm),
            ("distance_mm", distance_mm),
        ):
            if value <= 0:
                raise ValueError(f"{name} is {value}; a screen has a positive {name[:-3]}")

        return cls(
            x=(-distance_mm / width_mm, 0.0, 0.5),
            y=(0.0, distance_mm / height_mm, 0.5),
        )

    def as_config(self) -> dict[str, object]:
        """The block a configuration carries, as ht_screen_calibration takes it."""
        return {
            "x": [round(c, 6) for c in self.x],
            "y": [round(c, 6) for c in self.y],
            "perspective": [round(c, 6) for c in self.perspective],
            "residual": round(self.residual, 5),
            "samples": self.samples,
            "origin": [round(c, 4) for c in self.origin],
        }


INSET = 0.08

DEFAULT_SIDE = 3

MAX_SIDE = 6

GRID: tuple[tuple[float, float], ...] = (
    (0.5, 0.5),
    (0.08, 0.08),
    (0.92, 0.08),
    (0.92, 0.92),
    (0.08, 0.92),
    (0.5, 0.08),
    (0.92, 0.5),
    (0.5, 0.92),
    (0.08, 0.5),
)


def grid_of(side: int) -> tuple[tuple[float, float], ...]:
    """A side-by-side grid of dots to point at, in asking order."""
    if not DEFAULT_SIDE <= side <= MAX_SIDE:
        raise ValueError(
            f"a calibration grid is {DEFAULT_SIDE} to {MAX_SIDE} dots a side; {side} is not. "
            f"Below {DEFAULT_SIDE} there are too few dots to leave the fit anything spare, "
            f"and above {MAX_SIDE} it is a minute and a half of holding a head still for a "
            "few pixels"
        )
    if side == DEFAULT_SIDE:
        return GRID

    step = (1.0 - 2 * INSET) / (side - 1)
    cells = [(column, row) for row in range(side) for column in range(side)]
    middle = min(cells, key=lambda cell: math.dist(cell, ((side - 1) / 2,) * 2))
    rest = [cell for cell in cells if cell != middle]
    ordered = [middle] + [c for c in rest if sum(c) % 2 == sum(middle) % 2]
    ordered += [c for c in rest if sum(c) % 2 != sum(middle) % 2]
    return tuple((INSET + column * step, INSET + row * step) for column, row in ordered)


@dataclass
class Settling:
    """Whether the head has stopped moving on a dot, and for how long."""

    tolerance: float = 1.2
    hold: float = 0.8
    travel: float = TRAVEL_DEGREES
    min_cutoff_hz: float = 1.0
    beta: float = 0.05

    _filter: OneEuroFilter = field(init=False)
    _at: float | None = field(default=None, init=False)
    _arrived: tuple[float, float] | None = field(default=None, init=False)
    _moved: bool = field(default=False, init=False)
    _since: float | None = field(default=None, init=False)
    _anchor: tuple[float, float] | None = field(default=None, init=False)
    _total: tuple[float, float] = field(default=(0.0, 0.0), init=False)
    _frames: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._filter = OneEuroFilter(self.min_cutoff_hz, self.beta)
        self._moved = self.travel <= 0.0

    def feed(self, at: float, yaw: float, pitch: float) -> float:
        """Report the pose; return how long it has been still, in seconds."""
        dt = 0.0 if self._at is None else at - self._at
        self._at = at
        yaw, pitch = self._filter.filter(yaw, pitch, dt)

        if self._arrived is None:
            self._arrived = (yaw, pitch)
        elif not self._moved and (
            math.hypot(yaw - self._arrived[0], pitch - self._arrived[1]) > self.travel
        ):
            self._moved = True

        if (
            self._anchor is None
            or math.hypot(yaw - self._anchor[0], pitch - self._anchor[1]) > self.tolerance
        ):
            self._anchor = (yaw, pitch)
            self._since = at
            self._total, self._frames = (yaw, pitch), 1
            return 0.0

        self._total = (self._total[0] + yaw, self._total[1] + pitch)
        self._frames += 1
        if not self._moved:
            return 0.0
        return at - (self._since if self._since is not None else at)

    def settled(self, at: float, yaw: float, pitch: float) -> bool:
        """Whether the pose has now been still long enough to record this dot."""
        return self.feed(at, yaw, pitch) >= self.hold

    @property
    def pose(self) -> tuple[float, float] | None:
        """The mean pose over the dwell so far, or None before there is one."""
        if self._frames == 0:
            return None
        return self._total[0] / self._frames, self._total[1] / self._frames

    def reset(self) -> None:
        """Forget the dwell, for a frame with no face in it."""
        self._filter.reset()
        self._at = None
        self._since = None
        self._anchor = None
        self._total, self._frames = (0.0, 0.0), 0
