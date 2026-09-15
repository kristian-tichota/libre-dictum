from __future__ import annotations

from collections import deque

import numpy as np


def rms(audio: np.ndarray) -> float:
    """Root-mean-square level of a block; 0.0 for an empty one."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


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
