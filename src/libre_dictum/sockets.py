from __future__ import annotations

import os
import socket
from pathlib import Path

DIRECTORY_MODE = 0o700
SOCKET_MODE = 0o600


def staging_path(path: Path) -> Path:
    """Where a socket is bound before it is renamed into place at path."""
    return path.with_name(f".{path.name}.{os.getpid()}")


def listen_privately(path: Path, staging: Path, *, backlog: int) -> socket.socket:
    """A unix socket listening at path, reachable by this user alone."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    staging.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(staging))
        os.chmod(staging, SOCKET_MODE)
        listener.listen(backlog)
        os.rename(staging, path)
    except OSError:
        listener.close()
        staging.unlink(missing_ok=True)
        raise
    return listener
