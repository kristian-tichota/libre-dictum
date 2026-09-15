from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol


class FaultIndicator(Protocol):
    """The part of a status indicator that shows something is wrong."""

    def set_fault(self, reason: str) -> None: ...

    def clear_fault(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Health:
    """A snapshot: which subsystems are running, and why the others are not."""

    working: tuple[str, ...] = ()
    failed: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failed

    def reason(self, subsystem: str) -> str | None:
        """Why subsystem is not working, or None if it is."""
        return dict(self.failed).get(subsystem)

    def summary(self) -> str:
        """One line naming what failed and what still runs, for a log or a tooltip."""
        if self.ok:
            return f"running: {', '.join(self.working) or 'nothing'}"
        broken = "; ".join(f"{name} ({reason})" for name, reason in self.failed)
        return f"failed: {broken} | running: {', '.join(self.working) or 'nothing'}"


class HealthMonitor:
    """Reports a health snapshot when, and only when, it changes."""

    def __init__(self, *, on_change: Callable[[Health], None] | None = None) -> None:
        self._on_change = on_change
        self._lock = threading.Lock()
        self._current = Health()

    def update(self, failures: Mapping[str, str], subsystems: Iterable[str] = ()) -> Health:
        """Replace the picture; notify if it differs from the last one."""
        health = Health(
            working=tuple(name for name in subsystems if name not in failures),
            failed=tuple(sorted(failures.items())),
        )

        with self._lock:
            changed = health != self._current
            self._current = health

        if changed and self._on_change is not None:
            self._on_change(health)
        return health

    @property
    def current(self) -> Health:
        """The last reported snapshot."""
        with self._lock:
            return self._current
