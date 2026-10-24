from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol

from . import instance, scheduling
from .control.protocol import default_socket_path as default_control_path
from .control.service import ControlService
from .dispatch import CommandDispatcher
from .dwell import CLICK, Dwell
from .health import HAND_SUBSYSTEM, HEAD_SUBSYSTEM, FaultIndicator, Health, HealthMonitor
from .input.devices import InputBackend, UinputBackend
from .input.dsl import runnable_while_asleep
from .input.executor import InputExecutor
from .input.keymap import MOUSE_MAP
from .layers import (
    Confirmation,
    Layer,
    LayerState,
    SleepSwitch,
    SleepView,
    parse_layers,
    parse_names,
)
from .meters import GestureMeter, HandReading, Microphone
from .modes import ModeIndicator, ModeManager
from .overlay import model
from .overlay.protocol import DwellRing, default_socket_path
from .overlay.service import HudService
from .pedals.reader import TapRuns
from .pointer import AbsolutePointer, PointerFilter
from .settings import (
    POINTER_ABSOLUTE,
    AppSettings,
    EdgeBinding,
    HeadTrackingSettings,
    ModeSettings,
    PedalSettings,
    load_settings,
    spoken_phrase,
)
from .status import HeldIndicator, HeldInput, SleepIndicator
from .streams import StreamFactory, build_stream

if TYPE_CHECKING:
    from .pedals import PedalEvent, PedalWatcher
    from .tracking import FaceRotationTracker

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "libre-dictum"

HEALTH_INTERVAL_SECONDS = 2.0


class StatusIndicator(ModeIndicator, FaultIndicator, HeldIndicator, SleepIndicator, Protocol):
    """The whole tray icon, which only wiring has to see at once."""

    def show(self) -> None: ...

    def hide(self) -> None: ...


DISPLAY_SUBSYSTEM = "display socket"

CONTROL_SUBSYSTEM = "control socket"

PEDAL_SUBSYSTEM = "pedals"


def _describe(failure: BaseException) -> str:
    return str(failure) or type(failure).__name__


def _tell(what: str, update: Callable[[], None]) -> None:
    """Push something at a display, and let one that fails lose only its own update."""
    try:
        update()
    except Exception:  # noqa: BLE001 - a display is never worth a session
        logger.exception("Updating the %s failed", what)


class LayerDisplay(ModeIndicator, SleepIndicator, Protocol):
    """Both messages ModeDisplays fans out: where the layers are, and what sleeps."""


class ModeDisplays:
    """ModeIndicator and SleepIndicator for every display at once."""

    def __init__(self, displays: Sequence[LayerDisplay]) -> None:
        self._displays = tuple(displays)

    @classmethod
    def of(cls, *displays: LayerDisplay | None) -> ModeDisplays | None:
        """The fan-out over whichever displays exist, or None when none do."""
        present = [display for display in displays if display is not None]
        return cls(present) if present else None

    def add_mode(self, key: str, rgb: tuple[int, int, int] | None) -> None:
        for display in self._displays:
            _tell(type(display).__name__, partial(display.add_mode, key, rgb))

    def set_layers(self, layers: LayerState) -> None:
        for display in self._displays:
            _tell(type(display).__name__, partial(display.set_layers, layers))

    def set_sleep(self, view: SleepView) -> None:
        """What is asleep, and what is being asked."""
        for display in self._displays:
            _tell(type(display).__name__, partial(display.set_sleep, view))


class Application:
    """One running instance of libre-dictum."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        backend: InputBackend,
        stream_factory: StreamFactory = build_stream,
        hud_socket: Path | None = None,
        control_socket: Path | None = None,
        claim: IO[str] | None = None,
    ) -> None:
        self.backend = backend
        self._claim = claim
        self._control_socket = control_socket
        self.control: ControlService | None = None
        self._shutdown = threading.Event()
        self._tracker: FaceRotationTracker | None = None
        self._pedals: PedalWatcher | None = None
        self._watchdog: threading.Thread | None = None
        self._faults: dict[str, str] = {}
        self._sleep = SleepSwitch(settings.sleep_confirm_seconds)
        self._sleep_lock = threading.Lock()
        self._sleep_shown = SleepView()
        self._sleep_timer: threading.Timer | None = None
        windows = settings.pedal or PedalSettings()
        self._taps = TapRuns(tap_ms=windows.tap_ms, sequence_ms=windows.sequence_ms)
        self._held: dict[tuple[Layer, str], tuple[ModeSettings, EdgeBinding]] = {}
        self._held_lock = threading.Lock()
        self._dwell: Dwell | None = None
        self._dwell_at = 0.0
        self._dwell_ring: DwellRing | None = None
        self._button_held = False
        self._controlling: str | None = None

        self.executor = InputExecutor(
            backend,
            mode_switcher=self._switch_mode,
            locker=self._lock_modes,
            sleeper=self._request_sleep,
            on_held_change=self._on_held,
            display=self._on_hud,
            teller=self._on_tell,
            recenter=self._recenter,
        )
        self.indicator = self._create_indicator(settings)
        self.hud = HudService(hud_socket) if hud_socket is not None else None
        self.health = HealthMonitor(on_change=self._report_health)
        self._displays = ModeDisplays.of(self.indicator, self.hud)
        self.modes = ModeManager(
            settings,
            executor=self.executor,
            on_text=self._on_text,
            on_partial=self._on_partial,
            on_microphone=self._on_microphone,
            stream_factory=stream_factory,
            indicator=self._displays,
            settings_loader=lambda: load_configuration(settings.config_dir),
        )
        self.dispatcher = CommandDispatcher(
            self.modes,
            self.executor,
            on_reload=self.reload,
            on_heard=self._on_heard,
            display=self.hud,
            asleep=lambda: self.asleep(Layer.VOICE),
        )

    @classmethod
    def from_config_dir(cls, config_dir: Path, *, hud: bool = True) -> Application:
        """Load a configuration and open the input devices for it."""
        claim = instance.claim()
        try:
            settings = load_configuration(config_dir)
            add_script_path(config_dir)
            return cls(
                settings,
                backend=UinputBackend.open(),
                hud_socket=default_socket_path() if hud else None,
                control_socket=default_control_path(),
                claim=claim,
            )
        except Exception:
            claim.close()
            raise

    @property
    def settings(self) -> AppSettings:
        """The configuration the modes are running, after any reload."""
        return self.modes.settings

    def start(self) -> None:
        """Start recognizers, head tracking and the tray, then say what came up."""
        self._publish_views()
        self._open_display_socket()
        self.modes.start(cancelled=self._shutdown)
        if self._shutdown.is_set():
            return
        self._sync_head_tracking()
        self._sync_pedals()
        if self._shutdown.is_set():
            return
        self._sync_control()
        if self.indicator is not None:
            self.indicator.show()

        failed = self.modes.failed_modes()
        logger.info(
            "Ready: %d of %d mode(s) listening, active mode %r, optional subsystems: %s",
            len(self.settings.modes) - len(failed),
            len(self.settings.modes),
            self.modes.active,
            ", ".join(self._optional_subsystems()) or "none",
        )

        self._watchdog = threading.Thread(target=self._watch_health, name="health", daemon=True)
        self._watchdog.start()
        self.refresh_health()

    def run(self) -> None:
        """Start, then block until shutdown is called."""
        try:
            self.start()
            self._shutdown.wait()
        finally:
            self.stop()

    def shutdown(self) -> None:
        """Ask run to return."""
        self._shutdown.set()

    def stop(self) -> None:
        """Release every held key, then close everything that was opened."""
        logger.info("Shutting down")
        self._shutdown.set()
        if self._tracker is not None:
            self._tracker.stop()
        if self._pedals is not None:
            self._pedals.stop()
        if self.control is not None:
            self.control.stop()
        self.modes.stop()
        self.executor.release_all()
        if self.indicator is not None:
            self.indicator.hide()
        if self.hud is not None:
            self.hud.hide()
        self.backend.close()
        if self._claim is not None:
            self._claim.close()
            self._claim = None

    def reload(self) -> None:
        """Re-read the configuration, then bring back whatever had failed."""
        if not self.modes.reload():
            self._faults["configuration"] = "config.json was rejected; see the log"
            self.refresh_health()
            return

        self._faults.pop("configuration", None)
        self._forget_held()
        self._wake_everything()
        self._publish_views()
        self._sync_head_tracking()
        self._sync_pedals()
        self._sync_control()
        self._open_display_socket()
        self.refresh_health()

    def refresh_health(self) -> Health:
        """Ask every subsystem how it is doing, and report any change."""
        self._show_held(self.executor.held())
        return self.health.update(self._failures(), self._subsystems())

    def _subsystems(self) -> list[str]:
        return [f"mode {name!r}" for name in self.settings.modes] + self._optional_subsystems()

    def _failures(self) -> dict[str, str]:
        failures = {
            f"mode {name!r}": _describe(failure)
            for name, failure in self.modes.failed_modes().items()
        }
        if self._tracker is not None and self._tracker.failure is not None:
            failures[HEAD_SUBSYSTEM] = _describe(self._tracker.failure)
        if self._tracker is not None and self._tracker.hand_failure is not None:
            failures[HAND_SUBSYSTEM] = _describe(self._tracker.hand_failure)
        if self._pedals is not None and self._pedals.failure is not None:
            failures[PEDAL_SUBSYSTEM] = _describe(self._pedals.failure)
        if self.hud is not None and self.hud.failure is not None:
            failures[DISPLAY_SUBSYSTEM] = _describe(self.hud.failure)
        if self.control is not None and self.control.failure is not None:
            failures[CONTROL_SUBSYSTEM] = _describe(self.control.failure)
        failures.update(self._faults)
        return failures

    def _optional_subsystems(self) -> list[str]:
        running = []
        if self.indicator is not None:
            running.append("system tray")
        if self.hud is not None:
            running.append(DISPLAY_SUBSYSTEM)
        if self._tracker is not None:
            running.append(HEAD_SUBSYSTEM)
            if self.settings.head_tracking and self.settings.head_tracking.hands:
                running.append(HAND_SUBSYSTEM)
        if self._pedals is not None:
            running.append(PEDAL_SUBSYSTEM)
        if self.control is not None:
            running.append(CONTROL_SUBSYSTEM)
        return running

    def _watch_health(self) -> None:
        while not self._shutdown.wait(HEALTH_INTERVAL_SECONDS):
            scheduling.sweep()
            try:
                self.refresh_health()
            except Exception:  # noqa: BLE001 - the watchdog outlives everything it watches
                logger.exception("Health check failed")

    def _report_health(self, health: Health) -> None:
        if health.ok:
            logger.info("All subsystems are running: %s", ", ".join(health.working) or "none")
        else:
            logger.error("Something is not working -- %s", health.summary())

        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_health, health))

        if self.indicator is None:
            return
        if health.ok:
            self.indicator.clear_fault()
        else:
            self.indicator.set_fault(", ".join(name for name, _ in health.failed))

    def _on_text(self, text: str) -> None:
        self.dispatcher.handle(text)

    def _on_held(self, held: HeldInput) -> None:
        """Held input changed: say so, and show it."""
        logger.info("Held input: %s", held.summary() or "nothing")
        self._button_held = any(key.lower() in MOUSE_MAP for key in held.held)
        self._show_held(held)

    def _show_held(self, held: HeldInput) -> None:
        if self.indicator is not None:
            _tell("system tray", partial(self.indicator.set_held, held))
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_held, held))

    def _on_heard(self, heard: model.Heard) -> None:
        """What was said, and what became of it."""
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_heard, heard))

    def _on_hud(self, target: str) -> None:
        """hud(...) in a response."""
        if self.hud is None:
            logger.warning("hud(%s) ignored: no display socket was opened", target)
            return
        _tell(DISPLAY_SUBSYSTEM, partial(self.hud.act, target))

    def _on_partial(self, partial_text: str | None) -> None:
        """What is being heard right now, straight off a recognizer worker."""
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_partial, partial_text))

    def _on_microphone(self, microphone: Microphone | None) -> None:
        """What a dictation mode hears right now, straight off its worker."""
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_microphone, microphone))

    def _switch_mode(self, name: str, from_program: bool) -> None:
        self.modes.switch(name, from_program)

    def _lock_modes(self, locked: bool, argument: str) -> None:
        self.modes.lock(locked, parse_names(argument), holder=self._controlling)

    def _on_tell(self, name: str) -> None:
        """tell(...) in a response."""
        if self.hud is None:
            logger.warning("tell(%s) ignored: no display socket was opened", name)
            return
        _tell(DISPLAY_SUBSYSTEM, partial(self.hud.tell, name))

    def _on_control(self, request: str) -> str | None:
        """Run the response another program asked for by name, or say why not."""
        if self._shutdown.is_set():
            return "shutting down"
        control = self.settings.control
        name = spoken_phrase(request)
        response = control.commands.get(name) if control is not None else None
        if response is None:
            logger.warning("No control command is named %r", request)
            return f"no control command is named {request!r}"
        logger.info("Control command: %s", name)
        self._controlling = name
        try:
            if not self.executor.execute(response, from_program=True):
                return f"{name!r} did not run; see the log"
        finally:
            self._controlling = None
        return None

    def _on_rotation(
        self, yaw: float, pitch: float, dt: float, absolute_yaw: float, absolute_pitch: float
    ) -> None:
        """One smoothed head pose."""
        mode = self.modes.active_mode
        if mode is None or self.asleep():
            self._end_dwell()
            return

        if mode.pointer.law == POINTER_ABSOLUTE:
            self._point_absolutely(mode, absolute_yaw, absolute_pitch, dt)
            return

        self._end_dwell()
        pointer = PointerFilter(mode.pointer)
        movement = pointer.filter(yaw, pitch, dt)
        if movement is not None:
            self.backend.move_relative(*movement)
        elif pointer.at_rest(yaw, pitch):
            self._absorb_drift(yaw, pitch, dt)

    def _point_absolutely(self, mode: ModeSettings, yaw: float, pitch: float, dt: float) -> None:
        """Put the pointer where the head is pointing, or where it stopped."""
        head_tracking = self.settings.head_tracking
        if head_tracking is None:
            return
        pointer = AbsolutePointer(mode.pointer, head_tracking.screen, head_tracking.placement)
        dwell = self._dwell_for(mode, dt)
        step = None
        if dwell is not None:
            self._dwell_at += dt
            step = dwell.feed(
                self._dwell_at, yaw, pitch, typed=self.executor.typed, held=self._button_held
            )
            yaw, pitch = step.yaw, step.pitch

        point = pointer.on_screen(yaw, pitch)
        if point is None:
            self._show_dwell(None)
            return
        self.backend.move_absolute(*pointer.placement.into_desktop(*point))
        if step is None:
            return
        ring = None if step.ring is None else DwellRing(*point, step.ring, step.clicked)
        self._show_dwell(ring)
        if step.click and not self._shutdown.is_set():
            logger.info("Dwell click at %.3f, %.3f of the screen", *point)
            self._perform(mode, CLICK, Layer.GESTURE)

    def _dwell_for(self, mode: ModeSettings, dt: float) -> Dwell | None:
        """The dwell this mode clicks with, new after a gap, or None while it has none."""
        settings = mode.pointer.dwell
        if settings is None or self.asleep(Layer.GESTURE):
            self._end_dwell()
            return None
        if self._dwell is None or self._dwell.settings != settings or dt <= 0:
            self._end_dwell()
            self._dwell = Dwell(settings)
        return self._dwell

    def _end_dwell(self) -> None:
        """Drop the dwell and take its ring off screen."""
        self._dwell = None
        self._show_dwell(None)

    def _show_dwell(self, ring: DwellRing | None) -> None:
        if ring == self._dwell_ring:
            return
        self._dwell_ring = ring
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_dwell, ring))

    def _absorb_drift(self, yaw: float, pitch: float, dt: float) -> None:
        """Let neutral follow the resting pose, if the user asked for that."""
        head_tracking = self.settings.head_tracking
        tracker = self._tracker
        if head_tracking is None or tracker is None:
            return
        seconds = head_tracking.auto_recenter_seconds
        if seconds is not None:
            tracker.neutral.settle(yaw, pitch, dt, seconds)

    def _recenter(self) -> None:
        """Take the head's current pose as neutral."""
        if self._tracker is None:
            logger.info("recenter(): head tracking is not running, so there is no pose to adopt")
            return
        self._tracker.recenter()

    def _on_gestures_held(self, names: tuple[str, ...]) -> None:
        """Which gestures are held now."""
        logger.info("Held gestures: %s", ", ".join(names) or "nothing")
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_gestures, names))

    def _on_meters(self, meters: tuple[GestureMeter, ...], hands: tuple[HandReading, ...]) -> None:
        """One frame of live gesture readings."""
        if self.hud is not None:
            _tell(DISPLAY_SUBSYSTEM, partial(self.hud.set_meters, meters, hands))

    def _wants_meters(self) -> bool:
        """Whether a display would draw the readings, asked from the camera thread."""
        return self.hud is not None and self.hud.wants_meters

    def _on_gesture(self, gesture: str, pressed: bool) -> None:
        """A gesture went active, or relaxed."""
        mode = self.modes.mode_for(Layer.GESTURE)
        if mode is None and pressed:
            return
        self._edge(Layer.GESTURE, gesture, mode, pressed=pressed)

    def _on_pedal(self, event: PedalEvent) -> None:
        """A pedal went down or came up."""
        mode = self.modes.mode_for(Layer.PEDAL)
        if mode is None and event.pressed:
            return
        now = time.monotonic()
        self._edge(Layer.PEDAL, event.name, mode, pressed=event.pressed)
        if event.pressed:
            self._taps.press(event.name, now, self.executor.typed)
            return
        if event.owed:
            self._taps.reset()
            return
        self._run_taps(mode, self._taps.release(event.name, now, self.executor.typed))

    def _run_taps(self, mode: ModeSettings | None, run: Sequence[str]) -> None:
        """Fire the shortest tail of run that this mode binds, if it binds one."""
        if mode is None or not run:
            return
        for length in range(2, len(run) + 1):
            name = " ".join(run[-length:])
            if name in mode.pedals:
                logger.info("Pedal taps: %s", name)
                self._taps.clear()
                self._edge(Layer.PEDAL, name, mode, pressed=True)
                self._edge(Layer.PEDAL, name, mode, pressed=False)
                return

    def _edge(self, layer: Layer, name: str, mode: ModeSettings | None, *, pressed: bool) -> None:
        """Run one gesture or pedal edge under the binding its press saw."""
        held = (layer, name)
        if pressed:
            if mode is None or (bound := self._bound(mode, layer, name)) is None:
                return
            mode, binding = bound
            if not self._may_act(layer, mode, binding.response(pressed=True)):
                return
            with self._held_lock:
                self._held[held] = (mode, binding)
        else:
            with self._held_lock:
                captured = self._held.pop(held, None)
            if captured is None:
                return
            mode, binding = captured
            if not self._may_act(layer, mode, binding.response(pressed=False)):
                return
        self._perform(mode, binding.response(pressed=pressed), layer)

    def _bound(
        self, mode: ModeSettings, layer: Layer, name: str
    ) -> tuple[ModeSettings, EdgeBinding] | None:
        """The mode and binding one edge runs under, a stand-in's from the pedal layer."""
        bindings = mode.gestures if layer is Layer.GESTURE else mode.pedals
        binding = bindings.get(name)
        if binding is None or binding.pedal is None:
            return None if binding is None else (mode, binding)
        pedals = self.modes.mode_for(Layer.PEDAL)
        stood = pedals.pedals.get(binding.pedal) if pedals is not None else None
        return None if stood is None else (pedals, stood)

    def asleep(self, layer: Layer | None = None) -> bool:
        """Whether the named mechanism is beyond reach, or every mechanism when none is named."""
        with self._sleep_lock:
            return self._sleep.all_asleep if layer is None else self._sleep.asleep(layer)

    def _request_sleep(
        self,
        argument: str,
        asleep: bool,
        origin: Layer | None = None,
        from_program: bool = False,
    ) -> None:
        """One sleep(...) or wake(...)."""
        layers = parse_layers(argument)
        verb = "sleep" if asleep else "wake"
        with self._sleep_lock:
            outcome = self._sleep.request(
                layers, asleep=asleep, now=time.monotonic(), by=origin, from_program=from_program
            )
        if outcome is Confirmation.IDLE:
            logger.debug("%s(%s) changes nothing", verb, argument)
        elif outcome is Confirmation.REFUSED:
            logger.warning(
                "%s(%s) refused: another mechanism put this to sleep and only it may wake it",
                verb,
                argument,
            )
            self._time_out_arming()
        elif outcome is Confirmation.ARMED:
            logger.info("%s(%s) is armed; repeat it to confirm", verb, argument)
            self._time_out_arming()
        else:
            by = "another program" if from_program else origin or "nothing in particular"
            logger.info("%s(%s) applied by %s", verb, argument, by)
            if asleep:
                self.executor.release_all()
                self._forget_held()
        self._publish_sleep()

    def _time_out_arming(self) -> None:
        """Publish again once the window has closed, so the prompt or notice clears itself."""
        if self._sleep_timer is not None:
            self._sleep_timer.cancel()
        self._sleep_timer = threading.Timer(
            self.settings.sleep_confirm_seconds + 0.05, self._publish_sleep
        )
        self._sleep_timer.name = "sleep-arming"
        self._sleep_timer.daemon = True
        self._sleep_timer.start()

    def _wake_everything(self) -> None:
        """Wake everything, with no confirmation: what a successful reload does."""
        with self._sleep_lock:
            self._sleep.confirm_seconds = self.settings.sleep_confirm_seconds
            self._sleep.wake_everything()
        self._publish_sleep()

    def _publish_sleep(self) -> None:
        """Tell the displays what is asleep, and what is being asked."""
        with self._sleep_lock:
            view = self._sleep.view(time.monotonic())
        if view == self._sleep_shown:
            return
        self._sleep_shown = view
        logger.debug("Sleep: %s", view.summary())
        if self._displays is not None:
            self._displays.set_sleep(view)

    def _may_act(self, layer: Layer, mode: ModeSettings, response: str | None) -> bool:
        """Whether a sleeping mechanism is allowed to run this response."""
        if not self.asleep(layer):
            return True
        if runnable_while_asleep(response, mode.aliases):
            return True
        logger.debug("The %s layer is asleep; %r is not run", layer.value, response)
        return False

    def _forget_held(self) -> None:
        """Drop every captured release."""
        with self._held_lock:
            self._held.clear()

    def _perform(
        self, mode: ModeSettings, response: str | None, origin: Layer | None = None
    ) -> None:
        """Run one binding's response with its mode's timing, and where it came from."""
        if not response:
            return
        self.executor.execute(
            response,
            input_delay=mode.input_delay,
            type_delay=mode.type_delay,
            aliases=mode.aliases,
            origin=origin,
        )

    def _open_display_socket(self) -> None:
        """Bind the socket displays connect to, or report why there is none."""
        if self.hud is None:
            return
        _tell(DISPLAY_SUBSYSTEM, self.hud.show)

    def _publish_views(self) -> None:
        """Work out what a display can show about each mode, once, at load time."""
        hud = self.hud
        if hud is None:
            return
        settings = self.settings
        names = list(settings.modes)
        views = [
            model.build_view(mode, modes=names, previous_keyword=settings.previous_mode_keyword)
            for mode in settings.layer_modes.values()
        ]
        colours = {name: mode.icon for name, mode in settings.layer_modes.items()}
        pedals = list(settings.pedal.buttons) if settings.pedal is not None else []
        head_tracking = settings.head_tracking
        gestures = list(head_tracking.gesture_definitions) if head_tracking is not None else []
        _tell(
            DISPLAY_SUBSYSTEM,
            partial(
                hud.set_views,
                views,
                overlay=settings.overlay,
                colours=colours,
                pedals=pedals,
                gestures=gestures,
            ),
        )

    def _create_indicator(self, settings: AppSettings) -> StatusIndicator | None:
        if not settings.enable_systray:
            return None
        try:
            from .systray import RGBTrayIcon

            return RGBTrayIcon("libre-dictum")
        except Exception as exc:  # noqa: BLE001 - pystray probes the display on import
            logger.warning(
                "System tray disabled (%s). It needs pystray and pillow: "
                "uv sync --extra system-tray",
                exc,
            )
            return None

    def _sync_head_tracking(self) -> None:
        """Bring head tracking in line with the configuration, restarting a dead tracker."""
        settings = self.settings.head_tracking
        if settings is None:
            if self._tracker is not None:
                self._tracker.stop()
                self._tracker = None
            return

        if self._tracker is not None:
            if self._tracker.running and self._tracker.settings == settings:
                return
            self._tracker.stop()
            self._tracker = None

        tracker = self._create_tracker(settings)
        if tracker is not None:
            self._tracker = tracker
            tracker.start()

    def _create_tracker(self, settings: HeadTrackingSettings) -> FaceRotationTracker | None:
        try:
            from .tracking import FaceRotationTracker
        except ImportError as exc:
            logger.warning(
                "Head tracking disabled (%s). It needs opencv-python and mediapipe: "
                "uv sync --extra head-tracking",
                exc,
            )
            return None

        return FaceRotationTracker(
            settings,
            rotation_callback=self._on_rotation,
            gesture_callback=self._on_gesture,
            held_callback=self._on_gestures_held,
            meter_callback=self._on_meters,
            wants_meters=self._wants_meters,
        )

    def _sync_pedals(self) -> None:
        """Bring the pedal board in line with the configuration, restarting a dead watcher."""
        settings = self.settings.pedal
        if settings is None:
            if self._pedals is not None:
                self._pedals.stop()
                self._pedals = None
            return

        if self._pedals is not None:
            if self._pedals.running and self._pedals.settings == settings:
                return
            self._pedals.stop()
            self._pedals = None

        pedals = self._create_pedals(settings)
        if pedals is not None:
            self._pedals = pedals
            pedals.start()

    def _sync_control(self) -> None:
        """Take control commands while the configuration asks for them, retrying a failed bind."""
        if self.settings.control is None or self._control_socket is None:
            if self.control is not None:
                self.control.stop()
                self.control = None
            return
        if self.control is None:
            self.control = ControlService(self._control_socket, self._on_control)
        self.control.start()

    def _create_pedals(self, settings: PedalSettings) -> PedalWatcher | None:
        try:
            from .pedals import PedalWatcher
        except ImportError as exc:
            logger.warning("Pedals disabled (%s). They need hidapi: uv sync --extra pedals", exc)
            return None

        logger.info("Opening the pedal board %s", settings.describe())
        return PedalWatcher(settings, callback=self._on_pedal)


def load_configuration(config_dir: Path) -> AppSettings:
    """Read the configuration and check everything about it, displays included."""
    settings = load_settings(config_dir)
    model.check_display(settings)
    return settings


def add_script_path(config_dir: Path) -> None:
    """Make <config_dir>/scripts/ importable as the scripts package."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "scripts").mkdir(exist_ok=True)
    path = str(config_dir)
    if path not in sys.path:
        sys.path.append(path)
