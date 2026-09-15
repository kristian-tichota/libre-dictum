from __future__ import annotations

import importlib
import logging
import shlex
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..errors import CommandSyntaxError
from ..layers import Layer
from ..status import MODIFIER_ORDER, HeldInput, canonical
from ..text import to_ascii
from .devices import InputBackend
from .dsl import (
    KeyAction,
    KeyToken,
    Token,
    VerbToken,
    apply_aliases,
    expand_command,
    parse_response,
)
from .keymap import CHARACTERS, MODIFIER_KEYS, untypeable

logger = logging.getLogger(__name__)

SCRIPT_PACKAGE = "scripts"

SCRIPT_ARGUMENT_SEPARATOR = ";;"

DEFAULT_TYPE_DELAY = 0.002


@dataclass(slots=True)
class InputState:
    """Everything that survives between responses."""

    keys_held: list[str] = field(default_factory=list)
    modifiers_held: list[str] = field(default_factory=list)
    saved_value: str | None = None

    @property
    def saved_values(self) -> list[str]:
        """The saved value as the list expand_command consumes."""
        return [] if self.saved_value is None else [self.saved_value]

    def snapshot(self) -> HeldInput:
        """An immutable copy, safe to hand to a display on another thread."""
        return HeldInput(
            held=tuple(self.keys_held),
            pending=tuple(self.modifiers_held),
            saved=self.saved_value,
        )


class InputExecutor:
    """Runs response strings, serialising every caller."""

    def __init__(
        self,
        backend: InputBackend,
        *,
        mode_switcher: Callable[[str], None] | None = None,
        on_held_change: Callable[[HeldInput], None] | None = None,
        display: Callable[[str], None] | None = None,
        recenter: Callable[[], None] | None = None,
        sleeper: Callable[[str, bool, Layer | None], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend = backend
        self._mode_switcher = mode_switcher
        self._on_held_change = on_held_change
        self._display = display
        self._recenter = recenter
        self._sleeper = sleeper
        self._sleep = sleep
        self._lock = threading.RLock()
        self._report_lock = threading.Lock()
        self._reported = HeldInput()
        self.state = InputState()
        self.typed = 0
        self._type_delay = DEFAULT_TYPE_DELAY
        self._origin: Layer | None = None
        self._verbs: dict[str, Callable[[str], None]] = {
            "mode": self._verb_mode,
            "save": self._verb_save,
            "exec": self._verb_exec,
            "python": self._verb_python,
            "script": self._verb_script,
            "type": self._verb_type,
            "hud": self._verb_hud,
            "recenter": self._verb_recenter,
            "sleep": self._verb_sleep,
            "wake": self._verb_wake,
        }

    def execute(
        self,
        response: str,
        *,
        input_delay: float = 0.01,
        type_delay: float = DEFAULT_TYPE_DELAY,
        aliases: dict[str, str] | None = None,
        origin: Layer | None = None,
    ) -> bool:
        """Run one response string."""
        with self._lock:
            self._origin = origin
            executed = self._execute(
                response, input_delay=input_delay, type_delay=type_delay, aliases=aliases
            )
        self.report_held()
        return executed

    def _execute(
        self,
        response: str,
        *,
        input_delay: float,
        type_delay: float,
        aliases: dict[str, str] | None,
    ) -> bool:
        """The body of execute, with the lock already held."""
        self._type_delay = type_delay
        text = apply_aliases(response, aliases or {})
        if text != response:
            logger.debug("Aliases expanded %r to %r", response, text)

        filled = expand_command(text, self.state.saved_values, apply_defaults=True)
        if filled != text:
            logger.debug("Saved value %r turned %r into %r", self.state.saved_value, text, filled)
        text = filled

        try:
            tokens = parse_response(text)
        except CommandSyntaxError as exc:
            logger.warning("Ignoring response %r: %s", response, exc)
            return False

        logger.debug("Executing %r as %s", response, tokens)
        for token in tokens:
            self._run_token(token, input_delay)
        return True

    def release_all(self) -> None:
        """Release every held key and modifier: the recovery path, and part of shutdown."""
        with self._lock:
            self._release_all()
        self.report_held()

    def _release_all(self) -> None:
        """The body of release_all, with the lock already held."""
        for key in (*self.state.keys_held, *self.state.modifiers_held):
            self._backend.release(key)
        self.state.keys_held.clear()
        self.state.modifiers_held.clear()

    def clear_saved(self) -> None:
        """Forget the pending saved value."""
        with self._lock:
            self.state.saved_value = None
        self.report_held()

    def panic(self) -> None:
        """Release everything and forget any saved value."""
        with self._lock:
            logger.info(
                "Panic: releasing %d held key(s) and %d pending modifier(s)",
                len(self.state.keys_held),
                len(self.state.modifiers_held),
            )
            self._release_all()
            self.state.saved_value = None
        self.report_held()

    def held(self) -> HeldInput:
        """A snapshot of everything still down, for a display, a log or a test."""
        with self._lock:
            return self.state.snapshot()

    def report_held(self) -> HeldInput:
        """Tell the listener what is down, if it differs from what it was last told."""
        if self._on_held_change is None:
            return self.held()

        with self._report_lock:
            snapshot = self.held()
            if snapshot == self._reported:
                return snapshot
            self._reported = snapshot

        try:
            self._on_held_change(snapshot)
        except Exception:  # noqa: BLE001 - a display must not fail the command
            logger.exception("Reporting held input failed")
        return snapshot

    def _run_token(self, token: Token, input_delay: float) -> None:
        if isinstance(token, VerbToken):
            self._run_verb(token)
            return

        self.state.saved_value = None
        self._run_key(token, input_delay)

    def _run_key(self, token: KeyToken, input_delay: float) -> None:
        key, action = token.key, token.action

        if action is KeyAction.TOGGLE:
            action = KeyAction.RELEASE if key in self.state.keys_held else KeyAction.HOLD

        if action is not KeyAction.RELEASE:
            if key in self.state.keys_held:
                return

            self._count(key)
            self._backend.press(key)
            self._sleep(input_delay)

            if action is KeyAction.HOLD:
                self.state.keys_held.append(key)
                if key in self.state.modifiers_held:
                    self.state.modifiers_held.remove(key)
                return

            if key in MODIFIER_KEYS:
                self.state.modifiers_held.append(key)
                return

        self._count(key)
        self._backend.release(key)
        self._sleep(input_delay)

        if action is KeyAction.RELEASE:
            if key in self.state.keys_held:
                self.state.keys_held.remove(key)
            else:
                logger.debug("release(%s) ignored: the key was not held", key)
            return

        self._release_pending_modifiers(input_delay)

    def _count(self, key: str) -> None:
        """Note that something was typed, unless it was only a modifier moving."""
        if canonical(key) not in MODIFIER_ORDER:
            self.typed += 1

    def _release_pending_modifiers(self, input_delay: float) -> None:
        if not self.state.modifiers_held:
            return
        for modifier in self.state.modifiers_held:
            self._backend.release(modifier)
        self.state.modifiers_held.clear()
        self._sleep(input_delay)

    def _run_verb(self, token: VerbToken) -> None:
        try:
            self._verbs[token.verb](token.argument)
        except Exception:  # noqa: BLE001 - user code; must not kill the caller's thread
            logger.exception("%s(%s) failed", token.verb, token.argument)

    def _verb_mode(self, argument: str) -> None:
        if self._mode_switcher is None:
            logger.warning("mode(%s) ignored: no mode switcher is wired up", argument)
            return
        self._mode_switcher(argument)

    def _verb_hud(self, argument: str) -> None:
        """Ask the on-screen display to show something."""
        if self._display is None:
            logger.warning("hud(%s) ignored: no display is running", argument)
            return
        self._display(argument)

    def _verb_sleep(self, argument: str) -> None:
        """Put mechanisms beyond reach."""
        self._request_sleep(argument, asleep=True)

    def _verb_wake(self, argument: str) -> None:
        """Bring them back."""
        self._request_sleep(argument, asleep=False)

    def _request_sleep(self, argument: str, *, asleep: bool) -> None:
        """Hand one sleep or wake request on."""
        if self._sleeper is None:
            verb = "sleep" if asleep else "wake"
            logger.warning("%s(%s) ignored: no sleep switch is wired up", verb, argument)
            return
        self._sleeper(argument, asleep, self._origin)

    def _verb_recenter(self, argument: str) -> None:
        """Take the head's current pose as the pointer's neutral."""
        if self._recenter is None:
            logger.warning("recenter() ignored: head tracking is not wired up")
            return
        self._recenter()

    def _verb_save(self, argument: str) -> None:
        logger.debug("Saved %r for the next command", argument)
        self.state.saved_value = argument

    def _verb_exec(self, argument: str) -> None:
        try:
            command = shlex.split(argument)
        except ValueError:
            logger.warning("exec(%s) has unbalanced quotes; splitting on spaces", argument)
            command = argument.split(" ")
        subprocess.run(command, check=False)

    def _verb_python(self, argument: str) -> None:
        exec(argument, {"__name__": "libre_dictum.response"})  # noqa: S102 - by design

    def _verb_type(self, argument: str) -> None:
        """Type argument out, one key code per character."""
        text = to_ascii(argument)
        missing = untypeable(text)
        if missing:
            logger.warning(
                "type(): no key produces %s, so it is left out of %r",
                ", ".join(repr(character) for character in missing),
                text,
            )
            text = "".join(character for character in text if character in CHARACTERS)

        self.state.saved_value = None

        was_down = "shift" in self.state.keys_held or "shift" in self.state.modifiers_held
        shift_down = was_down
        try:
            for character in text:
                key, needs_shift = CHARACTERS[character]
                if needs_shift != shift_down:
                    if needs_shift:
                        self._backend.press("shift")
                    else:
                        self._backend.release("shift")
                    shift_down = needs_shift
                self._backend.press(key)
                self._backend.release(key)
                self._sleep(self._type_delay)
        finally:
            if shift_down != was_down:
                if was_down:
                    self._backend.press("shift")
                else:
                    self._backend.release("shift")
            self._release_pending_modifiers(self._type_delay)

    def _verb_script(self, argument: str) -> None:
        name, *arguments = argument.split(SCRIPT_ARGUMENT_SEPARATOR)
        module = importlib.import_module(f"{SCRIPT_PACKAGE}.{name}")
        module.script(*arguments)
