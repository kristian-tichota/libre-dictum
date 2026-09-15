# Architecture

`main.py` parses the arguments. `app.py` builds the object graph and owns the watchdog.

```
settings → devices → executor → modes → dispatcher
                             ├→ head tracking → pointer
                             ├→ pedals
                             └→ health → tray
                                      └→ socket ─ ─→ libre-dictum-hud (second process)
```

Three mechanisms reach the executor: voice through `dispatch`, gestures through the camera thread,
and pedals through the HID thread. Each keeps its own pointer into the single `modes` table
(`layers.py`).

## The layering rule

**Imports point one way.** Nothing in the language enforces this, and it is what makes the pure
layer testable without hardware.

| Layer | Modules | May import |
|---|---|---|
| **Pure logic** | `text`, `pattern`, `mathutil`, `pointer`, `smoothing`, `layers`, `input/dsl`, `input/keymap`, `audio/vad`, `audio/grammar`, `audio/partials`, `tracking/gestures`, `tracking/hands`, `tracking/takes`, `tracking/tuning`, `tracking/expressions`, `tracking/face_tuning`, `tracking/screenmap`, `tracking/cameras`, `pedals/reader`, `overlay/model`, `overlay/protocol`, `health`, `status`, `meters`, `errors` | stdlib, numpy, `errors`, `settings`' frozen dataclasses — plus `evdev.ecodes` in `keymap`, a constant table and the one exception |
| **Typed config** | `settings` | the pure layer |
| **Adapters** | `input/devices`, `audio/*_stream`, `audio/base`, `audio/transcriber`, `tracking/{tracker,probe,capture,face_capture,screen_capture,v4l2}`, `pedals/device`, `systray`, `overlay/service`, `instance` | the pure layer, `settings` |
| **Coordination** | `input/executor`, `modes`, `dispatch`, `streams` | everything above |
| **Wiring** | `app`, `main` | everything |
| **Another program** | `overlay/hud/*` | `overlay/protocol`, `errors`, `meters`, stdlib. **Pure only** — a display process is not in the `input` group |

The pure layer is the first two rows plus `overlay/hud/{render,style,motion,compositor}`. `mypy`'s
strict list MUST cover the Pure logic row, and a test enforces that. `window.py` is the only file
permitted to import a toolkit.

Four seams keep `pytest` free of hardware, models and sound libraries, and each of them concerns
import cost. `streams.build_stream` imports VOSK and torch inside the function. `audio/`,
`tracking/` and `pedals/` hand out their impure classes through a module `__getattr__`, so a missing
dependency surfaces where it can be caught. `UinputBackend.open()` opens devices there and never at
import time. `overlay/protocol` takes `overlay/model` for annotations only, because resolving it at
run time would pull in `evdev`, and the display process need not be in the `input` group.

## Placement of a change

| Changing | Module | Spec |
|---|---|---|
| What an utterance matches | `pattern.py` | `commands/` |
| What a response does | `input/dsl.py` (parse) + `input/executor.py` (run) | `input/` |
| A config key | `settings.py` | `configuration/` — **and `reference/configuration.md`, enforced** |
| Which command wins | `dispatch.py` | `commands/dispatch-order.feature` |
| Modes, layers, sleep, reload | `modes.py`, `layers.py` | `lifecycle/` |
| Pointer feel / where the head points | `pointer.py`, `smoothing.py`, `mathutil.py`, `tracking/screenmap.py` | `head-tracking/` |
| Gesture edges, hand scores | `tracking/gestures.py`, `tracking/hands.py` | `head-tracking/` |
| Pedal edges | `pedals/reader.py` | `input/pedals.feature` |
| What a display is told | `overlay/{model,protocol,service}.py` | `discoverability/` |
| What a surface *says* / looks like / is placed as | `overlay/hud/{render,style,motion,compositor}.py` | `discoverability/` |
| How it is *drawn* | `overlay/hud/window.py` | untested; gap 7 |

`tests/` mirrors `specs/`. **Prefer the pure layer.** The same behaviour placed in `pattern.py`
receives a fixture-free test, whereas in `vosk_stream.py` it would require a model and therefore
receives no test at all.
