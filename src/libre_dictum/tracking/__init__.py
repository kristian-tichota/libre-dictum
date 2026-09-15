from typing import TYPE_CHECKING, Any

from .gestures import GestureRecognizer

if TYPE_CHECKING:
    from .tracker import FaceRotationTracker

__all__ = ["FaceRotationTracker", "GestureRecognizer"]


def __getattr__(name: str) -> Any:
    """Load the camera loop only when something asks for it."""
    if name == "FaceRotationTracker":
        from .tracker import FaceRotationTracker

        return FaceRotationTracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
