from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from ..settings import TransformerSettings
from .base import AudioStream
from .transcriber import Transcriber, create_transcriber
from .vad import VoiceActivityChunker

logger = logging.getLogger(__name__)


class TransformerStream(AudioStream):
    """Free-text dictation through a transformer or whisper model."""

    def __init__(
        self,
        settings: TransformerSettings,
        *,
        name: str = "dictation",
        sample_rate: int = 16000,
        block_duration: float = 0.1,
        chunk_callback: Callable[[str], None] | None = None,
        partial_callback: Callable[[str | None], None] | None = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        super().__init__(
            name=name,
            sample_rate=sample_rate,
            block_duration=block_duration,
            chunk_callback=chunk_callback,
            partial_callback=partial_callback,
        )
        self.settings = settings
        self._transcriber = transcriber or create_transcriber(
            settings.model_name, device=settings.device
        )
        self._chunker = VoiceActivityChunker(
            sample_rate=sample_rate,
            block_duration=block_duration,
            silence_seconds=settings.silence_seconds,
            max_chunk_seconds=settings.max_chunk_seconds,
            energy_threshold=settings.energy_threshold,
            pre_roll_seconds=settings.pre_roll_seconds,
        )

    def _open_stream(self) -> sd.InputStream:
        def on_audio(indata, frames, time_info, status) -> None:  # noqa: ANN001 - sd API
            if status:
                logger.debug("Capture status for %s: %s", self.name, status)
            self._queue_block(indata.copy())

        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=on_audio,
            blocksize=int(self.sample_rate * self.block_duration),
        )

    def _process(self, block: np.ndarray) -> None:
        chunk = self._chunker.push(block)
        self._note_partial("" if self._chunker.recording else None)
        if chunk is not None:
            self._transcribe(chunk)

    def _on_disabled(self) -> None:
        self._chunker.reset()

    def _transcribe(self, audio: np.ndarray) -> None:
        if audio.size == 0:
            return
        text = self._transcriber.transcribe(audio, language=self.settings.lang).strip()
        logger.debug("Dictated: %r", text)
        self._emit(text)
