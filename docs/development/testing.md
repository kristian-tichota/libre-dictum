# Testing

The suite runs in seconds with **no optional extra installed at all**. That is a design property:
every touch of the outside world sits behind a seam, and nothing reaches a device or a system
library merely by being imported. `tests/audio/test_imports.py` and
`test_the_display_side_of_the_protocol_needs_none_of_the_core`, both of which spawn a subprocess,
keep the second half of that true, because a break in it is invisible on any machine that has the
extras installed.

## The seams

| Fake | Replaces | Asserts |
|---|---|---|
| `RecordingBackend` (`backend`) | `/dev/uinput` | the exact sequence, as `ctrl↓ c↓ c↑ ctrl↑` |
| `executor` | — | an `InputExecutor` whose `sleep` costs no wall-clock time |
| `FakeStream` + `stream_factory` | the recognizers | which streams were built, started, enabled, stopped |
| `FakeIndicator` | pystray | mode colours and fault text; it satisfies all three tray protocols |
| `settings_of`, `config_fixture` | reading `config.json` | validation, from a dict or a fixture file |
| `running_modes` | a started session | switching and reload |
| `Display` | the hud process | which frames crossed a **real** unix socket |
| `WedgedSocket` / `BrokenSocket` | a display that stopped reading, or whose write fails | that publishing does no I/O, and that such a display is dropped |

`socket_path` exists because `AF_UNIX` takes 107 bytes and `tmp_path` is named after the test.
`hud_socket` defaults to `None`, meaning no socket at all, so nothing binds in a suite that is not
about the display. Injection points beyond the fixtures: `ModeManager(stream_factory=, indicator=,
settings_loader=)`, `InputExecutor(backend, mode_switcher=, sleep=)`, `TransformerStream(transcriber=)`,
`FaceRotationTracker(retry_delays=)`, `Application(backend=, stream_factory=, hud_socket=)` and
`HudService(path, max_clients=, write_timeout=)`.

The `↓` and `↑` notation for press and release is borrowed from `specs/`, so a scenario and its test
read alike.

## Layouts without a toolkit

`overlay/hud/window.py` cannot be imported here (gap 7), so a layout MUST be checked by reading what
it produced rather than by reasoning about it. `--dump` is the routine rather than the fallback,
because a threshold settled in advance is often wrong on the first inspection of its rows.

```bash
uv run libre-dictum & uv run libre-dictum-hud --dump   # the drawn content, as text
uv run libre-dictum-hud --probe                        # what the compositor gave the surfaces
```

## Writing a test

**A new test MUST be sensitive**: break the code it guards, watch it fail, then restore. A guard
that hangs instead of failing MUST be rewritten to fail. A scripted mutation sweep (patch,
`pytest -x -q`, restore) applies the same check to the whole suite, and reports a test that passes
for the wrong reason or a guard that no test reaches.

- Classes group by the rule they exercise, and a name describes the behaviour
  (`test_a_held_expression_fires_only_once`, not `test_update_2`).
- A test that pins a fixed defect says so, so that the behaviour is not simplified back out.
- Nothing sleeps, and nothing writes outside `tmp_path`.
- Anything needing a real device gets `@pytest.mark.hardware`. Nothing carries it today.
- Difficulty in writing a test usually indicates that the behaviour belongs one layer up.

`tests/documentation/` enforces the following, in both directions.

- Every key the loader reads is documented, and every documented key is read.
- Every verb and placeholder appears in `response-dsl.md`, and the quoted defaults are real.
- Every complete JSON block in the reference loads.
- Every relative link resolves, and `docs/README.md` lists every page.
- Every module the layering table calls pure is on `mypy`'s strict list.

A new key that turns one of these red MUST be documented rather than exempted.
