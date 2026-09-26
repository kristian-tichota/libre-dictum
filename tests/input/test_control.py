from __future__ import annotations

import json
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from libre_dictum.app import Application
from libre_dictum.control import protocol
from libre_dictum.control.send import main, send
from libre_dictum.control.service import ControlService
from libre_dictum.errors import ProtocolError
from libre_dictum.layers import Layer
from libre_dictum.settings import parse_settings
from tests.conftest import FakeStream

CONFIG = {
    "enable_pedals": True,
    "enable_control": True,
    "starting_mode": "command mode",
    "control": {
        "commands": {
            "break started": "mode(pedal:break pedals)",
            "break over": "mode(pedal:voice)",
            "say hi": "type(hi)",
        }
    },
    "modes": {
        "command mode": {"type": "vosk", "path": "m"},
        "mouse mode": {"type": "vosk", "path": "m"},
        "break pedals": {"pedals": {"right": "space"}},
    },
}


@pytest.fixture
def socket_path(tmp_path_factory) -> Path:
    """Short enough for AF_UNIX, which a test-named tmp_path is not."""
    return tmp_path_factory.mktemp("ctl") / "c.sock"


@pytest.fixture
def config_dir(tmp_path) -> Path:
    """A real configuration directory, so that reload() has something to re-read."""
    (tmp_path / "m").mkdir()
    (tmp_path / "config.json").write_text(json.dumps(CONFIG))
    return tmp_path


@pytest.fixture
def running(backend, config_dir, socket_path, monkeypatch):
    """Starts an application with the given configuration, and stops every one afterwards."""
    monkeypatch.setattr(Application, "_create_pedals", lambda self, settings: None)
    started: list[Application] = []

    def start(config=CONFIG) -> Application:
        app = Application(
            parse_settings(config, config_dir),
            backend=backend,
            stream_factory=FakeStream,
            control_socket=socket_path,
        )
        app.start()
        started.append(app)
        return app

    yield start
    for app in started:
        app.stop()


def replies(path: Path, payload: bytes) -> bytes:
    """Everything the core answers to payload, sent on one connection."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        connection.sendall(payload)
        connection.shutdown(socket.SHUT_WR)
        return connection.makefile("rb").read()


class TestADeclaredName:
    def test_it_runs_its_response(self, running, socket_path):
        app = running()

        assert send("break started", socket_path) is None
        assert app.modes.layer_state().pedal == "break pedals"

    def test_case_and_spacing_do_not_matter(self, running, socket_path):
        app = running()

        send("Break   Started", socket_path)

        assert app.modes.layer_state().pedal == "break pedals"

    def test_the_pedals_rejoin_the_voice_layer_wherever_it_went(self, running, socket_path):
        app = running()
        app.modes.switch("mouse mode")

        send("break started", socket_path)
        send("break over", socket_path)

        assert app.modes.layer_state().pedal == "mouse mode"

    def test_a_sleeping_mechanism_does_not_stop_it(self, running, socket_path):
        app = running()
        app._request_sleep("pedal", True)
        app._request_sleep("pedal", True)
        assert app.asleep(Layer.PEDAL)

        send("break started", socket_path)

        assert app.modes.layer_state().pedal == "break pedals"

    def test_several_requests_share_one_connection(self, running, socket_path):
        app = running()

        assert replies(socket_path, b"break started\nsay hi\n") == b"ok\nok\n"
        assert app.backend.sequence == "h↓ h↑ i↓ i↑"


class TestAnythingElse:
    def test_an_undeclared_name_is_refused_and_types_nothing(self, running, socket_path):
        app = running()

        refusal = send("type hello", socket_path)

        assert refusal == "no control command is named 'type hello'"
        assert app.backend.events == []

    @pytest.mark.parametrize(
        "payload",
        [b"\xff\xfe\n", b"a" * 2000 + b"\n", b"   \n"],
        ids=["not utf-8", "too long", "blank"],
    )
    def test_a_malformed_request_is_refused(self, running, socket_path, payload):
        running()

        assert replies(socket_path, payload).startswith(b"error:")

    def test_the_tail_of_an_overlong_line_is_not_a_second_request(self, running, socket_path):
        app = running()

        answered = replies(socket_path, b"x" * (protocol.MAX_LINE_BYTES + 1) + b"break started\n")

        assert answered.startswith(b"error:") and answered.count(b"\n") == 1
        assert app.modes.layer_state().pedal == "command mode"

    def test_a_request_arriving_during_shutdown_is_refused(self, running, socket_path):
        app = running()
        app.shutdown()

        assert send("break started", socket_path) == "shutting down"
        assert app.modes.layer_state().pedal == "command mode"

    def test_a_failing_request_is_answered_and_the_next_one_still_runs(self, socket_path):
        answers = iter([RuntimeError("boom"), None])

        def handler(name):
            answer = next(answers)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        service = ControlService(socket_path, handler)
        service.start()
        try:
            assert send("first", socket_path) == "it failed; see the log"
            assert send("second", socket_path) is None
        finally:
            service.stop()


class TestTheSocket:
    def test_only_this_user_can_open_it(self, running, socket_path):
        running()

        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

    def test_there_is_none_unless_enabled(self, running, socket_path):
        app = running({**CONFIG, "enable_control": False})

        assert app.control is None
        assert not socket_path.exists()

    def test_a_reload_switches_it_off_and_on(self, running, socket_path, config_dir):
        app = running()

        (config_dir / "config.json").write_text(json.dumps({**CONFIG, "enable_control": False}))
        app.reload()
        assert not socket_path.exists()

        (config_dir / "config.json").write_text(json.dumps(CONFIG))
        app.reload()
        assert send("break started", socket_path) is None

    def test_stopping_removes_it(self, running, socket_path):
        app = running()
        app.stop()

        assert not socket_path.exists()

    def test_one_that_will_not_bind_is_a_failure_on_the_tray(self, running, socket_path):
        socket_path.parent.rmdir()
        socket_path.parent.write_text("a file where the directory should be")

        app = running()

        assert "control socket" in app._failures()


class TestTheService:
    def test_stopping_ends_its_thread(self, socket_path):
        service = ControlService(socket_path, lambda name: None)
        service.start()
        service.stop()

        service._acceptor.join(5)
        assert not service._acceptor.is_alive()

    def test_a_listener_that_dies_is_a_failure_a_restart_repairs(self, socket_path):
        service = ControlService(socket_path, lambda name: None)
        service.start()
        service._listener.shutdown(socket.SHUT_RDWR)
        service._acceptor.join(5)

        assert service.failure is not None and not service.listening

        service.start()
        try:
            assert send("anything", socket_path) is None
            assert service.failure is None
        finally:
            service.stop()


class TestTheClient:
    def test_it_exits_by_what_became_of_the_request(self, running, socket_path, monkeypatch):
        running()
        monkeypatch.setenv(protocol.SOCKET_ENV, str(socket_path))

        assert main(["break", "started"]) == 0
        assert main(["nonsense"]) == 1

    def test_nothing_answering_is_its_own_exit(self, socket_path, monkeypatch):
        monkeypatch.setenv(protocol.SOCKET_ENV, str(socket_path))

        assert main(["break", "started"]) == 2

    def test_it_needs_none_of_the_core(self):
        probe = (
            "import sys, libre_dictum.control.send;"
            "print(sorted(m for m in ('evdev', 'vosk', 'sounddevice', 'numpy')"
            " if m in sys.modules))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == "[]", f"the client dragged in {result.stdout.strip()}"


class TestTheProtocol:
    def test_a_request_is_one_line_with_its_spacing_collapsed(self):
        assert protocol.encode_request("  break \t started ") == b"break started\n"
        assert protocol.decode_request(b"break started\n") == "break started"

    def test_a_request_needs_a_name(self):
        with pytest.raises(ProtocolError, match="needs a name"):
            protocol.encode_request("  ")

    def test_a_refusal_is_one_line_carrying_its_reason(self):
        assert protocol.encode_reply("no such\nname") == b"error: no such name\n"
        assert protocol.decode_reply(b"error: no such name\n") == "no such name"

    def test_a_reply_that_is_not_one_is_said_to_be_so(self):
        assert protocol.decode_reply(b"ok\n") is None
        assert protocol.decode_reply(b"") == "no reply"
        assert protocol.decode_reply(b"what\n") == "unexpected reply 'what'"

    def test_the_environment_variable_wins(self, monkeypatch):
        monkeypatch.setenv(protocol.SOCKET_ENV, "/run/elsewhere.sock")

        assert protocol.default_socket_path() == Path("/run/elsewhere.sock")

    def test_otherwise_it_sits_beside_the_display_socket(self, monkeypatch):
        monkeypatch.delenv(protocol.SOCKET_ENV, raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")

        assert protocol.default_socket_path() == Path("/run/user/1000/libre-dictum/control.sock")
