from __future__ import annotations

import os
from pathlib import Path

from ..errors import ProtocolError
from ..runtime import runtime_dir

SOCKET_ENV = "LIBRE_DICTUM_CONTROL_SOCKET"

SOCKET_NAME = "control.sock"

MAX_LINE_BYTES = 1024

OK = "ok"

REFUSED = "error:"


def default_socket_path() -> Path:
    """Where the core takes control commands, unless told otherwise."""
    override = os.environ.get(SOCKET_ENV)
    if override:
        return Path(override)
    return runtime_dir() / SOCKET_NAME


def _words(text: str) -> str:
    """text on one line, with its whitespace collapsed."""
    return " ".join(text.split())


def encode_request(name: str) -> bytes:
    """The line asking the core to run the control command called name."""
    words = _words(name)
    if not words:
        raise ProtocolError("a control command needs a name")
    line = f"{words}\n".encode()
    if len(line) > MAX_LINE_BYTES:
        raise ProtocolError(f"a control command's name takes at most {MAX_LINE_BYTES - 1} bytes")
    return line


def decode_request(line: bytes) -> str:
    """The name one request line carries, or a ProtocolError saying what is wrong with it."""
    if not line.endswith(b"\n"):
        raise ProtocolError(f"a request is one line of at most {MAX_LINE_BYTES} bytes")
    try:
        words = _words(line.decode())
    except UnicodeDecodeError as exc:
        raise ProtocolError("a request is UTF-8") from exc
    if not words:
        raise ProtocolError("a control command needs a name")
    return words


def encode_reply(refusal: str | None) -> bytes:
    """ "ok", or the reason a request was refused, as one line."""
    if refusal is None:
        return f"{OK}\n".encode()
    return f"{REFUSED} {_words(refusal) or 'refused'}\n".encode()


def decode_reply(line: bytes) -> str | None:
    """None when the core ran the command, otherwise the reason it gave."""
    text = line.decode(errors="replace").strip()
    if text == OK:
        return None
    if text.startswith(REFUSED):
        return text.removeprefix(REFUSED).strip() or "refused"
    return f"unexpected reply {text!r}" if text else "no reply"
