from __future__ import annotations

import faulthandler
import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
FILE_BYTES = 2 * 1024 * 1024
FILE_BACKUPS = 4
DUMP_SIGNAL = signal.SIGUSR1

logger = logging.getLogger(__name__)


def configure(level: str, log_dir: Path | None, program: str) -> None:
    """Log to stderr, and to rotating PROGRAM.log and PROGRAM.faults (stack dumps) in log_dir."""
    logging.basicConfig(level=level, format=CONSOLE_FORMAT, datefmt="%H:%M:%S")
    logging.captureWarnings(True)
    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    if log_dir is None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / f"{program}.log",
        maxBytes=FILE_BYTES,
        backupCount=FILE_BACKUPS,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FILE_FORMAT))
    logging.getLogger().addHandler(handler)
    faults = (log_dir / f"{program}.faults").open("a", encoding="utf-8")
    faults.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()} started\n")
    faults.flush()
    faulthandler.enable(file=faults, all_threads=True)
    faulthandler.register(DUMP_SIGNAL, file=faults, all_threads=True, chain=False)


def _log_uncaught(
    kind: type[BaseException], value: BaseException, traceback: TracebackType | None
) -> None:
    """Log an exception that reached the top of the main thread."""
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, value, traceback)
        return
    logger.critical("Uncaught exception", exc_info=(kind, value, traceback))


def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
    """Log an exception that ended a thread."""
    if args.exc_type is SystemExit or args.exc_value is None:
        return
    name = args.thread.name if args.thread is not None else "unknown"
    logger.critical(
        "Uncaught exception in thread %s",
        name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
