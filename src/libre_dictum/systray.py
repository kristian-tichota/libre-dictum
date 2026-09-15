from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

from PIL import Image, ImageDraw

from .layers import Layer, LayerState, SleepView
from .status import Grip, HeldInput, HeldKey, canonical

logger = logging.getLogger(__name__)

ICON_SIZE = 256

RGB = tuple[int, int, int]
RGBA = tuple[int, int, int, int]

Box = tuple[float, float, float, float]
Path = Sequence[tuple[float, float]]

FAULT_COLOUR: RGB = (220, 50, 47)

_WHITE = (255, 255, 255, 255)

MAX_GLYPHS = 3

_OPACITY = {Grip.HELD: 255, Grip.PENDING: 175}

LAYER_ARCS: dict[Layer, tuple[float, float]] = {
    Layer.GESTURE: (188.0, 258.0),
    Layer.PEDAL: (282.0, 352.0),
}

_ARC_WIDTH = 0.12


def mode_image(rgb: RGB, size: int = ICON_SIZE) -> Image.Image:
    """A filled circle in a mode's colour."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((0, 0, size - 1, size - 1), fill=rgb)
    return image


ASLEEP_BRIGHTNESS = 0.3


def dimmed(image: Image.Image, factor: float = ASLEEP_BRIGHTNESS) -> Image.Image:
    """The same icon with the light turned down, keeping its shape and its alpha."""
    red, green, blue, alpha = image.convert("RGBA").split()
    scaled = [band.point(lambda value: int(value * factor)) for band in (red, green, blue)]
    return Image.merge("RGBA", (*scaled, alpha))


def fault_image(size: int = ICON_SIZE) -> Image.Image:
    """The fault indicator: an exclamation mark on a red disc."""
    image = mode_image(FAULT_COLOUR, size)
    draw = ImageDraw.Draw(image)

    centre, bar = size // 2, size // 10
    draw.rounded_rectangle(
        (centre - bar, size // 5, centre + bar, size * 3 // 5), radius=bar, fill=_WHITE
    )
    draw.ellipse(
        (centre - bar, size * 7 // 10, centre + bar, size * 7 // 10 + 2 * bar), fill=_WHITE
    )
    return image


def base_image(mode: Image.Image | None, *, fault: bool, asleep: bool) -> Image.Image | None:
    """The disc the icon is drawn on, or None when blank."""
    if fault:
        base = fault_image()
    elif mode is not None:
        base = mode
    else:
        return None
    return dimmed(base) if asleep else base


def _ink(image: Image.Image, opacity: int) -> tuple[RGBA, RGBA]:
    """Glyph and halo colours that stay visible on image."""
    sample = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
    channels = sample if isinstance(sample, tuple) else (0, 0, 0)
    luminance = 0.299 * channels[0] + 0.587 * channels[1] + 0.114 * channels[2]
    light, dark = (255, 255, 255, opacity), (20, 20, 20, opacity)
    return (dark, light) if luminance > 140 else (light, dark)


STROKE, AREA = "stroke", "area"
Shape = tuple[str, Path]

GLYPHS: dict[str, list[Shape]] = {
    "shift": [
        (
            AREA,
            [
                (0.5, 0.04),
                (0.98, 0.52),
                (0.7, 0.52),
                (0.7, 0.96),
                (0.3, 0.96),
                (0.3, 0.52),
                (0.02, 0.52),
            ],
        )
    ],
    "ctrl": [(STROKE, [(0.06, 0.72), (0.5, 0.2), (0.94, 0.72)])],
    "alt": [(STROKE, [(0.04, 0.84), (0.36, 0.84), (0.7, 0.18), (0.96, 0.18)])],
    "meta": [(AREA, [(0.5, 0.04), (0.96, 0.5), (0.5, 0.96), (0.04, 0.5)])],
}

OTHER_GLYPH: list[Shape] = [
    (STROKE, [(0.08, 0.2), (0.92, 0.2), (0.92, 0.8), (0.08, 0.8), (0.08, 0.2)])
]
MORE_GLYPH: list[Shape] = [
    (STROKE, [(0.1, 0.5), (0.9, 0.5)]),
    (STROKE, [(0.5, 0.1), (0.5, 0.9)]),
]


def draw_glyph(
    draw: ImageDraw.ImageDraw, box: Box, shapes: Sequence[Shape], ink: RGBA, halo: RGBA
) -> None:
    """Draw one glyph in box, outlined in halo so it survives what is under it."""
    left, top, side = box[0], box[1], box[2] - box[0]
    width = max(2, round(0.15 * side))

    for kind, points in shapes:
        scaled = [(left + x * side, top + y * side) for x, y in points]
        if kind is STROKE:
            draw.line(scaled, fill=halo, width=width + 2 * max(1, width // 3), joint="curve")
            draw.line(scaled, fill=ink, width=width, joint="curve")
        else:
            draw.polygon(scaled, fill=ink, outline=halo, width=max(1, width // 3))


def _boxes(count: int, size: int) -> list[Box]:
    """Where count glyphs go: a centred row, sized to stay inside the circle."""
    side = {1: 0.52, 2: 0.36, 3: 0.26}[count] * size
    gap = 0.04 * size
    span = count * side + (count - 1) * gap
    top = (size - side) / 2
    left = (size - span) / 2
    return [
        (left + i * (side + gap), top, left + i * (side + gap) + side, top + side)
        for i in range(count)
    ]


def with_held_keys(
    base: Image.Image, keys: Sequence[HeldKey], size: int = ICON_SIZE
) -> Image.Image:
    """base with a glyph drawn on it for each held key."""
    if not keys:
        return base

    shown = list(keys[: MAX_GLYPHS - 1]) + [None] if len(keys) > MAX_GLYPHS else list(keys)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    for box, key in zip(_boxes(len(shown), size), shown, strict=True):
        opacity = _OPACITY[Grip.HELD] if key is None else _OPACITY[key.grip]
        shapes = MORE_GLYPH if key is None else GLYPHS.get(canonical(key.key), OTHER_GLYPH)
        draw_glyph(draw, box, shapes, *_ink(base, opacity))

    return Image.alpha_composite(base.convert("RGBA"), layer)


def with_layer_arcs(
    base: Image.Image,
    arcs: Sequence[tuple[Layer, RGB | None]],
    size: int = ICON_SIZE,
) -> Image.Image:
    """base with a rim arc per layer away from voice."""
    if not arcs:
        return base

    image = base.convert("RGBA")
    draw = ImageDraw.Draw(image)
    width = max(2, round(_ARC_WIDTH * size))
    inset = width / 2
    box = (inset, inset, size - 1 - inset, size - 1 - inset)

    for layer, rgb in arcs:
        start, end = LAYER_ARCS[layer]
        fill = (*rgb, 255) if rgb is not None else _ink(base, 255)[0]
        draw.arc(box, start, end, fill=fill, width=width)
    return image


class RGBTrayIcon:
    """A tray icon of precomputed mode colours, or a fault."""

    def __init__(self, name: str = "libre-dictum") -> None:
        from pystray import Icon  # deferred: importing pystray probes the display

        self.name = name
        self.icon = Icon(name, title=name)
        self.images: dict[str, Image.Image] = {}
        self.colours: dict[str, RGB] = {}
        self.current_mode: str | None = None
        self.layers = LayerState()
        self.fault: str | None = None
        self.sleep = SleepView()
        self.held = HeldInput()
        self._thread: threading.Thread | None = None

    def add_mode(self, key: str, rgb: RGB | None) -> None:
        """Register a mode's colour."""
        if rgb is None:
            return
        if len(rgb) != 3 or not all(0 <= channel <= 255 for channel in rgb):
            raise ValueError(f"RGB must be three integers between 0 and 255, got {rgb!r}")
        self.colours[key] = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        self.images[key] = mode_image(rgb)

    def set_layers(self, layers: LayerState) -> None:
        """Show which mode each layer is in."""
        if layers == self.layers:
            return
        self.layers = layers
        if layers.voice in self.images:
            self.current_mode = layers.voice
        self._repaint()

    def set_sleep(self, view: SleepView) -> None:
        """Dim the disc while anything is asleep, and say which in the tooltip."""
        if view == self.sleep:
            return
        self.sleep = view
        self._repaint()

    def set_fault(self, reason: str) -> None:
        """Show that something is broken, whatever mode is active."""
        self.fault = reason
        self._repaint()

    def clear_fault(self) -> None:
        """Go back to showing the active mode."""
        self.fault = None
        self._repaint()

    def set_held(self, held: HeldInput) -> None:
        """Show which keys are still down, over whatever the icon shows now."""
        if held == self.held:
            return
        self.held = held
        self._repaint()

    def show(self) -> None:
        """Run the tray icon on its own thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.icon.run, name="systray", daemon=True)
        self._thread.start()

    def hide(self) -> None:
        """Remove the icon from the tray."""
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001 - never let teardown of a cosmetic feature raise
            logger.debug("Tray icon did not stop cleanly", exc_info=True)
        self._thread = None

    def _repaint(self) -> None:
        base = self._base_image()
        if base is not None:
            self.icon.icon = with_held_keys(
                with_layer_arcs(base, self._arcs()), self.held.pressed()
            )
        self.icon.title = self.title

    def _arcs(self) -> list[tuple[Layer, RGB | None]]:
        """Each layer that is somewhere else, with the colour of the mode it is in."""
        return [(layer, self.colours.get(mode)) for layer, mode in self.layers.diverged]

    def _base_image(self) -> Image.Image | None:
        mode = self.images.get(self.current_mode) if self.current_mode is not None else None
        return base_image(mode, fault=self.fault is not None, asleep=self.sleep.dozing)

    @property
    def title(self) -> str:
        """The tooltip: where every layer is, what is held, and what is broken."""
        parts = [self.name]
        layers = self.layers.summary()
        if layers:
            parts.append(layers)
        elif self.current_mode is not None:
            parts.append(f"mode: {self.current_mode}")
        if self.sleep.dozing:
            parts.append(self.sleep.summary())
        if self.sleep.prompt is not None:
            parts.append(self.sleep.prompt)
        if not self.held.empty:
            parts.append(self.held.summary())
        if self.fault is not None:
            parts.append(f"fault: {self.fault}")
        return " — ".join(parts)
