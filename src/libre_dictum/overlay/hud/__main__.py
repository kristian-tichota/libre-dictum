from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

from ...errors import LibreDictumError
from ..protocol import SOCKET_ENV, Catalogue, State, default_socket_path
from . import render, style
from .client import HudClient

logger = logging.getLogger(__name__)

DUMP_TIMEOUT_SECONDS = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="libre-dictum-hud",
        description="The libre-dictum on-screen chip and command sheet.",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=None,
        help=(
            f"where libre-dictum publishes (default: {default_socket_path()}, "
            f"overridden by ${SOCKET_ENV})"
        ),
    )
    parser.add_argument(
        "--chip-anchor",
        default="top-right",
        choices=list(render.ANCHORS),
        help="which corner the chip sits in (default: top-right)",
    )
    parser.add_argument(
        "--sheet-anchor",
        default="right",
        choices=list(render.ANCHORS),
        help="which edge the sheet docks to (default: right)",
    )
    parser.add_argument(
        "--opacity",
        type=float,
        default=style.DEFAULT_OPACITY,
        metavar="0..1",
        help=(
            "how much of the panel behind the text is painted "
            f"(default: {style.DEFAULT_OPACITY}; 0 draws no panel at all, just the text)"
        ),
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=style.DEFAULT_SCALE,
        metavar=f"{style.MIN_SCALE}..{style.MAX_SCALE}",
        help=(
            "how large the text is against the desktop's own font "
            f"(default: {style.DEFAULT_SCALE})"
        ),
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="print what the surfaces would say and exit; needs no display server",
    )
    parser.add_argument(
        "--outputs",
        action="store_true",
        help="print the name of every video output, which is what overlay.output.name takes",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help=(
            "print where this session lets the surfaces sit -- layer shell or an "
            "ordinary window -- and exit non-zero if it is the latter"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="how much to report, case-insensitive (default: INFO)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dump:
        return dump(args.socket)

    try:
        from .window import Hud, outputs, probe

        if args.outputs:
            return outputs()
        if args.probe:
            return probe(args.opacity, args.scale)
        return Hud(
            args.socket,
            chip_anchor=args.chip_anchor,
            sheet_anchor=args.sheet_anchor,
            opacity=args.opacity,
            scale=args.scale,
        ).run()
    except (ImportError, ValueError) as exc:
        logger.error(
            "The overlay needs GTK4 and its Python bindings (%s). On Debian/Ubuntu: "
            "apt install python3-gi libgirepository-2.0-0 libgtk-4-1 libgtk4-layer-shell0; "
            "then uv sync --extra overlay",
            exc,
        )
        return 1
    except LibreDictumError as exc:
        logger.error("%s", exc)
        return 1


def dump(socket_path: Path | None) -> int:
    """Print the chip and the sheet as text, then stop."""
    arrived = threading.Event()
    client = HudClient(socket_path or default_socket_path(), on_change=arrived.set)
    client.start()
    try:
        deadline = time.monotonic() + DUMP_TIMEOUT_SECONDS
        while client.state is None or client.catalogue is None:
            if time.monotonic() >= deadline:
                print(f"nothing published on {client.path}", file=sys.stderr)
                return 1
            arrived.wait(0.05)
            arrived.clear()
        for line in as_text(client.state, client.catalogue):
            print(line)
    finally:
        client.stop()
    return 0


def as_text(state: State, catalogue: Catalogue | None) -> list[str]:
    """The chip and the sheet as lines of text."""
    chip = render.chip_of(state, catalogue)
    if not state.chip:
        lines = ["-- chip hidden --"]
    else:
        lines = [f"{chip.mode}{'':4}{chip.glyphs}".rstrip()]
        if chip.layer_line:
            lines.append(f"  {chip.layer_line}")
        if chip.gesture_line:
            lines.append(f"  {chip.gesture_line}")
        lines.append(f"  {chip.mark} {chip.partial or chip.utterance}".rstrip())
        if chip.fault:
            lines.append(f"  ! {chip.fault}")
        if chip.note:
            lines.append(f"  ({chip.note})")

    panel = render.panel_of(state, catalogue)
    if panel is None:
        lines.append("-- sheet closed --")
        return lines

    lines.extend(["", f"{panel.title.upper()}   {panel.hint}", "-" * 60])
    width = max((len(row.label) for row in panel.rows), default=0) + 2
    columns = _column_widths(panel)
    if panel.columns:
        lines.append(
            " " * width
            + "  ".join(f"{column:>{columns[index]}}" for index, column in enumerate(panel.columns))
        )
    for row in panel.rows:
        cells = [
            f"{item.label:<{columns[index]}}" if item.tone else f"{item.label:>{columns[index]}}"
            for index, item in enumerate(row.items)
        ]
        cells += [" " * columns[index] for index in range(len(cells), panel.width)]
        lines.append(f"{row.label:<{width}}{'  '.join(cells)}  {row.phrase}".rstrip())
    for item in panel.items:
        lines.append(f"  {item.label:<28} {item.detail:>4}   {item.phrase}".rstrip())
    if panel.notes:
        lines.extend(["", *panel.notes])
    if panel.footer:
        lines.extend(["-" * 60, "  " + "   ".join(panel.footer)])
    return lines


def _column_widths(panel: render.Panel, least: int = 8) -> list[int]:
    """How wide each of a table's columns has to be to hold everything in it."""
    widths = [least] * panel.width
    for index, column in enumerate(panel.columns):
        widths[index] = max(widths[index], len(column))
    for row in panel.rows:
        for index, item in enumerate(row.items):
            widths[index] = max(widths[index], len(item.label))
    return widths


if __name__ == "__main__":
    sys.exit(main())
