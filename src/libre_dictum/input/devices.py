from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from evdev import AbsInfo, UInput
from evdev import ecodes as e

from ..errors import DeviceError
from ..smoothing import SubPixelCarry
from .keymap import key_code

MOUSE_CAPABILITIES: dict[int, Sequence[int]] = {
    e.EV_REL: [e.REL_X, e.REL_Y],
    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
}

ABSOLUTE_RANGE = 65535

TABLET_CAPABILITIES: dict[int, Sequence[Any]] = {
    e.EV_ABS: [
        (e.ABS_X, AbsInfo(value=0, min=0, max=ABSOLUTE_RANGE, fuzz=0, flat=0, resolution=0)),
        (e.ABS_Y, AbsInfo(value=0, min=0, max=ABSOLUTE_RANGE, fuzz=0, flat=0, resolution=0)),
    ],
    e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
}


@runtime_checkable
class InputBackend(Protocol):
    """Everything the executor needs from the outside world."""

    def press(self, key: str) -> None: ...

    def release(self, key: str) -> None: ...

    def move_relative(self, dx: float, dy: float) -> None: ...

    def move_absolute(self, x: float, y: float) -> None: ...

    def close(self) -> None: ...


class UinputBackend:
    """Writes to three /dev/uinput devices: a keyboard, a mouse and a tablet."""

    def __init__(self, keyboard: UInput, mouse: UInput, tablet: UInput) -> None:
        self._keyboard = keyboard
        self._mouse = mouse
        self._tablet = tablet
        self._carry = SubPixelCarry()

    @classmethod
    def open(cls) -> UinputBackend:
        """Create all three virtual devices, or raise DeviceError explaining why not."""
        try:
            return cls(
                UInput(),
                UInput(MOUSE_CAPABILITIES, name="virtual-mouse"),
                UInput(TABLET_CAPABILITIES, name="virtual-tablet"),
            )
        except Exception as exc:  # noqa: BLE001 - evdev raises UInputError, OSError or both
            raise DeviceError(
                "Cannot open /dev/uinput. Add your user to the 'input' group and log back in, "
                f"or run libre-dictum as root. ({exc})"
            ) from exc

    def _write(self, key: str, value: int) -> None:
        code, is_mouse = key_code(key)
        device = self._mouse if is_mouse else self._keyboard
        device.write(e.EV_KEY, code, value)
        device.syn()

    def press(self, key: str) -> None:
        self._write(key, 1)

    def release(self, key: str) -> None:
        self._write(key, 0)

    def move_relative(self, dx: float, dy: float) -> None:
        """Move by a fractional amount, carrying the remainder into the next call."""
        whole_dx, whole_dy = self._carry.take(dx, dy)
        if not whole_dx and not whole_dy:
            return
        self._mouse.write(e.EV_REL, e.REL_X, whole_dx)
        self._mouse.write(e.EV_REL, e.REL_Y, whole_dy)
        self._mouse.syn()

    def move_absolute(self, x: float, y: float) -> None:
        """Put the pointer at a normalised screen position, 0..1 from the top left."""
        self._tablet.write(e.EV_ABS, e.ABS_X, _absolute_axis(x))
        self._tablet.write(e.EV_ABS, e.ABS_Y, _absolute_axis(y))
        self._tablet.syn()

    def close(self) -> None:
        for device in (self._keyboard, self._mouse, self._tablet):
            device.close()

    def __enter__(self) -> UinputBackend:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _absolute_axis(value: float) -> int:
    """A normalised 0..1 coordinate as a device axis value, clamped to the range."""
    return max(0, min(ABSOLUTE_RANGE, round(value * ABSOLUTE_RANGE)))
