from __future__ import annotations

import json
from collections.abc import Iterable

from ..settings import Command

UNKNOWN_TOKEN = "[unk]"


def build_vocabulary(commands: Iterable[Command], extra_phrases: Iterable[str] = ()) -> list[str]:
    """Every phrase the recognizer may return, {numeric} expanded to digit words."""
    phrases: list[str] = []
    for command in commands:
        phrases.extend(command.pattern.grammar_variants())
    phrases.extend(extra_phrases)
    return phrases


def build_grammar(vocabulary: Iterable[str]) -> str:
    """Serialise a vocabulary into the JSON grammar KaldiRecognizer expects."""
    return json.dumps([*vocabulary, UNKNOWN_TOKEN])
