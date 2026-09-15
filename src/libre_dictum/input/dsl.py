from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum, auto

from ..errors import CommandSyntaxError
from .keymap import is_known_key

_PLACEHOLDER_RE = re.compile(r"\{(\d+)(?:=([^{}]*))?\}")

_REPEAT_RE = re.compile(r"(\d+)\(([^()]*)\)")

_TOKEN_SEPARATOR_RE = re.compile(r"(?<!\\)\+")

_VERB_RE = re.compile(
    r"(script|python|exec|mode|hold|release|toggle|save|type|hud|recenter|sleep|wake)\((.*)\)",
    re.DOTALL,
)

_GROUP_RE = re.compile(r"([^()]+)\((.*)\)", re.DOTALL)

_UNRESOLVED_RE = re.compile(r"\{[^{}]*\}")

_PLACEHOLDER_COUNT_RE = re.compile(r"\{[^{}]*\}(?=\()")


class KeyAction(StrEnum):
    """What a key token does to the key it names."""

    TAP = auto()
    HOLD = auto()
    RELEASE = auto()
    TOGGLE = auto()


KEY_VERBS: dict[str, KeyAction] = {
    "hold": KeyAction.HOLD,
    "release": KeyAction.RELEASE,
    "toggle": KeyAction.TOGGLE,
}

ACTION_VERBS = frozenset(
    {"script", "python", "exec", "mode", "save", "type", "hud", "recenter", "sleep", "wake"}
)

AWAKE_VERBS = frozenset({"sleep", "wake", "hud"})

NILADIC_VERBS = frozenset({"recenter"})

HUD_TARGETS = {
    "chip": "show or hide the chip",
    "sheet": "show or hide the command sheet",
    "open": "show the command sheet at its index",
    "close": "hide the command sheet",
    "modes": "show where the session is, and what reaches every mode from here",
    "pedals": "show what each pedal does now, and what moves the pedal layer",
    "gestures": "show what each gesture does now, and what moves the gesture layer",
    "meters": "show or hide the live gesture readings on the gestures page",
}

HUD_GROUP = "group:"


def hud_group(argument: str) -> str | None:
    """The group hud(group:...) names, or None for any other target."""
    if not argument.startswith(HUD_GROUP):
        return None
    return argument[len(HUD_GROUP) :].strip()


def check_hud_target(argument: str) -> None:
    """Raise CommandSyntaxError unless argument is something a display does."""
    group = hud_group(argument)
    if group is not None:
        if not group:
            raise CommandSyntaxError(f"hud({argument}) names no group after {HUD_GROUP!r}")
        return
    if argument not in HUD_TARGETS:
        targets = ", ".join(sorted(HUD_TARGETS))
        raise CommandSyntaxError(
            f"hud({argument}) is not something a display can do. "
            f"Targets: {targets}, or {HUD_GROUP}<group name>."
        )


@dataclass(frozen=True, slots=True)
class KeyToken:
    """A key or mouse button, and what to do with it."""

    key: str
    action: KeyAction = KeyAction.TAP


@dataclass(frozen=True, slots=True)
class VerbToken:
    """A verb that does something other than move a key."""

    verb: str
    argument: str


Token = KeyToken | VerbToken


def apply_aliases(text: str, aliases: dict[str, str]) -> str:
    """Rewrite text through each alias regex, in declaration order."""
    for pattern, replacement in aliases.items():
        text = re.sub(pattern, replacement, text)
    return text


def escape_value(value: str) -> str:
    """Escape the one DSL metacharacter, for text that is not DSL."""
    return value.replace("+", r"\+")


def expand_command(
    response_template: str, values: Iterable[str], apply_defaults: bool = False
) -> str:
    """Substitute {1}, {2}, ... with values."""
    values = list(values)
    count = len(values)

    def substitute(match: re.Match[str]) -> str:
        index = int(match.group(1))
        default = match.group(2)

        if index == 0:
            return match.group(0)
        if index <= count:
            return escape_value(values[index - 1])
        if apply_defaults:
            return default if default is not None else ""

        remaining = index - count
        return f"{{{remaining}={default}}}" if default is not None else f"{{{remaining}}}"

    return _PLACEHOLDER_RE.sub(substitute, response_template)


def invalid_placeholders(response: str) -> list[str]:
    """Return the placeholders in response that can never resolve."""
    return [m.group(0) for m in _PLACEHOLDER_RE.finditer(response) if int(m.group(1)) == 0]


def expand_repeats(text: str) -> str:
    """Expand n(group) into group + group + ..., innermost group first."""
    while True:
        expanded = _REPEAT_RE.sub(lambda m: "+".join([m.group(2)] * int(m.group(1))), text)
        if expanded == text:
            return text
        text = expanded


def split_tokens(text: str) -> list[str]:
    """Split a response on unescaped + and unescape the literal ones."""
    return [part.strip().replace(r"\+", "+") for part in _TOKEN_SEPARATOR_RE.split(text)]


def parse_token(text: str) -> Token:
    """Parse one token, or raise CommandSyntaxError."""
    match = _VERB_RE.fullmatch(text)
    if match:
        verb, argument = match.group(1), match.group(2)
        if verb in KEY_VERBS:
            if not is_known_key(argument):
                raise CommandSyntaxError(f"{verb}({argument}) names an unknown key: {argument!r}")
            return KeyToken(key=argument.lower(), action=KEY_VERBS[verb])
        if verb in NILADIC_VERBS and argument.strip():
            raise CommandSyntaxError(
                f"{verb}({argument}) takes no argument; write {verb}() on its own."
            )
        return VerbToken(verb=verb, argument=argument)

    if is_known_key(text):
        return KeyToken(key=text.lower())

    group = _GROUP_RE.fullmatch(text)
    if group:
        verbs = ", ".join(sorted(ACTION_VERBS | set(KEY_VERBS)))
        raise CommandSyntaxError(
            f"{text!r}: {group.group(1)!r} is neither a repeat count nor a verb. "
            f"A repeat count must be a number, as in 3(down). Known verbs: {verbs}."
        )

    raise CommandSyntaxError(f"{text!r} is neither a known key nor a known verb")


def has_placeholder(text: str) -> bool:
    """Whether text still holds a {...} placeholder."""
    return _UNRESOLVED_RE.search(text) is not None


def validate_response(text: str) -> tuple[Token, ...]:
    """The tokens of a response that can be parsed before it is ever spoken."""
    probe = _PLACEHOLDER_COUNT_RE.sub("1", text)
    return tuple(
        parse_token(token)
        for token in split_tokens(expand_repeats(probe))
        if not has_placeholder(token)
    )


def runnable_while_asleep(response: str | None, aliases: Mapping[str, str] | None = None) -> bool:
    """Whether a sleeping mechanism may run this response string."""
    if not response:
        return True
    try:
        tokens = validate_response(apply_aliases(response, dict(aliases or {})))
    except CommandSyntaxError:
        return False
    return allowed_while_asleep(tokens)


def allowed_while_asleep(tokens: Iterable[Token]) -> bool:
    """Whether a sleeping mechanism may run this response."""
    return all(isinstance(token, VerbToken) and token.verb in AWAKE_VERBS for token in tokens)


def parse_response(text: str) -> tuple[Token, ...]:
    """Expand repeats, split and parse a whole response."""
    return tuple(parse_token(token) for token in split_tokens(expand_repeats(text)))
