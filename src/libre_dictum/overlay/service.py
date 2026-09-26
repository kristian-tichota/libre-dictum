from __future__ import annotations

import contextlib
import logging
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from ..errors import ProtocolError
from ..health import Health
from ..input.dsl import hud_group
from ..layers import LayerState, SleepView
from ..meters import GestureMeter, HandReading
from ..settings import OverlaySettings
from ..sockets import listen_privately, staging_path
from ..status import HeldInput
from . import protocol
from .model import Heard, ModeView, Page, Sheet, Snapshot

logger = logging.getLogger(__name__)

MAX_CLIENTS = 4

WRITE_TIMEOUT_SECONDS = 2.0

_IDLE_POLL_SECONDS = 0.2


class _Client:
    """One connected display, and the two frames waiting to be written to it."""

    def __init__(self, connection: socket.socket, *, on_close: Callable[[_Client], None]) -> None:
        self._connection = connection
        self._on_close = on_close
        self._lock = threading.Lock()
        self._waiting = threading.Event()
        self._stopping = threading.Event()
        self._catalogue: protocol.Catalogue | None = None
        self._state: protocol.State | None = None
        self._revision = -1
        self._writer: threading.Thread | None = None

    def start(self, name: str) -> None:
        self._writer = threading.Thread(target=self._run, name=name, daemon=True)
        self._writer.start()

    def offer(self, catalogue: protocol.Catalogue, state: protocol.State) -> None:
        """Make this the picture to write next."""
        with self._lock:
            if catalogue.revision != self._revision:
                self._revision = catalogue.revision
                self._catalogue = catalogue
            self._state = state
        self._waiting.set()

    def close(self) -> None:
        """Stop writing and hang up."""
        self._stopping.set()
        self._waiting.set()
        with contextlib.suppress(OSError):
            self._connection.close()

    def _take(self) -> tuple[protocol.Catalogue | None, protocol.State | None]:
        with self._lock:
            pending = (self._catalogue, self._state)
            self._catalogue = self._state = None
            return pending

    def _run(self) -> None:
        try:
            while not self._stopping.is_set():
                if not self._waiting.wait(_IDLE_POLL_SECONDS):
                    continue
                self._waiting.clear()
                catalogue, state = self._take()
                for frame in (catalogue, state):
                    if frame is not None and not self._stopping.is_set():
                        self._connection.sendall(protocol.encode(frame))
        except OSError as exc:
            logger.info("Display disconnected: %s", exc)
        except Exception:  # noqa: BLE001 - a display's thread must not die silently
            logger.exception("Writing to a display failed")
        finally:
            self.close()
            self._on_close(self)


class HudService:
    """Publishes what the displays draw, over one unix socket."""

    def __init__(
        self,
        path: Path,
        *,
        chip: bool = True,
        sheet: bool = False,
        max_clients: int = MAX_CLIENTS,
        write_timeout: float = WRITE_TIMEOUT_SECONDS,
    ) -> None:
        self.path = protocol.check_socket_path(Path(path))
        self._staging = protocol.check_socket_path(staging_path(self.path))
        self.failure: BaseException | None = None

        self._max_clients = max_clients
        self._write_timeout = write_timeout
        self._lock = threading.RLock()
        self._stopping = threading.Event()

        self._catalogue = protocol.Catalogue()
        self._views: dict[str, ModeView] = {}
        self._overlay = OverlaySettings()
        self._layers = LayerState()
        self._sleep = SleepView()
        self._held = HeldInput()
        self._heard = Heard()
        self._partial: str | None = None
        self._health = Health()
        self._held_gestures: tuple[str, ...] = ()
        self._meters: tuple[GestureMeter, ...] = ()
        self._hands: tuple[HandReading, ...] = ()
        self._dot: protocol.CalibrationDot | None = None
        self._meters_on = True
        self._chip = chip
        self._sheet = Sheet(open_at_start=sheet)
        self._configured = False

        self._listener: socket.socket | None = None
        self._acceptor: threading.Thread | None = None
        self._clients: list[_Client] = []
        self._connected = 0

    @property
    def listening(self) -> bool:
        return self._listener is not None

    def show(self) -> None:
        """Bind the socket and start accepting displays."""
        with self._lock:
            if self._listener is not None:
                return
            self._stopping.clear()
            try:
                self._listener = self._bind()
            except Exception as exc:  # noqa: BLE001 - see below
                self.failure = exc
                logger.warning("No display socket at %s (%s)", self.path, exc)
                return

            self.failure = None
            self._acceptor = threading.Thread(target=self._accept, name="hud-accept", daemon=True)
            self._acceptor.start()
            logger.info("Displays can connect on %s", self.path)

    def hide(self) -> None:
        """Hang up on every display and remove the socket."""
        with self._lock:
            self._stopping.set()
            listener, self._listener = self._listener, None
            clients, self._clients = list(self._clients), []

        for client in clients:
            client.close()
        if listener is not None:
            listener.close()
            self.path.unlink(missing_ok=True)

    def _bind(self) -> socket.socket:
        """A listening socket at path, reachable by nobody else."""
        self._refuse_a_second_core()
        return listen_privately(self.path, self._staging, backlog=self._max_clients)

    def _refuse_a_second_core(self) -> None:
        """Fail when another running instance holds the socket."""
        if not self.path.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(self._write_timeout)
            probe.connect(str(self.path))
        except OSError:
            return
        finally:
            probe.close()
        raise ProtocolError(f"another libre-dictum is already publishing on {self.path}")

    def _accept(self) -> None:
        while not self._stopping.is_set():
            listener = self._listener
            if listener is None:
                return
            try:
                connection, _ = listener.accept()
            except OSError:
                if not self._stopping.is_set():
                    logger.info("Stopped accepting displays on %s", self.path)
                return
            except Exception:  # noqa: BLE001 - the accept thread outlives every display
                logger.exception("Accepting a display failed")
                return
            self._welcome(connection)

    def _welcome(self, connection: socket.socket) -> None:
        """Take on one display: never read from it, and send it the whole picture."""
        try:
            connection.shutdown(socket.SHUT_RD)
            connection.settimeout(self._write_timeout)
        except OSError as exc:
            logger.info("A display went away before it was set up: %s", exc)
            connection.close()
            return

        with self._lock:
            if len(self._clients) >= self._max_clients:
                logger.warning(
                    "Refusing a display: %d are already connected to %s",
                    len(self._clients),
                    self.path,
                )
                connection.close()
                return
            self._connected += 1
            client = _Client(connection, on_close=self._forget)
            self._clients.append(client)
            client.start(f"hud-client-{self._connected}")
            client.offer(self._catalogue, self._state())
        logger.info("A display connected to %s", self.path)

    def _forget(self, client: _Client) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def set_views(
        self,
        views: Sequence[ModeView],
        *,
        overlay: OverlaySettings | None = None,
        colours: Mapping[str, tuple[int, int, int] | None] | None = None,
        pedals: Sequence[str] = (),
        gestures: Sequence[str] = (),
    ) -> None:
        """A configuration was loaded: rebuild the catalogue and bump its revision."""
        wanted = overlay or OverlaySettings()
        with self._lock:
            if not self._configured or wanted.chip_enabled != self._overlay.chip_enabled:
                self._chip = wanted.chip_enabled
            if not self._configured or wanted.meters_enabled != self._overlay.meters_enabled:
                self._meters_on = wanted.meters_enabled
            self._configured = True
            self._overlay = wanted
            self._views = {view.mode: view for view in views}
            self._catalogue = protocol.catalogue_of(
                views,
                revision=self._catalogue.revision + 1,
                navigate=wanted.navigate or "",
                output=wanted.output.name or "",
                output_only=wanted.output.only,
                sheet_open=wanted.sheet_open or "",
                sheet_close=wanted.sheet_close or "",
                sheet_modes=wanted.sheet_modes or "",
                sheet_pedals=wanted.sheet_pedals or "",
                sheet_gestures=wanted.sheet_gestures or "",
                pedals=pedals,
                gestures=gestures,
                colours=colours,
            )
            self._refollow()
        self._publish()

    def add_mode(self, key: str, rgb: tuple[int, int, int] | None) -> None:
        """Complete the ModeIndicator protocol, and do nothing."""

    def set_layers(self, layers: LayerState) -> None:
        """A layer moved: which mode voice, gestures and the pedals are each in now."""
        with self._lock:
            self._layers = layers
            self._refollow()
        self._publish()

    @property
    def connected(self) -> bool:
        """Whether any display is listening."""
        with self._lock:
            return bool(self._clients)

    def set_sleep(self, view: SleepView) -> None:
        """What is beyond reach, and what is being asked."""
        with self._lock:
            self._sleep = view
        self._publish()

    def set_dot(self, dot: protocol.CalibrationDot | None) -> None:
        """Show one calibration dot, or None to take the surface away."""
        with self._lock:
            self._dot = dot
        self._publish()

    def set_held(self, held: HeldInput) -> None:
        """What is still down."""
        with self._lock:
            self._held = held
        self._publish()

    def set_gestures(self, names: Sequence[str]) -> None:
        """Which gestures are held right now."""
        with self._lock:
            self._held_gestures = tuple(names)
        self._publish()

    @property
    def wants_meters(self) -> bool:
        """Whether anything on screen would draw the readings."""
        if self._dark:
            return False
        return self._meters_on and self._sheet.visible and self._sheet.page is Page.GESTURES

    def set_meters(self, meters: Sequence[GestureMeter], hands: Sequence[HandReading] = ()) -> None:
        """One frame of live gesture readings."""
        with self._lock:
            if not self.wants_meters:
                if not self._meters and not self._hands:
                    return
                self._meters, self._hands = (), ()
            else:
                self._meters, self._hands = tuple(meters), tuple(hands)
        self._publish()

    def set_health(self, health: Health) -> None:
        """What is broken, with the reason."""
        with self._lock:
            self._health = health
        self._publish()

    def set_heard(self, heard: Heard) -> None:
        """The last settled utterance, what it matched, and the nearest miss if it did not."""
        with self._lock:
            self._heard = heard
        self._publish()

    def set_partial(self, partial: str | None) -> None:
        """What the recognizer has taken in so far, and the proof it is hearing anything."""
        with self._lock:
            self._partial = partial
        self._publish()

    @property
    def chip(self) -> bool:
        return self._chip

    @property
    def sheet(self) -> bool:
        return self._sheet.visible

    @property
    def group(self) -> str | None:
        """Which group the sheet has open; None is its index."""
        return self._sheet.group

    def toggle_chip(self) -> None:
        with self._lock:
            self._chip = not self._chip
        self._publish()

    @property
    def meters(self) -> bool:
        """Whether the user has the readings switched on."""
        return self._meters_on

    def toggle_meters(self) -> None:
        """Show or hide the live readings."""
        with self._lock:
            self._meters_on = not self._meters_on
            if not self._meters_on:
                self._meters, self._hands = (), ()
        self._publish()

    def open_sheet(self, group: str | None = None) -> None:
        """Show the sheet, at group or at its index."""
        with self._lock:
            self._sheet.open(group)
        self._publish()

    @property
    def page(self) -> str:
        """Which page the sheet is on, as it travels."""
        with self._lock:
            return str(self._sheet.page)

    def open_modes(self) -> None:
        """Show the sheet at the modes page: where the session is, and what leads away."""
        with self._lock:
            self._sheet.open_modes()
        self._publish()

    def open_pedals(self) -> None:
        """Show the sheet at the pedals page: what each pedal does, and what moves them."""
        with self._lock:
            self._sheet.open_pedals()
        self._publish()

    def open_gestures(self) -> None:
        """Show the sheet at the gestures page: what each gesture does, and what moves them."""
        with self._lock:
            self._sheet.open_gestures()
        self._publish()

    def close_sheet(self) -> None:
        with self._lock:
            self._sheet.close()
        self._publish()

    def toggle_sheet(self) -> None:
        with self._lock:
            self._sheet.toggle()
        self._publish()

    def handle(self, phrase: str) -> bool:
        """Act on phrase if it drives a display, and say whether it did."""
        with self._lock:
            overlay, view = self._overlay, self._views.get(self._layers.voice or "")

        if phrase == overlay.chip_command:
            self.toggle_chip()
        elif phrase == overlay.meters_command:
            self.toggle_meters()
        elif phrase == overlay.sheet_open:
            self.open_sheet()
        elif phrase == overlay.sheet_close:
            self.close_sheet()
        elif phrase == overlay.sheet_modes:
            self.open_modes()
        elif phrase == overlay.sheet_pedals:
            self.open_pedals()
        elif phrase == overlay.sheet_gestures:
            self.open_gestures()
        elif view is not None and (group := view.group_named(phrase)) is not None:
            self.open_sheet(group.name)
        else:
            return False
        logger.debug("Display phrase %r", phrase)
        return True

    def act(self, target: str) -> None:
        """What hud(...) in a response asks for."""
        group = hud_group(target)
        if group is not None:
            self.open_sheet(group)
        elif target == "chip":
            self.toggle_chip()
        elif target == "sheet":
            self.toggle_sheet()
        elif target == "open":
            self.open_sheet()
        elif target == "close":
            self.close_sheet()
        elif target == "modes":
            self.open_modes()
        elif target == "pedals":
            self.open_pedals()
        elif target == "gestures":
            self.open_gestures()
        elif target == "meters":
            self.toggle_meters()
        else:
            logger.warning("hud(%s) is not something a display can do", target)

    def state(self) -> protocol.State:
        """The frame a display would be sent right now."""
        with self._lock:
            return self._state()

    def _state(self) -> protocol.State:
        """The current state frame."""
        return protocol.state_of(
            Snapshot(
                layers=self._layers,
                sleep=self._sleep,
                held=self._held,
                heard=replace(self._heard, partial=self._partial),
                open_group=self._sheet.group,
                health=self._health,
                held_gestures=self._held_gestures,
                meters=self._meters,
                hands=self._hands,
            ),
            revision=self._catalogue.revision,
            chip=self._chip and (not self._dark or self._sleep.asking),
            sheet=self._sheet.visible and not self._dark,
            page=str(self._sheet.page),
            dot=self._dot,
        )

    @property
    def _dark(self) -> bool:
        """Whether a full sleep has taken the surfaces off screen."""
        return self._overlay.sleep_hides and self._sleep.total

    def _refollow(self) -> None:
        """Keep the sheet's open group only while the active mode has one."""
        mode = self._layers.voice
        view = self._views.get(mode) if mode is not None else None
        if view is not None:
            self._sheet.follow(view)

    def _publish(self) -> None:
        """Hand every client the current picture."""
        with self._lock:
            catalogue, state, clients = self._catalogue, self._state(), list(self._clients)
        for client in clients:
            client.offer(catalogue, state)
