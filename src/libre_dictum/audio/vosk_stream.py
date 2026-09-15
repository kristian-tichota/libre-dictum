from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable

import sounddevice as sd
from vosk import KaldiRecognizer, Model

from ..pattern import PartialMatcher
from ..settings import Command
from .base import AudioStream
from .grammar import build_grammar, build_vocabulary
from .partials import PartialTracker

logger = logging.getLogger(__name__)


class VoskStream(AudioStream):
    """Recognizes a fixed vocabulary and fires commands from partial results."""

    def __init__(
        self,
        commands: Iterable[Command],
        *,
        model_path: str,
        name: str = "commands",
        extra_phrases: Iterable[str] = (),
        sample_rate: int = 16000,
        block_duration: float = 0.1,
        chunk_callback: Callable[[str], None] | None = None,
        partial_callback: Callable[[str | None], None] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            sample_rate=sample_rate,
            block_duration=block_duration,
            chunk_callback=chunk_callback,
            partial_callback=partial_callback,
        )
        self.vocabulary = build_vocabulary(commands, extra_phrases)
        self._tracker = PartialTracker(PartialMatcher(self.vocabulary))
        self._model = Model(model_path)
        self._recognizer = KaldiRecognizer(
            self._model, self.sample_rate, build_grammar(self.vocabulary)
        )

    def _open_stream(self) -> sd.RawInputStream:
        def on_audio(indata, frames, time_info, status) -> None:  # noqa: ANN001 - sd API
            self._queue_block(bytes(indata))

        return sd.RawInputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            callback=on_audio,
            blocksize=int(self.sample_rate * self.block_duration),
        )

    def _on_disabled(self) -> None:
        self._tracker.reset()

    def _on_resumed(self) -> None:
        """Drop the utterance Kaldi was half-way through when the mode was left."""
        self._recognizer.Reset()

    def _process(self, block: bytes) -> None:
        if self._recognizer.AcceptWaveform(block):
            text = json.loads(self._recognizer.Result()).get("text", "").strip()
            self._note_partial(None)
            self._emit(self._tracker.on_final(text) or "")
        else:
            partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()
            self._note_partial(partial or None)
            phrase = self._tracker.on_partial(partial)
            if phrase is None:
                logger.debug("Partial: %s", partial)
            else:
                self._emit(phrase)
