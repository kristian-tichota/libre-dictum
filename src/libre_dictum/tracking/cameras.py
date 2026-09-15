from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass

from ..errors import CameraError

CAPABILITY_STRUCT = "<16s32s32sIII12x"
CAPABILITY_SIZE = struct.calcsize(CAPABILITY_STRUCT)

QUERYCAP = (2 << 30) | (CAPABILITY_SIZE << 16) | (ord("V") << 8) | 0

CAP_VIDEO_CAPTURE = 0x00000001
CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
CAP_META_CAPTURE = 0x00800000
CAP_DEVICE_CAPS = 0x80000000

_DEVICE_NAME = re.compile(r"^video(\d+)$")


@dataclass(frozen=True, slots=True)
class CameraNode:
    """One /dev/videoN and what it said it was."""

    index: int
    card: str = ""
    driver: str = ""
    bus_info: str = ""
    capture: bool = True
    metadata: bool = False

    @property
    def path(self) -> str:
        return f"/dev/video{self.index}"

    def describe(self) -> str:
        """This node as video0 (card name, frames)."""
        kind = "frames" if self.capture else "metadata" if self.metadata else "no frames"
        return f"video{self.index} ({self.card or 'unnamed'}, {kind})"

    def matches(self, name: str) -> bool:
        """Whether a configured name picks this node out."""
        wanted = name.strip().casefold()
        return wanted in self.card.casefold() or wanted in self.bus_info.casefold()


def decode_capability(index: int, payload: bytes) -> CameraNode:
    """One VIDIOC_QUERYCAP answer as a record."""
    driver, card, bus_info, _version, capabilities, device_caps = struct.unpack(
        CAPABILITY_STRUCT, payload
    )
    caps = device_caps if capabilities & CAP_DEVICE_CAPS else capabilities
    return CameraNode(
        index=index,
        card=_kernel_string(card),
        driver=_kernel_string(driver),
        bus_info=_kernel_string(bus_info),
        capture=bool(caps & (CAP_VIDEO_CAPTURE | CAP_VIDEO_CAPTURE_MPLANE)),
        metadata=bool(caps & CAP_META_CAPTURE),
    )


def index_from_device_name(name: str) -> int:
    """The 7 in video7, the number OpenCV opens a camera by."""
    match = _DEVICE_NAME.match(name)
    if not match:
        raise CameraError(
            f"{name!r} is not a video device node. 'camera' takes a name, an index, or a "
            "path that resolves to /dev/videoN"
        )
    return int(match.group(1))


def describe(nodes: Sequence[CameraNode]) -> str:
    """Every node there is, for an error that has to say what it found instead."""
    return ", ".join(node.describe() for node in nodes) or "none"


def candidates(nodes: Sequence[CameraNode], spec: int | str | None) -> tuple[CameraNode, ...]:
    """Every node that matches spec, in the order to try them."""
    ordered = tuple(sorted(nodes, key=lambda node: node.index))
    if not ordered:
        raise CameraError("there are no video devices at all; is the camera plugged in?")

    if isinstance(spec, int):
        return (_pinned(ordered, spec),)
    if spec is None:
        return _with_frames(ordered, "no video device hands out frames", ordered)
    return _named(ordered, spec)


def _pinned(nodes: Sequence[CameraNode], index: int) -> CameraNode:
    """The node at index, or an error saying what is at that number instead."""
    for node in nodes:
        if node.index != index:
            continue
        if node.capture:
            return node
        raise CameraError(
            f"'camera' is {index}, which is {node.describe()} -- the same camera's "
            f"metadata, not its picture. Present: {describe(nodes)}. Remove 'camera' to "
            "have one found, or name it instead of numbering it"
        )
    raise CameraError(
        f"'camera' is {index} and there is no video{index}. Present: {describe(nodes)}. "
        "Remove 'camera' to have one found, or name it instead of numbering it"
    )


def _named(nodes: Sequence[CameraNode], name: str) -> tuple[CameraNode, ...]:
    matched = tuple(node for node in nodes if node.matches(name))
    if not matched:
        raise CameraError(
            f"'camera' is {name!r} and no video device is called that. "
            f"Present: {describe(nodes)}"
        )
    return _with_frames(matched, f"every video device matching {name!r} is a metadata node", nodes)


def _with_frames(
    nodes: Sequence[CameraNode], complaint: str, present: Sequence[CameraNode]
) -> tuple[CameraNode, ...]:
    frames = tuple(node for node in nodes if node.capture)
    if not frames:
        raise CameraError(f"{complaint}. Present: {describe(present)}")
    return frames


def _kernel_string(field: bytes) -> str:
    """A fixed-width, NUL-padded string out of a kernel struct."""
    return field.split(b"\0", 1)[0].decode("utf-8", "replace")
