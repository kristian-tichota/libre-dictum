from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from .input.dsl import expand_command, runnable_while_asleep
from .input.executor import InputExecutor
from .layers import Layer
from .modes import ModeManager
from .overlay.model import Heard, nearest_pattern
from .settings import ModeSettings
from .text import normalize

logger = logging.getLogger(__name__)


class DisplayCommands(Protocol):
    """The part of a display that answers to something the user said."""

    def handle(self, phrase: str) -> bool:
        """Act on phrase if it drives a display; return whether it did."""


class CommandDispatcher:
    """Routes one recognized utterance."""

    def __init__(
        self,
        modes: ModeManager,
        executor: InputExecutor,
        *,
        on_reload: Callable[[], None] | None = None,
        on_heard: Callable[[Heard], None] | None = None,
        display: DisplayCommands | None = None,
        asleep: Callable[[], bool] | None = None,
    ) -> None:
        self._modes = modes
        self._executor = executor
        self._asleep = asleep or (lambda: False)
        self._reload = on_reload or modes.reload
        self._on_heard = on_heard
        self._display = display

    def handle(self, text: str) -> None:
        """Dispatch a recognized utterance."""
        try:
            self._dispatch(text)
        except Exception:  # noqa: BLE001 - a bad command must not deafen the recognizer
            logger.exception("Failed to handle %r", text)

    def _dispatch(self, text: str) -> None:
        """Route one utterance, telling a display what happened to it."""
        settings = self._modes.settings
        utterance = normalize(text)
        lowered = utterance.lower()
        logger.info("Heard: %s", utterance)
        if utterance != text:
            logger.debug("Normalized %r to %r", text, utterance)

        if lowered == settings.panic_command:
            self._executor.panic()
            self._report(utterance, matched=settings.panic_command)
            return

        if lowered == settings.reload_command:
            self._report(utterance, matched=settings.reload_command)
            self._reload()
            return

        sleeping = self._asleep()

        if not sleeping and lowered == settings.previous_mode_keyword:
            self._report(utterance, matched=lowered)
            self._modes.switch(lowered)
            return

        named = settings.mode_named(lowered)
        if named is not None:
            if sleeping:
                logger.debug("The voice layer is asleep; %r does not switch", utterance)
                self._report(utterance)
                return
            self._report(utterance, matched=named)
            self._modes.switch(named)
            return

        if self._display is not None:
            if self._display.handle(lowered):
                self._report(utterance, matched=lowered)
                return
        elif lowered in settings.overlay.phrases:
            logger.warning("%r needs a display, and none is running", utterance)
            self._report(utterance, matched=lowered)
            return

        mode = self._modes.active_mode
        if mode is None:
            logger.warning("Ignoring %r: no mode is active", utterance)
            self._report(utterance)
            return

        if lowered in mode.banned_strings:
            logger.debug("Ignoring banned string %r", utterance)
            return

        for command in mode.commands:
            captures = command.pattern.match(utterance)
            if captures is None:
                continue

            response = expand_command(command.response, captures)
            if sleeping and not runnable_while_asleep(response, mode.aliases):
                logger.debug("The voice layer is asleep; %r is not run", utterance)
                self._report(utterance)
                return

            self._report(utterance, matched=command.pattern.template)
            logger.debug(
                "Matched %r in mode %r: %r -> %r",
                command.pattern.template,
                mode.name,
                command.response,
                response,
            )
            self._executor.execute(
                response,
                input_delay=mode.input_delay,
                type_delay=mode.type_delay,
                aliases=mode.aliases,
                origin=Layer.VOICE,
            )
            return

        self._report_near_miss(utterance, mode)

    def _report_near_miss(self, utterance: str, mode: ModeSettings) -> None:
        """Say that nothing matched, and name the pattern that came closest."""
        wanted = self._on_heard is not None or logger.isEnabledFor(logging.DEBUG)
        closest = nearest_pattern(utterance, mode.commands) if wanted else None
        self._report(utterance, nearest=closest)

        if not logger.isEnabledFor(logging.DEBUG):
            return
        if closest is not None:
            logger.debug(
                "Nothing in mode %r matches %r; the closest pattern is %r",
                mode.name,
                utterance,
                closest,
            )
        else:
            logger.debug("Nothing in mode %r matches %r", mode.name, utterance)

    def _report(self, text: str, *, matched: str | None = None, nearest: str | None = None) -> None:
        """Tell a display what was heard and what became of it."""
        if self._on_heard is None:
            return
        try:
            self._on_heard(Heard(text=text, matched=matched, nearest=nearest))
        except Exception:  # noqa: BLE001 - a display must not fail the command
            logger.exception("Reporting %r to a display failed", text)
