from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from .errors import ConfigError
from .input.executor import InputExecutor
from .layers import Layer, LayerState, parse_target
from .settings import AppSettings, ModeKind, ModeSettings, spoken_phrase
from .streams import (
    RecognitionStream,
    StreamFactory,
    build_stream,
    describe_model,
    mode_vocabulary,
)

logger = logging.getLogger(__name__)

_SECONDARY = (Layer.GESTURE, Layer.PEDAL)


class ModeIndicator(Protocol):
    """The part of a status display this module uses."""

    def add_mode(self, key: str, rgb: tuple[int, int, int] | None) -> None: ...

    def set_layers(self, layers: LayerState) -> None: ...


def _stream_signature(mode: ModeSettings, settings: AppSettings) -> tuple[object, ...]:
    """What a mode's recognizer is built from."""
    if mode.kind is ModeKind.VOSK:
        return (mode.kind, mode.vosk, mode.commands, tuple(mode_vocabulary(mode, settings)))
    return (mode.kind, mode.transformer)


class ModeManager:
    """Starts, stops and switches modes, one pointer per input layer."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        executor: InputExecutor,
        on_text: Callable[[str], None],
        on_partial: Callable[[str | None], None] | None = None,
        stream_factory: StreamFactory = build_stream,
        indicator: ModeIndicator | None = None,
        settings_loader: Callable[[], AppSettings] | None = None,
    ) -> None:
        self.settings = settings
        self._layer: dict[Layer, str | None] = dict.fromkeys(Layer, None)
        self._before: dict[Layer, str | None] = dict.fromkeys(Layer, None)

        self._executor = executor
        self._on_text = on_text
        self._on_partial = on_partial
        self._build_stream = stream_factory
        self._indicator = indicator
        self._load_settings = settings_loader
        self._streams: dict[str, RecognitionStream] = {}
        self._failures: dict[str, BaseException] = {}
        self._lock = threading.RLock()

    def start(self, *, cancelled: threading.Event | None = None) -> None:
        """Build and start every stream, then enable the starting mode of every layer."""

        def interrupted() -> bool:
            if cancelled is None or not cancelled.is_set():
                return False
            logger.info(
                "Startup interrupted: %d of %d mode(s) had started",
                len(self._streams),
                len(self.settings.modes),
            )
            return True

        with self._lock:
            for name, mode in self.settings.modes.items():
                if interrupted():
                    return
                self._open_stream(mode)
                if self._indicator:
                    self._indicator.add_mode(name, mode.icon)

            if interrupted():
                return

            logger.info("%d of %d mode(s) started", len(self._streams), len(self.settings.modes))
            self._activate(self._first_available(self.settings.starting_for(Layer.VOICE)))
            for layer in _SECONDARY:
                self._layer[layer] = self._starting(layer)
            self._publish_layers()

    def _starting(self, layer: Layer) -> str | None:
        """Where one secondary layer begins: its own setting, or wherever voice landed."""
        configured = self.settings.starting_for(layer)
        if configured is not None and configured in self.settings.layer_modes:
            return configured
        return self._layer[Layer.VOICE]

    def stop(self) -> None:
        """Stop every stream."""
        with self._lock:
            for name, stream in self._streams.items():
                try:
                    stream.stop()
                except Exception:  # noqa: BLE001 - one stubborn stream must not block the rest
                    logger.exception("Mode %r did not stop cleanly", name)
            self._streams.clear()
            self._layer[Layer.VOICE] = None

    def _open_stream(self, mode: ModeSettings) -> RecognitionStream | None:
        """Build and start one mode's recognizer, or record why it will not run."""
        logger.info("Loading %s for mode %r", describe_model(mode), mode.name)
        try:
            stream = self._build_stream(mode, self.settings, self._on_text, self._on_partial)
            stream.start()
        except Exception as exc:  # noqa: BLE001 - one mode's problem, not the session's
            logger.exception("Mode %r will not run", mode.name)
            self._failures[mode.name] = exc
            self._streams.pop(mode.name, None)
            return None

        self._failures.pop(mode.name, None)
        self._streams[mode.name] = stream
        return stream

    def _first_available(self, preferred: str | None) -> str | None:
        """preferred if its recognizer came up, otherwise any mode that did."""
        if preferred is not None and preferred in self._streams:
            return preferred

        fallback = next(iter(self._streams), None)
        if fallback is None:
            logger.error("No mode started; nothing will be recognized until a reload fixes it")
        else:
            logger.warning("Mode %r is unavailable; starting in %r instead", preferred, fallback)
        return fallback

    @property
    def active(self) -> str | None:
        """The voice layer's mode: which recognizer is listening."""
        return self._layer[Layer.VOICE]

    @property
    def previous(self) -> str | None:
        """The mode the voice layer came from."""
        return self._before[Layer.VOICE]

    @property
    def active_mode(self) -> ModeSettings | None:
        """Settings of the mode the voice layer is in, or None before startup."""
        return self.mode_for(Layer.VOICE)

    def mode_for(self, layer: Layer) -> ModeSettings | None:
        """Settings of the mode one layer is in."""
        name = self._layer[layer]
        if name is None:
            return None
        table = self.settings.modes if layer is Layer.VOICE else self.settings.layer_modes
        return table.get(name)

    def layer_state(self) -> LayerState:
        """Which mode each layer is in, leaving out any layer the configuration has off."""
        return LayerState(
            voice=self._layer[Layer.VOICE],
            gesture=self._layer[Layer.GESTURE] if self.settings.head_tracking else None,
            pedal=self._layer[Layer.PEDAL] if self.settings.pedal else None,
        )

    def switch(self, name: str) -> None:
        """Move whichever layers name asks for."""
        layer, target = parse_target(name)
        with self._lock:
            if layer is Layer.VOICE:
                self._switch_voice(target)
            elif layer is not None:
                self._move(layer, self._resolve(layer, target))
            else:
                self._switch_every_layer(target)
            self._publish_layers()

    def _switch_every_layer(self, target: str) -> None:
        """Point all three layers at target, or refuse the lot."""
        voice = self._resolve(Layer.VOICE, target)
        if voice is None or not self._available(voice):
            return

        for layer in _SECONDARY:
            self._move(layer, self._resolve(layer, target))
        self._switch_voice(voice)

    def _switch_voice(self, target: str) -> None:
        """Make target the mode the recognizer listens in, running exit and enter."""
        resolved = self._resolve(Layer.VOICE, target)
        if resolved is None:
            return
        if resolved == self._layer[Layer.VOICE]:
            logger.debug("Mode %r is already active", resolved)
            return
        if not self._available(resolved):
            return

        leaving = self.active_mode
        if leaving is not None:
            self._streams[leaving.name].disable()
            self._run(leaving, leaving.exit_command)

        self._before[Layer.VOICE] = self._layer[Layer.VOICE]
        self._activate(resolved)

    def _move(self, layer: Layer, target: str | None) -> None:
        """Point one secondary layer at a mode."""
        if target is None or target == self._layer[layer]:
            return
        self._before[layer] = self._layer[layer]
        self._layer[layer] = target
        logger.info("Active %s mode: %s", layer.value, target)

    def _available(self, name: str) -> bool:
        """Whether the voice layer may move to name, saying so when it may not."""
        unavailable = self._unavailable(name)
        if unavailable is None:
            return True
        logger.warning("Mode %r is unavailable: %s", name, unavailable)
        return False

    def _unavailable(self, name: str) -> str | None:
        """Why name cannot be switched to, or None when it can."""
        stream = self._streams.get(name)
        if stream is None:
            failure = self._failures.get(name)
            return (
                f"its recognizer never started ({failure})" if failure else "it has no recognizer"
            )
        if stream.failure is not None:
            return f"its recognizer stopped ({stream.failure})"
        return None

    def _resolve(self, layer: Layer, name: str) -> str | None:
        """The mode one layer's switch request names, or None."""
        phrase = spoken_phrase(name)
        if phrase == self.settings.previous_mode_keyword:
            if self._before[layer] is None:
                logger.warning("No previous %s mode to go back to", layer.value)
            return self._before[layer]

        if layer is Layer.VOICE:
            table, named = self.settings.modes, self.settings.mode_named(phrase)
        else:
            table, named = self.settings.layer_modes, self.settings.layer_mode_named(phrase)

        target = name if name in table else named
        if target is None:
            logger.warning("Cannot switch the %s layer to unknown mode %r", layer.value, name)
        return target

    def _activate(self, name: str | None) -> None:
        self._layer[Layer.VOICE] = name
        if name is None:
            return

        entering = self.settings.modes[name]
        self._run(entering, entering.enter_command)
        self._streams[name].enable()
        logger.info("Active mode: %s", name)

    def _publish_layers(self) -> None:
        if self._indicator:
            self._indicator.set_layers(self.layer_state())

    def _run(self, mode: ModeSettings, response: str | None) -> None:
        if response:
            self._executor.execute(
                response,
                input_delay=mode.input_delay,
                type_delay=mode.type_delay,
                aliases=mode.aliases,
            )

    def reload(self) -> bool:
        """Re-read the configuration and apply it."""
        if self._load_settings is None:
            logger.warning("Reload is not available: no settings loader is wired up")
            return False

        try:
            candidate = self._load_settings()
        except ConfigError as exc:
            logger.error("Keeping the current configuration: %s", exc)
            return False

        self.apply(candidate)
        logger.info("Configuration reloaded")
        return True

    def apply(self, settings: AppSettings) -> None:
        """Move the running session onto settings, rebuilding what changed."""
        with self._lock:
            self._executor.release_all()
            self._executor.clear_saved()

            previous_settings, self.settings = self.settings, settings

            for name in set(self._streams) - set(settings.modes):
                logger.info("Mode %r was removed", name)
                self._streams.pop(name).stop()
            for name in set(self._failures) - set(settings.modes):
                self._failures.pop(name)

            for name, mode in settings.modes.items():
                old = previous_settings.modes.get(name)
                unchanged = old is not None and _stream_signature(
                    old, previous_settings
                ) == _stream_signature(mode, settings)
                if name in self._streams and unchanged:
                    continue
                self._replace_stream(mode)

            if self._indicator:
                for name, mode in settings.modes.items():
                    self._indicator.add_mode(name, mode.icon)

            if self._before[Layer.VOICE] not in settings.modes:
                self._before[Layer.VOICE] = None
            if self._layer[Layer.VOICE] not in self._streams:
                logger.warning("Active mode %r is gone after the reload", self.active)
                self._layer[Layer.VOICE] = None
                self._activate(self._first_available(settings.starting_for(Layer.VOICE)))

            self._resettle_layers()
            self._publish_layers()

    def _resettle_layers(self) -> None:
        """Put the secondary layers back on a mode the new configuration still has."""
        for layer in _SECONDARY:
            if self._before[layer] not in self.settings.layer_modes:
                self._before[layer] = None
            if self._layer[layer] not in self.settings.layer_modes:
                if self._layer[layer] is not None:
                    logger.warning(
                        "The %s layer's mode %r is gone after the reload",
                        layer.value,
                        self._layer[layer],
                    )
                self._layer[layer] = self._starting(layer)

    def _replace_stream(self, mode: ModeSettings) -> None:
        existing = self._streams.pop(mode.name, None)
        if existing is not None:
            existing.stop()

        stream = self._open_stream(mode)
        if stream is not None and mode.name == self.active:
            stream.enable()

    def failed_modes(self) -> dict[str, BaseException]:
        """Modes that are not listening, and why: never started, or their thread died."""
        failed = dict(self._failures)
        failed.update({n: s.failure for n, s in self._streams.items() if s.failure is not None})
        return failed
