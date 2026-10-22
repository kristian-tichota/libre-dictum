from __future__ import annotations

import logging
import os
import resource
import threading

logger = logging.getLogger(__name__)

HIGHEST_NICE = -20
LOWEST_NICE = 19

_TASKS = "/proc/self/task"
_REALTIME = frozenset({os.SCHED_FIFO, os.SCHED_RR})
_warned: set[int | None] = set()


def realtime_ceiling(limit: int) -> int:
    """The highest SCHED_FIFO priority an RLIMIT_RTPRIO of limit permits, 0 for none."""
    top = os.sched_get_priority_max(os.SCHED_FIFO)
    return top if limit == resource.RLIM_INFINITY else max(0, min(limit, top))


def nice_floor(limit: int) -> int:
    """The lowest nice value an RLIMIT_NICE of limit permits."""
    if limit == resource.RLIM_INFINITY:
        return HIGHEST_NICE
    return max(HIGHEST_NICE, LOWEST_NICE + 1 - limit)


def claim() -> None:
    """Raise the scheduling limits to their hard values, lift every thread, and log the grant."""
    for which in (resource.RLIMIT_RTPRIO, resource.RLIMIT_NICE):
        soft, hard = resource.getrlimit(which)
        if soft != hard:
            resource.setrlimit(which, (hard, hard))
    sweep()

    top = os.sched_get_priority_max(os.SCHED_FIFO)
    rtprio = resource.getrlimit(resource.RLIMIT_RTPRIO)[0]
    nice = resource.getrlimit(resource.RLIMIT_NICE)[0]
    ceiling, floor = realtime_ceiling(rtprio), nice_floor(nice)
    if ceiling == top and floor == HIGHEST_NICE:
        logger.info(
            "Recognizers run at real-time priority %d, every other thread at nice %d",
            ceiling,
            floor,
        )
        return
    logger.warning(
        "Scheduling falls short: real-time priority %d of %d and nice %d of %d, because "
        "RLIMIT_RTPRIO is %s and RLIMIT_NICE is %s; set LimitRTPRIO=%d and LimitNICE=%d "
        "on user@%d.service",
        ceiling,
        top,
        os.getpriority(os.PRIO_PROCESS, 0),
        HIGHEST_NICE,
        _describe_limit(rtprio),
        _describe_limit(nice),
        top,
        LOWEST_NICE + 1 - HIGHEST_NICE,
        os.getuid(),
    )


def realtime() -> bool:
    """Move the calling thread to the highest SCHED_FIFO priority permitted, else lift it."""
    ceiling = realtime_ceiling(resource.getrlimit(resource.RLIMIT_RTPRIO)[0])
    if ceiling:
        try:
            os.sched_setscheduler(
                0, os.SCHED_FIFO | os.SCHED_RESET_ON_FORK, os.sched_param(ceiling)
            )
            return True
        except OSError as exc:
            _warn_once(exc, "real-time priority %d was refused", ceiling)
    _lift(threading.get_native_id(), _floor())
    return False


def sweep() -> None:
    """Lift every thread of the process that is not at a real-time policy."""
    floor = _floor()
    try:
        tasks = os.listdir(_TASKS)
    except OSError as exc:
        _warn_once(exc, "cannot list %s", _TASKS)
        return
    for task in tasks:
        _lift(int(task), floor)


def _floor() -> int:
    return nice_floor(resource.getrlimit(resource.RLIMIT_NICE)[0])


def _lift(tid: int, floor: int) -> None:
    """Mark a thread's children for normal priority, then lower its nice value to floor."""
    try:
        policy = os.sched_getscheduler(tid)
        if policy & ~os.SCHED_RESET_ON_FORK in _REALTIME:
            return
        if not policy & os.SCHED_RESET_ON_FORK:
            os.sched_setscheduler(tid, policy | os.SCHED_RESET_ON_FORK, os.sched_param(0))
        if os.getpriority(os.PRIO_PROCESS, tid) > floor:
            os.setpriority(os.PRIO_PROCESS, tid, floor)
    except ProcessLookupError:
        return
    except OSError as exc:
        _warn_once(exc, "thread %d cannot be lifted to nice %d", tid, floor)


def _warn_once(exc: OSError, message: str, *args: object) -> None:
    if exc.errno in _warned:
        return
    _warned.add(exc.errno)
    logger.warning("Scheduling: " + message + " (%s)", *args, exc)


def _describe_limit(limit: int) -> str:
    return "unlimited" if limit == resource.RLIM_INFINITY else str(limit)
