# Known gaps

`specs/` claims every scenario is implemented. The defects below are not, and all were reproduced
against the tree. A scenario MUST NOT be trusted as a description of current behaviour until it has
been checked against this page.

**1. The mode and executor locks are taken in both orders, which permits a permanent deadlock.**
`ModeManager.switch` holds modes and then runs `exit_command` through the executor, while
`execute()` holds the executor and then runs `mode(...)` through `switch`. Two threads, no timeout,
both daemons, so the tray still shows a mode and the watchdog still logs that all subsystems are
running while voice and gestures no longer respond, with no hands-free recovery. Panic cannot help,
because it needs the executor lock. The window is the `input_delay` sleeps, widened arbitrarily by
`exec()` in an enter or exit command. The fix is for `_verb_mode` to hand the switch off instead of
performing it under the lock.

**2. `exec()` and `script()` block recognition and the panic phrase.** `subprocess.run()` is called
synchronously inside `execute()`. A measurement showed `panic()` returning after 2.8 s for a 3 s
child, and with the documented `exec(firefox)` it does not return until the browser closes.
`scripts-and-processes.feature` forbids this in `@accessibility-critical` scenarios; the tests miss
it because they monkeypatch `subprocess.run` with a lambda. The fix is `Popen` plus a reaper thread
that logs the exit status, which also closes gap 3.

**3. `exec()`'s exit status is neither logged nor checked.**

**4. `script()` targets are not validated at load.** A missing module, or one with no `script`
function, raises on the recognizer thread the first time the command is spoken. The defect is
small, and the promise it breaks is central to the tinkerer persona.

**5. The loader validates meaning, not JSON types.** Six of nine malformed values escape as raw
exceptions: `"icon": 5` raises `TypeError`, `"modes": {"m": "oops"}` raises `AttributeError`, and
`"input_delay": "fast"` raises `ValueError`. Startup then reports a traceback instead of a message.
Reload is worse: `ModeManager.reload` catches only `ConfigError`, so the exception escapes before
`_faults["configuration"]` is set, and the session survives while **the tray never shows the
fault**. The fix is typed accessors raising `ConfigError` with the mode and key.

**6. Tracker construction is unguarded.** `_check_gesture_definitions` rejects a malformed
`ht_custom_gestures` at load, feature names included, with the nearest match suggested. What remains
is that `_create_tracker` wraps only the *import* of `FaceRotationTracker` in `try`, not its
construction.

**7. Nothing in the suite tests the code that draws the overlay.** The environment the suite runs in
has no `gi` or GTK4, so `overlay/hud/window.py` cannot be imported by a test. Running it by hand
found a defect no test here could: a layer-shell shim loaded *after* `gi.repository.Gtk` still
imports while every layer-shell call is silently ignored, which is why `window.py` performs
`ctypes.CDLL(..., RTLD_GLOBAL)` above `import gi` and then asks whether that worked. Four
behaviours remain guessed. The first is `set_input_region(cairo.Region())` for click-through,
re-applied on every `present()` because a re-shown surface is a new one. The second is whether a
26-row group fits without scrolling, and the third is whether a layer-shell margin reset once a
frame reads as one movement. The fourth is whether `set_monitor` remaps a surface already on screen,
which is what confining the surfaces to one output rests on. An
X11 display and a Python that has GTK4 drive everything around those four, `--outputs` included, and
offer no layer shell to exercise them with. `--probe` is the substitute. Unexercised for the same
reason: both `*_stream._process` calls to `_note_partial`, which need PortAudio, and the `EV_ABS`
tablet, which needs `/dev/uinput`.

## Smaller defects

- Repeated modifier taps stack duplicates in `modifiers_held`.
- `_starting_mode` is annotated `-> str`, so `AppSettings.starting_mode`'s `None` branch is dead.
- A response-only edit rebuilds a VOSK recognizer, reloading the model, although the grammar depends
  on the patterns alone.
- `enter_command` and `exit_command` belong to the voice layer alone (decision 17), so a pedal
  layout whose entry should open the sheet has no way to say so.
- **A `starting_gesture_mode` or `starting_pedal_mode` pointing at a template fails silently.** A
  bare spoken mode name moves all three layers, so the pin is abandoned by the most ordinary action
  available. The working pattern is a template that every mode imports. There is no load warning and
  no note in the reference.
- Nothing checks a `pedal.buttons` offset against the board. A wrong offset binds the wrong pedal
  silently, and the pedals page is the only place that is visible.
- A gesture held across a reload leaks its button. This self-heals on the next gesture.
- Ordering between two tap runs is unobservable, so bind `left left` or `left left left`, not both.
- `FaceRotationTracker._run`'s attempt budget is one `for` loop over the thread's whole life, so a
  camera that streams for nine minutes and then drops spends an attempt, and the fifth gives up.
