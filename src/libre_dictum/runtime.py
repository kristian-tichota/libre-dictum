from __future__ import annotations

import os
from pathlib import Path

DIRECTORY_NAME = "libre-dictum"


def runtime_dir() -> Path:
    """The per-user directory holding the session's claim and sockets."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / DIRECTORY_NAME
    return Path(f"/tmp/{DIRECTORY_NAME}-{os.getuid()}")
