from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .overlay.model import navigation_phrases
from .settings import AppSettings, ModeKind, ModeSettings, spoken_phrase


@runtime_checkable
class RecognitionStream(Protocol):
    """What a mode needs from its recognizer."""

    failure: BaseException | None

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def enable(self) -> None: ...

    def disable(self) -> None: ...


class StreamFactory(Protocol):
    """Builds the stream for one mode."""

    def __call__(
        self,
        mode: ModeSettings,
        settings: AppSettings,
        on_text: Callable[[str], None],
        on_partial: Callable[[str | None], None] | None = None,
    ) -> RecognitionStream: ...


def always_recognized(settings: AppSettings) -> list[str]:
    """Every mode name plus the reserved phrases, normalized and lowercased."""
    phrases = [spoken_phrase(name) for name in settings.modes]
    phrases.extend(settings.reserved_phrases)
    return list(dict.fromkeys(phrases))


def mode_vocabulary(mode: ModeSettings, settings: AppSettings) -> list[str]:
    """Every phrase one mode's recognizer must know beyond its own command patterns."""
    phrases = always_recognized(settings)
    phrases.extend(navigation_phrases(mode, settings))
    return list(dict.fromkeys(phrases))


def describe_model(mode: ModeSettings) -> str:
    """What a mode is about to load."""
    if mode.kind is ModeKind.VOSK and mode.vosk is not None:
        return f"VOSK model {mode.vosk.model_path}"
    if mode.transformer is not None:
        return f"transformer model {mode.transformer.model_name}"
    return f"{mode.kind} model"


def build_stream(
    mode: ModeSettings,
    settings: AppSettings,
    on_text: Callable[[str], None],
    on_partial: Callable[[str | None], None] | None = None,
) -> RecognitionStream:
    """Construct the recognizer for mode."""
    if mode.kind is ModeKind.VOSK:
        from .audio.vosk_stream import VoskStream

        assert mode.vosk is not None  # guaranteed by settings validation
        return VoskStream(
            mode.commands,
            model_path=mode.vosk.model_path,
            name=mode.name,
            extra_phrases=mode_vocabulary(mode, settings),
            chunk_callback=on_text,
            partial_callback=on_partial,
        )

    from .audio.transformer_stream import TransformerStream

    assert mode.transformer is not None  # guaranteed by settings validation
    return TransformerStream(
        mode.transformer,
        name=mode.name,
        chunk_callback=on_text,
        partial_callback=on_partial,
    )
