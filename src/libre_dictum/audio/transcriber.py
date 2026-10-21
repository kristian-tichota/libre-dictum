from __future__ import annotations

import inspect
import logging
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

WHISPER_PREFIX = "whisper-"

SAMPLE_RATE = 16000


def load_cached_first(loader: Any, model_name: str, **kwargs: Any) -> Any:
    """Load model_name through loader, treating the local cache as authoritative."""
    try:
        return loader.from_pretrained(model_name, local_files_only=True, **kwargs)
    except OSError:
        logger.info("%s is not in the local model cache; downloading it now", model_name)
        return loader.from_pretrained(model_name, **kwargs)


def priority_stream(device: Any) -> Any:
    """Return the device's highest-priority stream after its pending work, or None off-GPU."""
    import torch

    device = torch.device(device)
    if device.type != "cuda":
        return None
    torch.cuda.synchronize(device)
    return torch.cuda.Stream(device=device, priority=torch.cuda.Stream.priority_range()[1])


def takes_language(call: Any) -> bool:
    """Whether call declares a language parameter of its own."""
    try:
        parameters = inspect.signature(call).parameters
    except (TypeError, ValueError):
        return False
    return "language" in parameters


class Transcriber(Protocol):
    """Turns a block of audio into text."""

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str: ...


class WhisperTranscriber:
    """openai-whisper, loaded by short name (turbo, base, ...)."""

    def __init__(self, model_name: str) -> None:
        import whisper

        self.model_name = model_name
        self._model = whisper.load_model(model_name)
        self._stream = priority_stream(self._model.device)

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        import torch

        with torch.cuda.stream(self._stream):
            result = self._model.transcribe(audio, language=language)
        return str(result.get("text", ""))


class TransformersTranscriber:
    """Any HuggingFace speech-to-text model."""

    def __init__(self, model_name: str, device: str = "auto") -> None:
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        self.model_name = model_name
        placement = {"device_map": "auto"} if device == "auto" else {}
        self._model = load_cached_first(AutoModelForSpeechSeq2Seq, model_name, **placement)
        if device != "auto":
            self._model = self._model.to(device)
        self._processor = load_cached_first(AutoProcessor, model_name)
        self._processor_takes_language = takes_language(self._processor)
        self._stream = priority_stream(self._model.device)

    def transcribe(self, audio: np.ndarray, language: str = "en") -> str:
        import torch

        extract_kwargs: dict[str, Any] = {}
        generate_kwargs: dict[str, Any] = {"max_new_tokens": 256}
        if self._processor_takes_language:
            extract_kwargs["language"] = language
        elif getattr(self._model.generation_config, "is_multilingual", False):
            generate_kwargs["language"] = language

        inputs = self._processor(
            audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", **extract_kwargs
        )
        with torch.cuda.stream(self._stream):
            inputs = inputs.to(self._model.device, dtype=self._model.dtype)
            outputs = self._model.generate(**inputs, **generate_kwargs)
            return self._processor.batch_decode(outputs, skip_special_tokens=True)[0]


def create_transcriber(model_name: str, device: str = "auto") -> Transcriber:
    """Build the transcriber that model_name asks for."""
    if model_name.startswith(WHISPER_PREFIX):
        return WhisperTranscriber(model_name[len(WHISPER_PREFIX) :])
    return TransformersTranscriber(model_name, device=device)
