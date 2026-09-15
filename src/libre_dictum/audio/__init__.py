from typing import TYPE_CHECKING, Any

from .base import AudioStream

if TYPE_CHECKING:
    from .transformer_stream import TransformerStream
    from .vosk_stream import VoskStream

__all__ = ["AudioStream", "TransformerStream", "VoskStream"]


def __getattr__(name: str) -> Any:
    """Load a recognizer only when something asks for it."""
    if name == "VoskStream":
        from .vosk_stream import VoskStream

        return VoskStream
    if name == "TransformerStream":
        from .transformer_stream import TransformerStream

        return TransformerStream
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
