from __future__ import annotations

import re
from dataclasses import dataclass, field

from .text import normalize

DIGIT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")

PLACEHOLDER_PATTERNS = {
    "numeric": r"(\d+)",
    "any": r"(\S+)",
    "rest": r"(.+)",
}

_PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDER_PATTERNS) + r")\}")


def _to_regex(normalized_template: str, *, anchor_start: bool) -> re.Pattern[str]:
    """Compile a normalized template into a case-insensitive regex."""
    parts = []
    position = 0
    for match in _PLACEHOLDER_RE.finditer(normalized_template):
        parts.append(re.escape(normalized_template[position : match.start()]))
        parts.append(PLACEHOLDER_PATTERNS[match.group(1)])
        position = match.end()
    parts.append(re.escape(normalized_template[position:]))

    body = "".join(parts)
    return re.compile(("^" if anchor_start else "") + body + "$", re.IGNORECASE)


@dataclass(frozen=True)
class CommandPattern:
    """A command pattern, compiled once at load and never per utterance."""

    template: str
    normalized: str = field(compare=False)
    placeholders: tuple[str, ...] = field(compare=False)
    regex: re.Pattern[str] = field(compare=False, repr=False)

    @classmethod
    def compile(cls, template: str) -> CommandPattern:
        normalized = normalize(template)
        return cls(
            template=template,
            normalized=normalized,
            placeholders=tuple(m.group(1) for m in _PLACEHOLDER_RE.finditer(normalized)),
            regex=_to_regex(normalized, anchor_start=True),
        )

    @property
    def has_rest(self) -> bool:
        """Whether the pattern contains {rest}, which no closed grammar can express."""
        return "rest" in self.placeholders

    def match(self, text: str) -> tuple[str, ...] | None:
        """The captures for a whole normalized utterance, or None."""
        match = self.regex.fullmatch(text)
        return match.groups() if match else None

    def grammar_variants(self) -> list[str]:
        """The phrases a closed recognizer grammar can contain for this pattern."""
        variants = [self.normalized.lower()]
        for _ in range(self.placeholders.count("numeric")):
            variants = [v.replace("{numeric}", word, 1) for v in variants for word in DIGIT_WORDS]
        return variants


class PartialMatcher:
    """Matches a growing partial transcript against the tail of any known phrase."""

    def __init__(self, variants: list[str]) -> None:
        self._compiled = [_to_regex(variant, anchor_start=False) for variant in reversed(variants)]

    def match(self, partial_text: str) -> str | None:
        """Return the matched phrase (as heard) or None."""
        for regex in self._compiled:
            match = regex.search(partial_text)
            if match:
                return match.group(0)
        return None
