from __future__ import annotations

WIDGET_NAME = "libre-dictum"

SURFACE_RGB = (20, 20, 24)

DEFAULT_OPACITY = 0.65

EDGE_OF_OPACITY = 0.22

DEFAULT_SCALE = 1.2

MIN_SCALE, MAX_SCALE = 0.5, 3.0

TONE_LIVE = "hearing"
TONE_BLOCKED = "miss"
TONE_MEASURED = "detail"


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """A dial CSS will accept."""
    return min(high, max(low, value))


def stylesheet(opacity: float = DEFAULT_OPACITY, scale: float = DEFAULT_SCALE) -> str:
    """The whole stylesheet: the panel's dial, and the text's."""
    panel = clamp(opacity)
    edge = round(panel * EDGE_OF_OPACITY, 3)
    text = round(clamp(scale, MIN_SCALE, MAX_SCALE) * 100)
    red, green, blue = SURFACE_RGB
    return f"""\
/* The window paints nothing: the theme's opaque window background is the "solid block"
   the surfaces must never be, and an id beats the theme's class. */
window#{WIDGET_NAME} {{
  background-color: transparent;
  background-image: none;
  box-shadow: none;
}}
box.surface {{
  font-size: {text}%;
  background-color: rgba({red}, {green}, {blue}, {panel});
  border: 1px solid rgba(255, 255, 255, {edge});
  border-radius: 10px;
  padding: 8px 12px;
}}
/* The shadow is what is left to read the text against once the panel is turned down. */
label {{
  color: #e8e8ea;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.95);
}}
label.mode {{ font-weight: bold; }}
label.glyph {{ font-size: 115%; }}
label.quiet, label.dim {{ color: rgba(232, 232, 234, 0.62); }}
label.hearing {{ color: #7fd1a0; }}
label.miss {{ color: #e0a33c; }}
label.fault {{ color: #f05a4f; font-weight: bold; }}
label.title {{ font-weight: bold; font-size: 120%; letter-spacing: 1px; }}
label.axis {{ color: rgba(232, 232, 234, 0.72); font-size: 92%; }}
label.phrase {{ color: #8ab4f8; font-family: monospace; font-size: 92%; }}
label.detail {{ color: rgba(232, 232, 234, 0.66); font-family: monospace; font-size: 88%; }}
/* A meter is read as a column of numbers settling, so it is monospace whatever tone it is
   drawn in -- a proportional digit makes a bar jitter as its value changes. */
label.meter {{ font-family: monospace; font-size: 92%; }}
label.note {{ color: rgba(232, 232, 234, 0.66); font-family: monospace; font-size: 88%; }}
/* Sleep is not a fault and must not read as one: a mechanism deliberately put beyond reach
   is the calmest thing on this display. Italic and dim rather than coloured, because the
   dimmed mode dot beside it is already carrying the weight. */
label.sleep {{ color: rgba(232, 232, 234, 0.70); font-style: italic; font-size: 92%; }}
"""
