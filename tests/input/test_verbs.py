import textwrap

import pytest

from libre_dictum.input import executor as executor_module
from libre_dictum.input.executor import InputExecutor
from libre_dictum.layers import Layer


class TestVerbTable:
    def test_every_action_verb_has_a_handler(self, executor):
        from libre_dictum.input.dsl import ACTION_VERBS

        assert set(executor._verbs) == set(ACTION_VERBS)


class TestExec:
    @pytest.fixture
    def commands(self, monkeypatch):
        recorded: list[list[str]] = []
        monkeypatch.setattr(
            executor_module.subprocess, "run", lambda cmd, **kwargs: recorded.append(cmd)
        )
        return recorded

    def test_a_program_is_run(self, executor, commands):
        executor.execute("exec(firefox)")
        assert commands == [["firefox"]]

    def test_arguments_are_split(self, executor, commands):
        executor.execute("exec(notify-send hello there)")
        assert commands == [["notify-send", "hello", "there"]]

    def test_a_quoted_argument_stays_one_argument(self, executor, commands):
        executor.execute("exec(notify-send 'hello there')")
        assert commands == [["notify-send", "hello there"]]

    def test_a_failing_program_does_not_stop_the_response(self, executor, backend, monkeypatch):
        def explode(*args, **kwargs):
            raise FileNotFoundError("no such program")

        monkeypatch.setattr(executor_module.subprocess, "run", explode)
        executor.execute("exec(nonexistent) + a")
        assert backend.sequence == "a↓ a↑"


class TestPython:
    def test_python_is_executed(self, executor, tmp_path):
        target = tmp_path / "written.txt"
        executor.execute(f"python(open({str(target)!r}, 'w').write('hi'))")
        assert target.read_text() == "hi"

    def test_python_cannot_reach_into_the_package(self, executor, caplog):
        executor.execute("python(logger.warning('reached in'))")
        assert "failed" in caplog.text

    def test_broken_python_does_not_stop_the_response(self, executor, backend):
        executor.execute("python(1/0) + a")
        assert backend.sequence == "a↓ a↑"


class TestScript:
    @pytest.fixture
    def script_dir(self, tmp_path, monkeypatch):
        package = tmp_path / "scripts"
        package.mkdir()
        (package / "__init__.py").touch()
        (package / "recorder.py").write_text(textwrap.dedent("""
                calls = []

                def script(*args):
                    calls.append(args)
                """))
        monkeypatch.syspath_prepend(str(tmp_path))
        return package

    def test_a_script_is_called(self, executor, script_dir):
        executor.execute("script(recorder;;hello)")

        import scripts.recorder

        assert scripts.recorder.calls == [("hello",)]

    def test_arguments_are_split_on_the_separator(self, executor, script_dir):
        executor.execute("script(recorder;;one;;two)")

        import scripts.recorder

        assert scripts.recorder.calls[-1] == ("one", "two")

    def test_a_missing_script_does_not_stop_the_response(self, executor, backend, script_dir):
        executor.execute("script(nonexistent;;x) + a")
        assert backend.sequence == "a↓ a↑"


class TestModeSwitch:
    def test_mode_calls_the_switcher(self, backend):
        switched: list[str] = []
        executor = InputExecutor(backend, mode_switcher=switched.append, sleep=lambda _: None)

        executor.execute("mode(dictate mode)")
        assert switched == ["dictate mode"]

    def test_mode_without_a_switcher_is_reported(self, executor, caplog):
        executor.execute("mode(dictate mode)")
        assert "mode switcher" in caplog.text


class TestSleepAndWake:
    """Two verbs, so a doubled ask is a no-op."""

    def test_sleep_asks_for_sleep(self, backend):
        asked: list[tuple[str, bool, object]] = []
        executor = InputExecutor(backend, sleeper=lambda *call: asked.append(call))

        executor.execute("sleep(gesture)", origin=Layer.PEDAL)

        assert asked == [("gesture", True, Layer.PEDAL)]

    def test_wake_asks_for_the_other_direction(self, backend):
        asked: list[tuple[str, bool, object]] = []
        executor = InputExecutor(backend, sleeper=lambda *call: asked.append(call))

        executor.execute("wake()")

        assert asked == [("", False, None)]

    def test_the_origin_does_not_leak_into_the_next_response(self, backend):
        asked: list[tuple[str, bool, object]] = []
        executor = InputExecutor(backend, sleeper=lambda *call: asked.append(call))

        executor.execute("sleep()", origin=Layer.PEDAL)
        executor.execute("wake()")

        assert [call[2] for call in asked] == [Layer.PEDAL, None]

    def test_without_a_switch_it_is_reported(self, executor, caplog):
        executor.execute("sleep()")

        assert "sleep switch" in caplog.text

    def test_a_switch_that_raises_does_not_take_the_thread_with_it(self, backend, caplog):
        def boom(argument, asleep, origin):
            raise RuntimeError("no")

        executor = InputExecutor(backend, sleeper=boom)

        assert executor.execute("sleep() + b") is True
        assert backend.sequence == "b↓ b↑"
