from typing import TYPE_CHECKING, Any

from .reader import PedalEvent, PedalReader

if TYPE_CHECKING:
    from .device import PedalWatcher

__all__ = ["PedalEvent", "PedalReader", "PedalWatcher"]


def __getattr__(name: str) -> Any:
    """Load the HID thread only when something asks for it."""
    if name == "PedalWatcher":
        from .device import PedalWatcher

        return PedalWatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
