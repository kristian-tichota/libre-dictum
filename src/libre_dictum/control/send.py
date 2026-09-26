from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from ..errors import ProtocolError
from . import protocol

TIMEOUT_SECONDS = 5.0


def send(name: str, path: Path, *, timeout: float = TIMEOUT_SECONDS) -> str | None:
    """Ask the core at path to run name: None when it did, otherwise the reason it refused."""
    request = protocol.encode_request(name)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
        connection.sendall(request)
        with connection.makefile("rb") as stream:
            return protocol.decode_reply(stream.readline(protocol.MAX_LINE_BYTES))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="libre-dictum-send",
        description=(
            "Run one of the running libre-dictum's control commands. Exits 0 when it ran, "
            "1 when it was refused, and 2 when nothing answered."
        ),
    )
    parser.add_argument(
        "name", nargs="+", help="the command's name in 'control.commands'; words are joined"
    )
    args = parser.parse_args(argv)

    path = protocol.default_socket_path()
    try:
        refusal = send(" ".join(args.name), path)
    except (OSError, ProtocolError) as exc:
        print(f"libre-dictum-send: {path}: {exc}", file=sys.stderr)
        return 2
    if refusal is not None:
        print(f"libre-dictum-send: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
