from __future__ import annotations

import math


def clamp(value: float, lowest: float, highest: float) -> float:
    """Confine value to [lowest, highest]."""
    return max(lowest, min(highest, value))


def ellipse_boundary(dx: float, dy: float, radius_h: float, radius_v: float) -> float:
    """How far an axis-aligned ellipse's edge is from its centre, along (dx, dy)."""
    magnitude = math.hypot(dx, dy)
    if magnitude == 0:
        return 0.0

    scale = math.hypot(
        _axis_scale(abs(dx) / magnitude, radius_h), _axis_scale(abs(dy) / magnitude, radius_v)
    )
    return 0.0 if math.isinf(scale) or scale == 0 else 1.0 / scale


def _axis_scale(component: float, radius: float) -> float:
    """One axis's contribution to the ellipse equation, with a zero radius meaning no extent."""
    if radius > 0:
        return component / radius
    return math.inf if component else 0.0
