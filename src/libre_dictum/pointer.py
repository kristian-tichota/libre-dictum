from __future__ import annotations

import math

from .mathutil import clamp, ellipse_boundary
from .settings import PointerSettings
from .tracking.screenmap import WHOLE_DESKTOP, ScreenBounds, ScreenMap


class PointerFilter:
    """Filters (yaw, pitch) degrees off neutral into a relative pointer movement."""

    def __init__(self, settings: PointerSettings) -> None:
        self.settings = settings

    def at_rest(self, yaw: float, pitch: float) -> bool:
        """Whether the pose is inside the dead zone, whatever the mode's other settings say."""
        settings = self.settings
        boundary = ellipse_boundary(yaw, pitch, settings.dead_angle_h, settings.dead_angle_v)
        return math.hypot(yaw, pitch) <= boundary

    def filter(self, yaw: float, pitch: float, dt: float) -> tuple[float, float] | None:
        """Return the movement to apply this frame, or None when nothing should move."""
        settings = self.settings
        if not settings.enabled or dt <= 0:
            return None

        magnitude = math.hypot(yaw, pitch)
        boundary = ellipse_boundary(yaw, pitch, settings.dead_angle_h, settings.dead_angle_v)
        excess = magnitude - boundary
        if excess <= 0:
            return None

        span = settings.full_speed_angle - boundary
        fraction = clamp(excess / span, 0.0, 1.0) if span > 0 else 1.0
        step = settings.max_speed_px_per_sec * fraction**settings.speed_power * dt

        dx = step * yaw / magnitude
        dy = step * pitch / magnitude

        if settings.invert_x:
            dx = -dx
        if settings.invert_y:
            dy = -dy

        return -dx, dy


class AbsolutePointer:
    """Maps a head pose straight onto a point on the screen."""

    def __init__(
        self,
        settings: PointerSettings,
        mapping: ScreenMap,
        placement: ScreenBounds = WHOLE_DESKTOP,
    ) -> None:
        self.settings = settings
        self.mapping = mapping
        self.placement = placement

    @property
    def usable(self) -> bool:
        """Whether there is a calibration to point with."""
        return self.settings.enabled and self.mapping.measured

    def at(self, yaw: float, pitch: float) -> tuple[float, float] | None:
        """Where the pointer belongs, in normalised screen coordinates, or None."""
        if not self.usable:
            return None
        x, y = self.mapping.at(yaw, pitch)
        if self.settings.invert_x:
            x = 1.0 - x
        if self.settings.invert_y:
            y = 1.0 - y
        return self.placement.into_desktop(x, y)
