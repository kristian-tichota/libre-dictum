from __future__ import annotations

from ..pattern import PartialMatcher


class PartialTracker:
    """Turns a stream of partial transcripts into at-most-once command phrases."""

    def __init__(self, matcher: PartialMatcher) -> None:
        self._matcher = matcher
        self._fired_words = 0

    def on_partial(self, partial_text: str) -> str | None:
        """Return the phrase to fire from this partial, if any."""
        if not partial_text:
            return None

        phrase = self._matcher.match(self._unfired_tail(partial_text))
        if phrase is None:
            return None

        self._fired_words = len(partial_text.split(" "))
        return phrase

    def on_final(self, text: str) -> str | None:
        """Return whatever the final result adds beyond what already fired."""
        remaining = self._unfired_tail(text)
        self.reset()
        return remaining or None

    def reset(self) -> None:
        """Start a new utterance."""
        self._fired_words = 0

    def _unfired_tail(self, text: str) -> str:
        return " ".join(text.split(" ")[self._fired_words :])
