from __future__ import annotations

import os
from pathlib import Path

import pytest

from libre_dictum import instance
from libre_dictum.app import Application
from libre_dictum.errors import AlreadyRunningError, ConfigError
from libre_dictum.settings import parse_settings
from tests.conftest import FakeStream


@pytest.fixture
def lock(tmp_path: Path) -> Path:
    return tmp_path / "session" / instance.LOCK_NAME


class TestTheClaim:
    def test_the_first_one_gets_it(self, lock):
        with instance.claim(lock) as claimed:
            assert not claimed.closed
        assert lock.exists()

    def test_it_makes_its_own_directory(self, lock):
        with instance.claim(lock):
            assert lock.parent.is_dir()

    def test_the_second_one_is_refused(self, lock):
        with instance.claim(lock), pytest.raises(AlreadyRunningError):
            instance.claim(lock)

    def test_the_refusal_names_the_process_holding_it(self, lock):
        with (
            instance.claim(lock),
            pytest.raises(AlreadyRunningError, match=f"pid {os.getpid()}"),
        ):
            instance.claim(lock)

    def test_the_refusal_says_what_two_of_them_would_do(self, lock):
        with instance.claim(lock), pytest.raises(AlreadyRunningError, match="typed twice"):
            instance.claim(lock)

    def test_the_refusal_names_the_claim_itself(self, lock):
        with instance.claim(lock), pytest.raises(AlreadyRunningError, match=str(lock)):
            instance.claim(lock)

    def test_releasing_it_lets_the_next_one_start(self, lock):
        instance.claim(lock).close()

        with instance.claim(lock) as second:
            assert not second.closed

    def test_a_lock_left_behind_by_a_crash_is_not_a_lockout(self, lock):
        lock.parent.mkdir(parents=True)
        lock.write_text("999999\n")

        with instance.claim(lock) as claimed:
            assert not claimed.closed

    def test_an_emptied_lock_file_still_gets_a_clear_refusal(self, lock):
        with instance.claim(lock) as first:
            first.seek(0)
            first.truncate()
            first.flush()

            with pytest.raises(AlreadyRunningError, match="already running"):
                instance.claim(lock)

    def test_the_holder_writes_its_own_pid(self, lock):
        with instance.claim(lock):
            assert lock.read_text().strip() == str(os.getpid())

    def test_a_shorter_pid_does_not_leave_the_old_one_behind(self, lock):
        lock.parent.mkdir(parents=True)
        lock.write_text("1234567890\n")

        with instance.claim(lock):
            assert lock.read_text().strip() == str(os.getpid())


class TestTheApplicationHoldsIt:
    """Taken before the devices, released after them, and never left behind."""

    def settings(self, tmp_path):
        return parse_settings({"modes": {"root mode": {"type": "vosk", "path": "m"}}}, tmp_path)

    def test_stopping_releases_it(self, backend, tmp_path, lock):
        handle = instance.claim(lock)
        app = Application(
            self.settings(tmp_path),
            backend=backend,
            stream_factory=FakeStream,
            claim=handle,
        )

        app.stop()

        assert handle.closed
        with instance.claim(lock) as next_one:
            assert not next_one.closed, "a stopped session still holds the claim"

    def test_it_is_released_after_the_devices_are_closed(self, backend, tmp_path, lock):
        order: list[str] = []
        handle = instance.claim(lock)
        app = Application(
            self.settings(tmp_path),
            backend=backend,
            stream_factory=FakeStream,
            claim=handle,
        )
        backend.close = lambda: order.append("devices")  # type: ignore[method-assign]
        handle.close = lambda: order.append("claim")  # type: ignore[method-assign]

        app.stop()

        assert order == ["devices", "claim"]

    def test_a_failure_after_claiming_does_not_lock_the_next_attempt_out(
        self, monkeypatch, tmp_path, lock
    ):
        monkeypatch.setattr(instance, "lock_path", lambda: lock)

        with pytest.raises(ConfigError):
            Application.from_config_dir(tmp_path / "nowhere")

        with instance.claim(lock) as after:
            assert not after.closed, "a rejected configuration held the claim"


class TestWhereItLives:
    def test_it_sits_beside_the_socket_in_the_runtime_directory(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

        assert instance.lock_path() == tmp_path / "libre-dictum" / instance.LOCK_NAME

    def test_without_one_it_falls_back_per_uid(self, monkeypatch):
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        assert instance.lock_path() == Path(f"/tmp/libre-dictum-{os.getuid()}") / instance.LOCK_NAME

    def test_the_display_socket_override_does_not_move_it(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        monkeypatch.setenv("LIBRE_DICTUM_HUD_SOCKET", "/tmp/somewhere/else.sock")

        assert instance.lock_path().parent == tmp_path / "libre-dictum"
