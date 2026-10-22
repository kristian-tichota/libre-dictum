from __future__ import annotations

import json
import os
import resource
import subprocess
import sys

import pytest

from libre_dictum import scheduling

UNPRIVILEGED_SESSION = """
import json, logging, os, resource, threading
from libre_dictum import scheduling

logging.basicConfig(format="%(levelname)s %(message)s")
resource.setrlimit(resource.RLIMIT_RTPRIO, (0, 0))
resource.setrlimit(resource.RLIMIT_NICE, (0, 0))

gate = threading.Event()
idle = [threading.Thread(target=gate.wait) for _ in range(3)]
for thread in idle:
    thread.start()
scheduling.claim()
swept = all(
    os.sched_getscheduler(int(task)) & os.SCHED_RESET_ON_FORK
    for task in os.listdir("/proc/self/task")
)
gate.set()

outcome = {}
def recognizer():
    outcome["realtime"] = scheduling.realtime()
    outcome["policy"] = os.sched_getscheduler(0)
fresh = threading.Thread(target=recognizer)
fresh.start()
fresh.join()
print(json.dumps({"swept": swept, **outcome}))
"""


@pytest.fixture(scope="module")
def session() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", UNPRIVILEGED_SESSION],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


class TestLimits:
    @pytest.mark.parametrize(
        ("limit", "ceiling"), [(resource.RLIM_INFINITY, 99), (200, 99), (50, 50), (0, 0)]
    )
    def test_rlimit_rtprio_caps_the_real_time_priority(self, limit, ceiling):
        assert scheduling.realtime_ceiling(limit) == ceiling

    @pytest.mark.parametrize(
        ("limit", "floor"),
        [(resource.RLIM_INFINITY, -20), (40, -20), (30, -10), (20, 0), (0, 20)],
    )
    def test_rlimit_nice_sets_the_lowest_nice(self, limit, floor):
        assert scheduling.nice_floor(limit) == floor


class TestWithoutAGrant:
    def test_every_thread_starts_its_children_at_normal_priority(self, session):
        assert json.loads(session.stdout)["swept"]

    def test_a_recognizer_falls_back_to_the_nice_floor(self, session):
        outcome = json.loads(session.stdout)
        assert outcome["realtime"] is False
        assert outcome["policy"] == os.SCHED_OTHER | os.SCHED_RESET_ON_FORK

    def test_the_shortfall_names_the_limits_to_raise(self, session):
        assert "WARNING Scheduling falls short" in session.stderr
        assert "LimitRTPRIO=99 and LimitNICE=40" in session.stderr
