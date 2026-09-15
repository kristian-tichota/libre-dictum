from __future__ import annotations

import ctypes
import logging
import math
import os
import signal
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..protocol import default_socket_path
from . import compositor, motion, render, style
from .client import HudClient

logger = logging.getLogger(__name__)


def _load_layer_shell(env: Mapping[str, str]) -> str | None:
    """dlopen the shim, before anything in this process has heard of libwayland."""
    for name in compositor.libraries(env):
        try:
            ctypes.CDLL(name, mode=ctypes.RTLD_GLOBAL)
        except OSError as exc:
            logger.debug("Could not load %s (%s)", name, exc)
        else:
            logger.debug("Loaded the layer-shell shim from %s", name)
            return name
    return None


LIBRARY = _load_layer_shell(os.environ)

import gi  # noqa: E402 - the shim above has to be loaded first, and it is why

gi.require_version("Gtk", "4.0")

from gi.repository import GLib, Gtk  # noqa: E402 - gi requires the version first

try:
    import cairo
except ImportError:  # pragma: no cover - pycairo ships with the overlay extra
    cairo = None

EDGES = {
    "top-right": ("TOP", "RIGHT"),
    "top-left": ("TOP", "LEFT"),
    "bottom-right": ("BOTTOM", "RIGHT"),
    "bottom-left": ("BOTTOM", "LEFT"),
    "right": ("TOP", "RIGHT"),
    "left": ("TOP", "LEFT"),
}

MARGIN = 12

SHEET_WIDTH = 380

INDEX_COLUMNS = 2


def _layer_shell() -> Any:
    """The gtk4-layer-shell binding, or None when there is none to import."""
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gtk4LayerShell

        return Gtk4LayerShell
    except (ImportError, ValueError) as exc:
        logger.debug("No Gtk4LayerShell typelib (%s)", exc)
        return None


class Shell:
    """The shim as the rest of this file needs it: usable, or explained."""

    def __init__(self, library: str | None = LIBRARY) -> None:
        self.library = library
        self.module = _layer_shell()
        self.binding = self.module is not None
        self.supported = bool(self.binding and self.module.is_supported())
        self.version = 0
        if self.supported:
            reader = getattr(self.module, "get_protocol_version", None)
            self.version = int(reader()) if reader is not None else 0

    @property
    def usable(self) -> Any:
        """The module to hand a surface, or None to make it an ordinary window."""
        return self.module if self.supported else None


class Surface:
    """One layer-shell window: on the overlay layer, focusless and click-through."""

    def __init__(self, shell: Any, *, anchor: str, width: int = 0, title: str = "") -> None:
        self.window = Gtk.Window()
        self.window.set_name(style.WIDGET_NAME)
        self.window.set_decorated(False)
        self.window.set_title(title or compositor.APP_ID)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.body.add_css_class("surface")
        self.window.set_child(self.body)
        if width:
            self.window.set_default_size(width, 1)

        self._shell = shell
        self._complained = False
        self._misplaced = False
        self.anchor = anchor
        self._edge = EDGES.get(anchor, EDGES["top-right"])[1]
        self._configure(shell, anchor)
        self.window.connect("realize", lambda _window: self.pass_input_through())

    def _configure(self, shell: Any, anchor: str) -> None:
        if shell is None:
            return
        vertical, horizontal = EDGES.get(anchor, EDGES["top-right"])
        shell.init_for_window(self.window)
        namer = getattr(shell, "set_namespace", None)
        if namer is not None:
            namer(self.window, compositor.NAMESPACE)
        shell.set_layer(self.window, shell.Layer.OVERLAY)
        shell.set_keyboard_mode(self.window, shell.KeyboardMode.NONE)
        shell.set_exclusive_zone(self.window, 0)
        edges = [vertical, horizontal]
        if anchor == "fill":
            edges = ["TOP", "BOTTOM", "LEFT", "RIGHT"]
        elif anchor in {"left", "right"}:
            edges = ["TOP", "BOTTOM", horizontal]
        margin = 0 if anchor == "fill" else MARGIN
        for edge in edges:
            shell.set_anchor(self.window, getattr(shell.Edge, edge), True)
            shell.set_margin(self.window, getattr(shell.Edge, edge), margin)

    def inset(self, distance: float) -> None:
        """Sit distance pixels further in from the edge it is anchored to."""
        if self._shell is None:
            return
        self._shell.set_margin(
            self.window, getattr(self._shell.Edge, self._edge), MARGIN + round(distance)
        )

    def pin(self, monitor: Any) -> None:
        """Put this surface on one output, or let the compositor choose with None."""
        setter = getattr(self._shell, "set_monitor", None)
        if setter is None:
            return
        try:
            setter(self.window, monitor)
        except Exception as exc:  # noqa: BLE001 - a misplaced overlay beats no overlay
            if not self._misplaced:
                self._misplaced = True
                logger.warning("Could not put the overlay on one output: %s", exc)

    @property
    def layered(self) -> bool:
        """Whether this became a layer surface."""
        if self._shell is None:
            return False
        check = getattr(self._shell, "is_layer_window", None)
        if check is None:  # pragma: no cover
            return True
        return bool(check(self.window))

    def pass_input_through(self) -> None:
        """Take the surface out of the pointer's way, and keep it there."""
        if cairo is None:
            if not self._complained:
                self._complained = True
                logger.warning("pycairo is missing, so the overlay will consume clicks")
            return
        try:
            surface = self.window.get_surface()
            if surface is not None:
                surface.set_input_region(cairo.Region())
        except Exception as exc:  # noqa: BLE001 - a solid overlay beats no overlay
            if not self._complained:
                self._complained = True
                logger.warning("Could not make the overlay click-through: %s", exc)

    def visible(self, showing: bool) -> None:
        if showing:
            self.window.present()
            self.pass_input_through()
        else:
            self.window.set_visible(False)


def _label(text: str = "", *classes: str, xalign: float = 0.0) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_xalign(xalign)
    for name in classes:
        label.add_css_class(name)
    return label


def _dot_markup(colour: tuple[int, int, int] | None) -> str:
    """A colour as the same dot the tray draws; grey for a mode with none."""
    red, green, blue = colour or (150, 150, 150)
    return f'<span foreground="#{red:02x}{green:02x}{blue:02x}">●</span>'


def _swatch(colour: tuple[int, int, int] | None) -> Gtk.Label:
    """A label holding one mode colour as a dot."""
    dot = _label("", "glyph")
    dot.set_markup(_dot_markup(colour))
    return dot


class ChipSurface(Surface):
    """Mode, the other layers when they differ, held keys, hearing, the last utterance, faults."""

    def __init__(self, shell: Any, *, anchor: str = "top-right", animate: bool = True) -> None:
        super().__init__(shell, anchor=anchor, title=f"{compositor.APP_ID} chip")
        self._room = motion.Slide(duration=motion.DURATION_MS if animate else 0.0)
        self._ticking = False

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._swatch = _swatch(None)
        self._mode = _label(render.WAITING, "mode")
        self._glyphs = _label("", "glyph", xalign=1.0)
        self._glyphs.set_hexpand(True)
        for widget in (self._swatch, self._mode, self._glyphs):
            top.append(widget)

        heard = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._hearing = _label("", "hearing")
        self._utterance = _label("", "quiet")
        self._utterance.set_ellipsize(3)
        for widget in (self._hearing, self._utterance):
            heard.append(widget)

        self._layers = _label("", "axis")
        self._gestures = _label("", "hearing")
        self._gestures.set_ellipsize(3)
        self._sleep = _label("", "sleep")
        self._fault = _label("", "fault")
        self._note = _label("", "dim")
        for widget in (
            top,
            self._layers,
            self._sleep,
            self._gestures,
            heard,
            self._fault,
            self._note,
        ):
            self.body.append(widget)

    def draw(self, chip: render.Chip) -> None:
        """Repaint in place."""
        self._swatch.set_markup(_dot_markup(chip.swatch))
        self._mode.set_text(chip.mode)
        self._glyphs.set_text(chip.glyphs)
        layers = chip.layer_line
        self._layers.set_text(layers)
        self._layers.set_visible(bool(layers))
        gestures = chip.gesture_line
        self._gestures.set_text(gestures)
        self._gestures.set_visible(bool(gestures))
        sleep = chip.sleep_line
        self._sleep.set_text(sleep)
        self._sleep.set_visible(bool(sleep))
        self._hearing.set_text(chip.mark)
        self._utterance.set_text(chip.partial or chip.utterance)
        _classify(self._utterance, "miss", chip.missed and not chip.hearing)
        self._fault.set_text(chip.fault or "")
        self._fault.set_visible(chip.fault is not None)
        self._note.set_text(chip.note or "")
        self._note.set_visible(chip.note is not None)
        self.window.set_tooltip_text(chip.tooltip() or None)

    def make_room(self, distance: float) -> None:
        """Ease the chip distance pixels clear of the sheet, or back again."""
        if self._room.aim(distance, now=_milliseconds()) and not self._ticking:
            self._ticking = True
            self.window.add_tick_callback(self._step)

    def _step(self, _widget: Any, clock: Any) -> bool:
        """One frame of the slide, on GTK's frame clock."""
        self.inset(self._room.advance(clock.get_frame_time() / 1000.0))
        self._ticking = self._room.moving
        return GLib.SOURCE_CONTINUE if self._ticking else GLib.SOURCE_REMOVE


def _milliseconds() -> float:
    """The clock the frame clock is on, in the units motion counts in."""
    return GLib.get_monotonic_time() / 1000.0


def _classify(widget: Gtk.Widget, name: str, wanted: bool) -> None:
    if wanted:
        widget.add_css_class(name)
    else:
        widget.remove_css_class(name)


class CalibrationSurface(Surface):
    """One dot at a time, over the whole screen, while the screen mapping is measured."""

    def __init__(self, shell: Any) -> None:
        super().__init__(shell, anchor="fill", title=f"{compositor.APP_ID} calibration")
        self.body.add_css_class("calibration")
        self._area = Gtk.DrawingArea()
        self._area.set_hexpand(True)
        self._area.set_vexpand(True)
        self._area.set_draw_func(self._paint)
        self.body.append(self._area)
        self._target: render.Target | None = None

    def draw(self, target: render.Target | None) -> None:
        """Show one dot, or nothing."""
        if target == self._target:
            return
        self._target = target
        self._area.queue_draw()

    def pass_input_through(self) -> None:
        """Keep the surface in the pointer's way."""

    def _paint(self, _area: Any, context: Any, width: int, height: int) -> None:
        target = self._target
        if target is None or cairo is None:
            return
        x, y = target.x * width, target.y * height
        radius = max(6.0, min(width, height) * 0.012)

        context.set_source_rgba(0.0, 0.0, 0.0, 0.55)
        context.paint()

        if target.settled > 0.0:
            context.set_source_rgba(0.45, 0.80, 1.0, 0.9)
            context.set_line_width(radius * 0.5)
            start = -math.pi / 2
            context.arc(x, y, radius * 2.2, start, start + 2 * math.pi * target.settled)
            context.stroke()

        context.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        context.arc(x, y, radius, 0, 2 * math.pi)
        context.fill()
        context.set_source_rgba(0.1, 0.1, 0.1, 1.0)
        context.arc(x, y, radius * 0.28, 0, 2 * math.pi)
        context.fill()

        _caption(context, width, height, target)


def _caption(context: Any, width: int, height: int, target: render.Target) -> None:
    """The count and the instruction, centred, well away from every dot position."""
    context.set_source_rgba(1.0, 1.0, 1.0, 0.85)
    context.select_font_face("sans-serif", 0, 0)
    for text, size, offset in (
        (target.hint, max(14.0, height * 0.022), -0.5),
        (target.caption, max(11.0, height * 0.016), 1.4),
    ):
        context.set_font_size(size)
        extents = context.text_extents(text)
        context.move_to(width / 2 - extents.width / 2, height * 0.5 + size * offset)
        context.show_text(text)


class SheetSurface(Surface):
    """The group index, or one group, docked at full height."""

    def __init__(self, shell: Any, *, anchor: str = "right", width: int = SHEET_WIDTH) -> None:
        super().__init__(shell, anchor=anchor, width=width, title=f"{compositor.APP_ID} sheet")
        self._asked = width
        self._title = _label("", "title")
        self._hint = _label("", "phrase")
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroller.set_child(self._content)
        self._notes = _label("", "note")
        self._notes.set_visible(False)
        self._footer = _label("", "dim")
        self._footer.set_wrap(True)
        for widget in (self._title, self._hint, scroller, self._notes, self._footer):
            self.body.append(widget)
        self._drawn: render.Panel | None = None

    def span(self) -> int:
        """The sheet's width on screen, allocated or measured."""
        allocated = self.window.get_width()
        if allocated:
            return allocated
        _, natural, _, _ = self.window.measure(Gtk.Orientation.HORIZONTAL, -1)
        return max(natural, self._asked)

    def draw(self, panel: render.Panel) -> None:
        """Rebuild the body, but only when the panel is genuinely a different one."""
        if panel == self._drawn:
            return
        self._drawn = panel

        self._title.set_text(panel.title)
        self._hint.set_text(panel.hint)
        self._notes.set_text("\n".join(panel.notes))
        self._notes.set_visible(bool(panel.notes))
        self._footer.set_text("   ".join(panel.footer))
        self._footer.set_visible(bool(panel.footer))

        child = self._content.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._content.remove(child)
            child = following

        if panel.layout is render.Layout.INDEX:
            self._content.append(_index_grid(panel))
        elif panel.layout is render.Layout.TABLE:
            self._content.append(_table_grid(panel))
        else:
            for item in panel.items:
                self._content.append(_list_row(item))


def _index_grid(panel: render.Panel) -> Gtk.Grid:
    grid = Gtk.Grid(column_spacing=16, row_spacing=2)
    for position, item in enumerate(panel.items):
        row, column = divmod(position, INDEX_COLUMNS)
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = _label(f"{item.label}  {item.detail}", *(("dim",) if item.dim else ()))
        cell.append(name)
        if item.phrase:
            cell.append(_label(item.phrase, "phrase"))
        grid.attach(cell, column, row, 1, 1)
    return grid


def _table_grid(panel: render.Panel) -> Gtk.Grid:
    grid = Gtk.Grid(column_spacing=10, row_spacing=2)
    offset = 0
    if panel.columns:
        for index, column in enumerate(panel.columns, start=1):
            grid.attach(_label(column, "axis", xalign=0.5), index, 0, 1, 1)
        offset = 1
    for index, row in enumerate(panel.rows):
        grid.attach(_label(row.label, "axis"), 0, index + offset, 1, 1)
        for position, item in enumerate(row.items, start=1):
            grid.attach(_cell(item), position, index + offset, 1, 1)
        if row.phrase:
            grid.attach(_label(row.phrase, "phrase"), panel.width + 1, index + offset, 1, 1)
    return grid


def _cell(item: render.Item) -> Gtk.Label:
    """One table cell."""
    classes = ["dim"] if item.dim else []
    if item.tone:
        classes.extend(("meter", item.tone))
    return _label(item.label, *classes, xalign=0.0 if item.tone else 0.5)


def _list_row(item: render.Item) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    phrase = _label(item.label)
    phrase.set_hexpand(True)
    row.append(phrase)
    row.append(_label(item.detail, "detail", xalign=1.0))
    return row


class Hud:
    """The display process: two surfaces, one socket, one main loop."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        chip_anchor: str = "top-right",
        sheet_anchor: str = "right",
        opacity: float = style.DEFAULT_OPACITY,
        scale: float = style.DEFAULT_SCALE,
    ) -> None:
        # Before the first window: with no GtkApplication this is the xdg-shell app id.
        GLib.set_prgname(compositor.APP_ID)
        GLib.set_application_name("libre-dictum")
        Gtk.init()
        _install_style(opacity, scale)

        self.shell = Shell()
        self.chip = ChipSurface(self.shell.usable, anchor=chip_anchor, animate=_animated())
        self.sheet = SheetSurface(
            self.shell.usable,
            anchor=sheet_anchor,
            width=round(SHEET_WIDTH * style.clamp(scale, style.MIN_SCALE, style.MAX_SCALE)),
        )
        self.calibration = CalibrationSurface(self.shell.usable)
        self.outcome = report(self.shell, self.chip.layered)
        self.output = compositor.Output()
        self._asked: tuple[str, bool] | None = None
        self._unpinnable = False
        self._watch_outputs()
        self.client = HudClient(socket_path or default_socket_path(), on_change=self._changed)
        self._loop = GLib.MainLoop()

    def run(self) -> int:
        """Connect, draw, and block until interrupted."""
        for received in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, received, self._quit)
        self.client.start()
        self._redraw()
        try:
            self._loop.run()
        finally:
            self.client.stop()
        return 0

    def _quit(self) -> bool:
        self._loop.quit()
        return False

    def _changed(self) -> None:
        """A frame arrived, on the reader thread."""
        GLib.idle_add(self._redraw, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _watch_outputs(self) -> None:
        """Re-place the surfaces whenever an output arrives or goes away."""
        monitors = _monitors()
        if monitors is not None:
            monitors.connect("items-changed", lambda *_: self._replaced())

    def _restrict(self, name: str, only: bool) -> None:
        """Take the output the configuration asked for, unless it is the one in force."""
        if (name, only) != self._asked:
            self._asked = (name, only)
            self._replace()

    def _replaced(self) -> None:
        """An output arrived or went away: place the surfaces again, and draw them."""
        self._replace()
        self._redraw()

    def _replace(self) -> None:
        """Pin every surface to the asked-for output, or take them all off screen."""
        name, only = self._asked or ("", True)
        if name and self.shell.usable is None:
            self.output = compositor.Output()
            if not self._unpinnable:
                self._unpinnable = True
                logger.warning("%s", compositor.explain_unpinnable(name))
            return
        present = dict(_outputs())
        self.output = compositor.output_for(tuple(present), name=name, only=only)
        if name and self.output.connector is None:
            logger.warning("%s", compositor.explain_absent(tuple(present), name=name, only=only))
        monitor = present.get(self.output.connector or "")
        for surface in (self.chip, self.sheet, self.calibration):
            surface.pin(monitor)

    def _redraw(self) -> bool:
        state, catalogue = self.client.state, self.client.catalogue
        if catalogue is not None:
            self._restrict(catalogue.output, catalogue.output_only)
        allowed = self.output.visible

        chip = render.chip_of(state, catalogue)
        self.chip.draw(chip)
        self.chip.visible(allowed and (state is None or state.chip))

        panel = render.panel_of(state, catalogue)
        if panel is not None:
            self.sheet.draw(panel)
        self.sheet.visible(allowed and panel is not None)
        self.chip.make_room(self._room_for(panel))

        target = render.target_of(state)
        self.calibration.draw(target)
        self.calibration.visible(allowed and target is not None)
        return False

    def _room_for(self, panel: render.Panel | None) -> float:
        """How far in the chip belongs while panel is showing."""
        if panel is None:
            return 0.0
        return motion.clearance(
            self.chip.anchor, self.sheet.anchor, width=self.sheet.span(), gap=MARGIN
        )


def _monitors() -> Any:
    """The session's list of outputs, or None when there is no display to ask."""
    from gi.repository import Gdk

    display = Gdk.Display.get_default()
    return None if display is None else display.get_monitors()


def _outputs() -> tuple[tuple[str, Any], ...]:
    """Every output this session has, as (connector name, monitor), GDK's order."""
    monitors = _monitors()
    if monitors is None:
        return ()
    found = (monitors.get_item(index) for index in range(monitors.get_n_items()))
    return tuple((one.get_connector(), one) for one in found if one.get_connector())


def outputs() -> int:
    """Print the name of every video output, which is what overlay.output.name takes."""
    if not Gtk.init_check():
        print("no display -- GTK could not open one, so no output can be named")
        return 1
    found = _outputs()
    for name, monitor in found:
        area = monitor.get_geometry()
        rate = monitor.get_refresh_rate()
        made = " ".join(filter(None, (monitor.get_manufacturer(), monitor.get_model())))
        print(
            f"{name:<10} {area.width}x{area.height}+{area.x}+{area.y}  "
            f"{f'{rate / 1000:g} Hz  ' if rate else ''}{made}".rstrip()
        )
    if not found:
        print("no output reported a name")
    return 0 if found else 1


def _animated() -> bool:
    """Whether this desktop asked for animation at all."""
    settings = Gtk.Settings.get_default()
    return True if settings is None else bool(settings.get_property("gtk-enable-animations"))


def report(shell: Shell, applied: bool, env: Mapping[str, str] | None = None) -> compositor.Outcome:
    """Say where the surfaces ended up, once, and loudly when it is the wrong place."""
    session = compositor.Session.from_env(os.environ if env is None else env)
    outcome = compositor.outcome_of(
        session,
        library=shell.library,
        binding=shell.binding,
        supported=shell.supported,
        applied=applied,
        version=shell.version,
    )
    logger.log(
        logging.ERROR if outcome.degraded else logging.INFO,
        "%s",
        compositor.explain(session, outcome),
    )
    return outcome


def probe(opacity: float = style.DEFAULT_OPACITY, scale: float = style.DEFAULT_SCALE) -> int:
    """Print what the display got from this session, and exit non-zero if it is less."""
    session = compositor.Session.from_env(os.environ)
    if not Gtk.init_check():
        print(f"session:   {session.where}")
        print("display:   none -- GTK could not open one, so there is nothing to probe")
        return 1

    shell = Shell()
    _install_style(opacity, scale)
    surface = ChipSurface(shell.usable, anchor="top-right")
    outcome = compositor.outcome_of(
        session,
        library=shell.library,
        binding=shell.binding,
        supported=shell.supported,
        applied=surface.layered,
        version=shell.version,
    )
    protocol = f"zwlr_layer_shell_v1 v{shell.version}" if shell.supported else "none offered"
    print(f"session:   {session.where}")
    print(f"shim:      {shell.library or 'not loaded'}")
    print(f"binding:   {'Gtk4LayerShell-1.0' if shell.binding else 'no typelib'}")
    print(f"protocol:  {protocol}")
    print(f"placement: {outcome.placement} ({outcome.trouble})")
    print(f"app id:    {compositor.APP_ID}")
    print(f"opacity:   {style.clamp(opacity)}")
    print(f"scale:     {style.clamp(scale, style.MIN_SCALE, style.MAX_SCALE)}")
    print()
    print(compositor.explain(session, outcome))
    return 1 if outcome.degraded else 0


def _install_style(
    opacity: float = style.DEFAULT_OPACITY, scale: float = style.DEFAULT_SCALE
) -> None:
    from gi.repository import Gdk

    provider = Gtk.CssProvider()
    provider.load_from_data(style.stylesheet(opacity, scale).encode())
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
