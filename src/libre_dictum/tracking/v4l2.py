from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path

from ..errors import CameraError
from .cameras import (
    CAPABILITY_SIZE,
    QUERYCAP,
    CameraNode,
    decode_capability,
    index_from_device_name,
)

logger = logging.getLogger(__name__)

DEVICE_DIR = Path("/dev")

BY_ID_DIR = Path("/dev/v4l/by-id")


def enumerate_cameras(device_dir: Path = DEVICE_DIR) -> tuple[CameraNode, ...]:
    """Every video node that answered, by index."""
    found = [node for path in sorted(device_dir.glob("video*")) if (node := _query(path))]
    return tuple(sorted(found, key=lambda node: node.index))


def resolve(spec: int | str | None) -> int | str | None:
    """A device path becomes the index it points at."""
    if not isinstance(spec, str) or not spec.startswith("/"):
        return spec

    path = Path(spec)
    if not path.exists():
        raise CameraError(f"'camera' is {spec!r} and there is no such device node")
    return index_from_device_name(path.resolve().name)


def stable_path(index: int, by_id_dir: Path = BY_ID_DIR) -> str | None:
    """The by-id symlink pointing at videoN, if the kernel made one."""
    if not by_id_dir.is_dir():
        return None
    for link in sorted(by_id_dir.iterdir()):
        if link.resolve().name == f"video{index}":
            return str(link)
    return None


def _query(path: Path) -> CameraNode | None:
    """What one node says it is, or None when it would not say."""
    try:
        index = index_from_device_name(path.name)
    except CameraError:
        return None

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as exc:
        logger.debug("%s will not open (%s)", path, exc)
        return None

    try:
        payload = fcntl.ioctl(descriptor, QUERYCAP, bytes(CAPABILITY_SIZE))
    except OSError as exc:
        logger.debug("%s answered no VIDIOC_QUERYCAP (%s)", path, exc)
        return None
    finally:
        os.close(descriptor)

    return decode_capability(index, payload)
