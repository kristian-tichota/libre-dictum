from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import IO

from .errors import AlreadyRunningError

logger = logging.getLogger(__name__)

LOCK_NAME = "libre-dictum.lock"


def lock_path() -> Path:
    """Where the claim lives: one per user, on the same tmpfs as the socket."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "libre-dictum" / LOCK_NAME
    return Path(f"/tmp/libre-dictum-{os.getuid()}") / LOCK_NAME


def claim(path: Path | None = None) -> IO[str]:
    """Take the session's claim, or raise AlreadyRunningError naming the holder."""
    lock = path or lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = lock.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        holder = _holder(handle)
        handle.close()
        raise AlreadyRunningError(
            f"another libre-dictum is already running{holder}. Two of them both hold "
            "/dev/uinput and both listen, so every command would be typed twice. Stop "
            f"the first one and start again ({lock} is the claim)."
        ) from exc

    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    logger.debug("Claimed %s for pid %d", lock, os.getpid())
    return handle


def _holder(handle: IO[str]) -> str:
    """ " (pid 1234)" for the process holding the claim, or ""."""
    try:
        handle.seek(0)
        pid = handle.read().strip()
    except OSError:  # pragma: no cover - the handle was open a statement ago
        return ""
    return f" (pid {pid})" if pid.isdigit() else ""
