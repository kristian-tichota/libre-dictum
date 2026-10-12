from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..meters import Microphone

FLOOR_DB = -60.0

CEILING_DB = -10.0

LEVEL_SECONDS = 0.05

LEVEL_HISTORY = 32


def rms(audio: np.ndarray) -> float:
    """Root-mean-square level of a block; 0.0 for an empty one."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


def loudness(level: float) -> float:
    """An RMS level on a decibel scale: 0.0 at FLOOR_DB, 1.0 at CEILING_DB."""
    if level <= 0.0:
        return 0.0
    decibels = 20.0 * math.log10(level)
    return min(max((decibels - FLOOR_DB) / (CEILING_DB - FLOOR_DB), 0.0), 1.0)


class VoiceActivityChunker:
    """Accumulates blocks into utterance-sized chunks."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        block_duration: float = 0.1,
        silence_seconds: float = 0.3,
        max_chunk_seconds: float = 30.0,
        energy_threshold: float = 0.01,
        pre_roll_seconds: float = 0.25,
    ) -> None:
        self.sample_rate = sample_rate
        self.block_duration = block_duration
        self.silence_seconds = silence_seconds
        self.max_chunk_seconds = max_chunk_seconds
        self.energy_threshold = energy_threshold

        self._pre_roll: deque[np.ndarray] = deque(
            maxlen=max(1, int(pre_roll_seconds / block_duration))
        )
        self._chunk: list[np.ndarray] = []
        self._chunk_seconds = 0.0
        self._silence_seconds = 0.0

    @property
    def recording(self) -> bool:
        """Whether an utterance is currently being accumulated."""
        return bool(self._chunk)

    @property
    def quiet(self) -> float:
        """Seconds of silence since the current utterance last rose above the threshold."""
        return self._silence_seconds

    def push(self, block: np.ndarray) -> np.ndarray | None:
        """Feed one block; returns a finished chunk when the utterance ends."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        block_seconds = len(block) / self.sample_rate
        level = rms(block)

        if not self.recording:
            self._pre_roll.append(block)
            if level >= self.energy_threshold:
                self._chunk = list(self._pre_roll)
                self._pre_roll.clear()
                self._chunk_seconds = sum(len(b) for b in self._chunk) / self.sample_rate
                self._silence_seconds = 0.0
            return None

        self._chunk.append(block)
        self._chunk_seconds += block_seconds
        if level < self.energy_threshold:
            self._silence_seconds += block_seconds
        else:
            self._silence_seconds = 0.0

        ended = (
            self._silence_seconds >= self.silence_seconds
            or self._chunk_seconds >= self.max_chunk_seconds
        )
        return self.flush() if ended else None

    def flush(self) -> np.ndarray | None:
        """Return whatever has been accumulated so far and start over."""
        if not self.recording:
            return None
        audio = np.concatenate(self._chunk, axis=0).flatten()
        self.reset()
        return audio

    def reset(self) -> None:
        """Forget the current utterance."""
        self._chunk = []
        self._chunk_seconds = 0.0
        self._silence_seconds = 0.0
        self._pre_roll.clear()


class MicrophoneGauge:
    """Recent loudness and the chunker's progress, as a display draws them."""

    def __init__(
        self,
        chunker: VoiceActivityChunker,
        *,
        level_seconds: float = LEVEL_SECONDS,
        history: int = LEVEL_HISTORY,
    ) -> None:
        self._chunker = chunker
        self._slice = max(1, round(chunker.sample_rate * level_seconds))
        self._levels: deque[float] = deque(maxlen=history)
        self._heard = 0

    def hear(self, block: np.ndarray) -> None:
        """Record one block's loudness, a level per level_seconds of it."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        for start in range(0, len(block), self._slice):
            self._levels.append(loudness(rms(block[start : start + self._slice])))
            self._heard += 1

    def reading(self, *, transcribing: bool = False) -> Microphone:
        """The levels so far and where the chunker stands."""
        chunker = self._chunker
        return Microphone(
            levels=tuple(self._levels),
            gate=loudness(chunker.energy_threshold),
            recording=chunker.recording,
            quiet=chunker.quiet,
            patience=chunker.silence_seconds,
            transcribing=transcribing,
            heard=self._heard,
            period=self._slice / chunker.sample_rate,
        )

    def reset(self) -> None:
        """Forget the levels and start counting again."""
        self._levels.clear()
        self._heard = 0
