# Configuration reference

The configuration file is `~/.config/libre-dictum/config.json`. Everything in it is validated at
load, and every error names the mode, and the command where one applies. Without the file the
program reports what is missing and exits non-zero.

```json
{
  "starting_mode": "command mode",
  "enable_systray": true,
  "modes": {
    "command mode": {
      "type": "vosk",
      "path": "models/vosk-model-small-en-us-0.15",
      "icon": [0, 255, 0],
      "commands": {
        "open terminal": "alt + enter",
        "press {any}": "{1}",
        "{numeric} step down": "{1}(down)"
      }
    },
    "dictate mode": {
      "type": "transformer",
      "model_name": "whisper-turbo",
      "commands": { "{rest}": "type({1})" }
    }
  }
}
```

A response is written in [its own language](response-dsl.md). Any setting that has to be measured
rather than chosen has an ordered procedure in [Calibration order](calibration.md).

## Global

| Key | Type | Default | Meaning |
|---|---|---|---|
| `modes` | object | `{}` | Mode name → mode. At least one must be runnable. |
| `include` | list of paths/globs | `[]` | Other files of this configuration. `config.json` only. See [Several files](#several-files). |
| `starting_mode` | string | first runnable, in file order | Unknown name or a template is an error. |
| `reload_command` | string | `"reload config"` | Re-reads and applies this file. |
| `panic_command` | string or `null` | `"release everything"` | Releases every held key and the saved value, from any mode. Setting it to `null` removes the only hands-free recovery from a stuck modifier. |
| `previous_mode_keyword` | string or `null` | `"previous mode"` | Returns to the previously active mode. |
| `sleep_confirm_seconds` | number | `5` | How long a `sleep()` or `wake()` waits to be repeated. This is the entire misfire guard; see [Sleep](#sleep). |
| `enable_systray` | boolean | `false` | Mode colour, a glyph per held modifier, a mark on fault. Needs the `system-tray` extra. |
| `enable_head_tracking` | boolean | `false` | Pointer and gestures from a webcam. Needs `head-tracking`. |
| `enable_pedals` | boolean | `false` | USB HID pedal board. Needs `pedals`. |
| `pedal` | object | Stream Deck Pedal's numbers | Which device, and which report byte is which pedal. See [Pedals](#pedals). |
| `enable_control` | boolean | `false` | Run the named commands other programs send. See [Control](#control). |
| `control` | object | `{}` | The names another program may send, and what each runs. |
| `starting_gesture_mode` | string or `null` | follows `starting_mode` | Where the **gesture layer** starts. May name a template, subject to the constraint in [Input layers](#input-layers). |
| `starting_pedal_mode` | string or `null` | follows `starting_mode` | The same for the pedal layer. |
| `overlay` | object | see [Display](#the-on-screen-display) | Phrases that show, hide and navigate the surfaces. |

**Dispatch order.** Panic, then reload, then the sleep gate, then the previous-mode phrase, then any
mode name, then the display's phrases, then `banned_strings`, then `commands`. Mode names act as
switch phrases from *every* mode, which is what keeps every mode reachable by voice. The accepted
cost is that a dictation mode cannot type its own mode names. Everything above the command table is
reserved, so a command or mode using the same phrase is rejected at load.

## Several files

`config.json` may list others in `include`; the set becomes one configuration. Only `config.json`
may `include`. A glob expands in sorted order. An entry matching nothing is an error, because a mistyped
directory would otherwise remove a hundred commands from the session silently. **Nothing merges
across files**: a mode or setting defined twice is an error naming both. Templates are the mechanism
for sharing, and they work across files. Worked example:
[`examples/configs/split/`](../../examples/configs/split).

## Input layers

There are three independent mechanisms, namely voice, gestures and pedals, each with its own pointer
into the single `modes` table. Only voice needs a recognizer, which is why moving either of the
others costs nothing.

| Written | Moves |
|---|---|
| `mode(mouse mode)` | **all three.** Must name a runnable mode |
| `mode(voice:mouse mode)` | voice only. Must name a runnable mode |
| `mode(gesture:x)` / `mode(pedal:x)` | that layer only. **May name an import template** |
| `mode(pedal:previous mode)` | that layer, back where it came from |
| `mode(pedal:voice)` | that layer, onto the voice layer's mode, unless a mode is named `voice` |

- A bare spoken mode name, the previous-mode phrase, and an unqualified `mode()` all move all three.
  Each layer returns to *its own* previous mode.
- `enter_command` and `exit_command` belong to the **voice** layer alone. Otherwise a mode that all
  three layers entered would run them three times.
- **The voice layer is the only one that can refuse.** A switch into a mode whose recognizer never
  started moves nothing, because a partial switch is worse than none and a mode that cannot hear
  could not be left by voice.
- A layer whose subsystem is off is drawn nowhere.
- **A layer MUST NOT be pinned with `starting_*_mode` in order to make bindings apply everywhere.**
  A bare spoken mode name moves all three layers, so the pin is abandoned by the most ordinary
  action available. Place the bindings in a template that every mode *imports*.

## Sleep

Sleep is a fourth state beside each layer's mode pointer. The events of a sleeping mechanism are
dropped before any binding is looked up, and its pointer does not move, so waking restores the
previous mode.

| Written | Means |
|---|---|
| `sleep()` / `wake()` | all three, and the pointer with them |
| `sleep(gesture)` | one — `voice`, `gesture` or `pedal` |
| `sleep(gesture, pedal)` | two |

- **Nothing happens on the first request.** A request *arms*; only the **same** request inside
  `sleep_confirm_seconds` applies it. A different one re-arms rather than confirming. Either half may
  come from any mechanism, so arming by gesture and confirming by pedal is allowed.
- **Both are deterministic rather than a toggle**, so an accidental repeat has no effect. Bind
  `sleep` and `wake` side by side. Each is inert in the other's state, which allows the wake to be
  deliberately harder to perform.
- **The mechanism that confirms a sleep owns it**, and a wake from any other mechanism is refused,
  which is what makes a deliberately harder wake worth configuring. A refusal is stated on the chip
  rather than only logged.
- **A successful reload wakes every mechanism** and is subject to no ownership check, which is the
  recovery from a sleep whose wake binding was edited away or whose mechanism was disconnected.
  Panic deliberately does *not* wake.
- While asleep, a mechanism runs `sleep`, `wake` and `hud` and nothing else. A `+` chain requires
  every token to be permitted, so `"wake() + type(hello)"` is dropped whole. Voice additionally
  keeps panic and reload. Mode names are refused.
- **Going to sleep releases every held key** and drops every captured release. Otherwise a gesture
  holding a button as sleep begins would hold it indefinitely.
- The pointer parks only when **all three** are asleep.
- No recognizer is rebuilt. A wake phrase alone in a closed grammar would match a large share of
  what it hears, because the competing phrases are what make VOSK accurate.
- Sleep does not survive a restart.
- On screen the mode dot dims, and one italic line states what is asleep, which mechanism may wake
  it, any refusal and any pending confirmation. The fault style is deliberately not used, because a
  mechanism placed beyond reach on purpose has not failed.

## Pedals

One device, configured globally. What each pedal *does* is configured per mode.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `vendor_id` | int or `"0x…"` | `0x0fd9` | JSON has no hex literal, hence the string form. |
| `product_id` | int or `"0x…"` | `0x0086` | |
| `buttons` | object | `{"left": 4, "middle": 5, "right": 6}` | Pedal name to report byte, counted from zero. The names are arbitrary. |
| `report_length` | integer | `8` | An offset past the end is a load error. |
| `read_timeout_ms` | integer | `10` | Bounds how long the thread waits, so that it notices a shutdown and a motionless pedal is not read as a wedged device. |
| `tap_ms` | integer | `500` | Longest press that still counts as a **tap**. |
| `sequence_ms` | integer | `1000` | Longest gap between two taps of a run. |

The device is retried five times, at 1, 2, 4, 8 and 16 s, then given up on with an error, and the
reload phrase starts it again. **A pedal still down when the device failed is released.** The hidraw
node requires a udev rule. Without one, the log names both possible causes and voice control
continues to run.

### Per mode

| Form | Meaning |
|---|---|
| `"left": "left_mouse"` | run on **press** |
| `"left": {"press": …, "release": …}` | either may be omitted |
| `"left": null` | deliberately nothing, which withdraws a single binding inherited from a template |

Both edges exist because a pedal that fired only on press could not sustain a state, which is what a
foot does better than a voice. `hold(left_mouse)` with `release(left_mouse)` is a drag, and
`mode(dictate mode)` with `mode(voice:previous mode)` dictates only while the pedal is down.

Binding a pedal the board has not got is a load error listing the ones it has.

### Runs of bare taps

A key naming **several pedals separated by spaces** is a run of taps: `"left left": "sleep()"`.

A **tap** is a press shorter than `tap_ms` during which *nothing was typed*, and each half of that
definition closes a different hole. Without the second half, `left left` would fire on ctrl+c
followed quickly by ctrl+v. Without the first, a modifier held in order to zoom by scrolling would
be indistinguishable from a tap, because the mouse belongs to another process and that press
reaches this one as no key events at all. Only *non-modifier* events count as typing, and **a bare
modifier tap changes nothing in any application**, which is what allows a run of taps to act as a
toggle on a pedal that is also bound to `hold(ctrl)`.

The taps still perform their own bindings, and nothing is suppressed or delayed. The run is
forgotten once one has matched, so a **shorter run always wins over a longer one**; bind one or the
other, not both. A release the device still owes completes nothing.

## Control

Other programs run named commands over a unix socket that only this user can open:
`$XDG_RUNTIME_DIR/libre-dictum/control.sock`, or `$LIBRE_DICTUM_CONTROL_SOCKET`. Only the names in
`control.commands` run. A command runs while a mechanism sleeps, because no mechanism sent it.

```json
"enable_control": true,
"control": {
  "commands": {
    "break started": "mode(pedal:break pedals)",
    "break over": "mode(pedal:voice)"
  }
}
```

| Key | Default | Meaning |
|---|---|---|
| `commands` | `{}` | Name to response. A name is normalized as an utterance is; two names that normalize alike are an error. |

`libre-dictum-send break started` sends one, joining its words. It exits 0 when the command ran, 1
when it was refused and 2 when nothing answered; `python -m libre_dictum.control.send` is the same
program. A request is one UTF-8 line, and its reply is `ok` or `error:` with the reason.

## Modes

| Key | Type | Default | Meaning |
|---|---|---|---|
| `type` | `"vosk"` / `"transformer"` / `"import"` | `"import"` | An absent type makes the mode an import template, which never listens, although a gesture or pedal layer may rest on it. |
| `imports` | list | `[]` | Templates to merge in. |
| `commands` | object | `{}` | Spoken pattern to response. |
| `aliases` | object | `{}` | Regular expression to replacement, applied to a response before anything else. |
| `banned_strings` | list | `[]` | Utterances to recognize and deliberately drop. |
| `gestures` | object | `{}` | Gesture name to response, applied while the **gesture layer** is on this mode. |
| `pedals` | object | `{}` | Pedal name to response, applied while the **pedal layer** is on this mode. |
| `icon` | `[r,g,b]` | none | Tray colour, three ints 0–255. |
| `input_delay` | number | `0.01` | Seconds between key events. Raise it for applications that drop fast input. |
| `type_delay` | number | `0.002` | Seconds between characters of a `type()`. It is much smaller, because a sentence is hundreds of events and all of them hold the executor lock that the panic phrase needs. |
| `enter_command` / `exit_command` | response | none | Run when the **voice** layer enters or leaves the mode. |

**VOSK** (`"type": "vosk"`):

| Key | Default | Meaning |
|---|---|---|
| `path` | required | The model directory. Relative paths are looked for under the working directory, then beside `config.json`. |

Its grammar is **closed**, and is built from the mode's own patterns plus every mode name and
reserved phrase, which is what makes a small model accurate enough to drive a keyboard. There are
two consequences. `{rest}` is rejected at load, and `{numeric}` expands to one phrase per digit
word, so it matches **single digits** and multiplies the vocabulary by ten; more than 500 generated
phrases is reported with the count. Commands fire from partial results.

**Dictation** (`"type": "transformer"`):

| Key | Default | Meaning |
|---|---|---|
| `model_name` | required | A name of the form `whisper-<name>` loads openai-whisper, so `whisper-turbo` selects `turbo`. Anything else is a HuggingFace identifier. |
| `transformer_device` | `"auto"` | `"auto"` delegates placement to accelerate. Any other value is used as given. |
| `lang` | `"en"` | Used where the model accepts a language. |
| `silence_seconds` | `0.3` | Silence that ends an utterance. |
| `max_chunk_seconds` | `30.0` | Longest chunk in one go. |
| `energy_threshold` | `0.01` | RMS above which a block is speech. |
| `pre_roll_seconds` | `0.25` | Audio retained from before the threshold was crossed, so the first syllable survives. |

**Templates.** A template is a mode with no `type`, named `"::something"` by convention. Objects
merge recursively, lists extend, and the importing mode wins for any scalar it defines itself.
`icon` is seeded but never merged, and `type` and `imports` are never copied. Two keys exist only in
a template:

| Key | Meaning |
|---|---|
| `vosk` | Hoisted into an importing mode of type `"vosk"`. |
| `transformer` | Hoisted into an importing mode of type `"transformer"`. |

Each is hoisted only into a mode of its own type, which is how one template configures both:

```json
"::models": {
  "vosk": { "path": "models/vosk-model-small-en-us-0.15" },
  "transformer": { "model_name": "whisper-turbo", "transformer_device": "cuda" }
}
```

## Head tracking

There is one global switch and one camera. Every setting below is global with a per-mode override.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ht_model_path` | string | required when enabled | mediapipe's `face_landmarker.task`. |
| `camera` | string / int / `null` | `null` | A name, a device path or an index. `null` selects one automatically. See below. |
| `camera_index` | integer | — | Deprecated. `camera` accepts the same number, and setting both is an error. |
| `ht_min_detection_confidence` | number | `0.5` | Mediapipe face detection. |
| `ht_min_tracking_confidence` | number | `0.5` | Mediapipe tracking. |
| `ht_offset_x`, `ht_offset_y` | number | `0.0` | The *initial* neutral position, in degrees. `recenter()` moves it from there. |
| `ht_filter_min_cutoff` | number | `1.0` | The One-Euro cutoff in Hz while the head is still. A lower value removes more jitter. |
| `ht_filter_beta` | number | `0.05` | How quickly the filter widens on movement. A higher value gives less lag on a deliberate turn. |
| `ht_auto_recenter_seconds` | number or `null` | `null` | Period over which neutral drifts toward the current resting pose. |
| `ht_custom_gestures` | object | the shipped set | Replaces the defaults entirely. See [Gestures](#gestures). |
| `ht_screen_calibration` | object | `{}` | Required by `"ht_pointer_law": "absolute"`. See [Screen calibration](#screen-calibration). |
| `ht_screen_bounds` | object | whole desktop | Only with **more than one monitor**. See [Two monitors](#two-monitors). |
| `ht_face_baseline` | object | `{}` | Resting blendshape levels of the face in front of the camera. See [Face baseline](#face-baseline). |
| `ht_debug_gestures` | list | `[]` | Print live scores for these gestures, or for **bare feature names**, which is the only way to observe a score before its gesture exists. An unknown name is a load error, because a misspelling there would print nothing. |
| `ht_capture_fourcc` | string or `null` | `"MJPG"` | `null` leaves the driver alone. |
| `ht_capture_width`, `ht_capture_height`, `ht_capture_fps` | int or `null` | `1920`, `1080`, `60` | Some webcams reject these outright. A silent downgrade is logged as a warning naming both formats. |

### Hands

Hand tracking is a second model on the *same* camera and frame, and is off by default. It does not
affect the pointer. It converts 21 landmarks into named scores, so that a hand shape becomes an
ordinary gesture condition.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ht_hand_enabled` | boolean | `false` | Requires `enable_head_tracking`, because it shares the camera. |
| `ht_hand_model_path` | string | required when enabled | `hand_landmarker.task`, which is a **different file** from the face model. |
| `ht_hand_max_hands` | integer | `2` | Two hands cost the same as one, because the cost is the palm detector. |
| `ht_hand_stride` | integer | `4` | Run the hand model on every *n*th frame. |
| `ht_hand_min_detection_confidence` | number | `0.5` | |
| `ht_hand_min_presence_confidence` | number | `0.5` | Confidence that a tracked hand is still in frame. |
| `ht_hand_min_tracking_confidence` | number | `0.5` | |
| `ht_hand_calibration` | object | see [Calibration](#calibration) | Where the 0 and 1 of each score sit. **This is the first thing to suspect when a shape never fires.** |

**Purpose of the stride.** The hand model costs about 11.5 ms against the face model's 1.8 ms,
within a 60 fps budget of 16.7 ms. Measured on a six-core desktop processor at 640×480, as median
and worst frame in the loop: face only, 1.8 / 3.4; stride 1, 12.0 / 16.4; stride 2, 6.5 / 12.9;
**stride 4, 2.1 / 14.2, giving hands at 15 Hz**; stride 8, 1.9 / 13.9. At the default the typical
frame is untouched and the strided frame still fits the budget. A rate of 15 Hz is indistinguishable
from immediate for a shape held 200 to 400 ms. Under load the median does not move and only the
strided frame does, so a transcription burst costs gesture *latency*, roughly 67 ms rising to 90 ms,
rather than pointer precision.

Between strided frames **the last scores stand**, so a gesture mixing hand and face conditions, and
its `hold`, see a steady picture. The pointer is unaffected, because speed is expressed per second
and a longer frame interval therefore moves it the same distance. A hand model that fails costs hand
scores only, and is reported separately.

### Which camera

`/dev/videoN` numbers move between boots, and a single UVC webcam claims two of them, carrying
frames on one and per-frame metadata on the other under the same card name. The metadata node
**fails silently**: it opens, reports success, and delivers no frame.

`camera` accepts four forms. Nothing: every node is queried, metadata nodes are dropped, and the
first node that delivers a frame is used. **A name**: matched case-insensitively as a substring of
the card name or bus identifier. A substring is required because the kernel truncates card names to
31 characters, and the bus identifier names a *port*, which is how two identical cameras are
distinguished. **A path**: `/dev/v4l/by-id/…` is built from the device's own vendor, product and
serial, and therefore survives a reboot. **An index**: treated as a pin, and landing on a metadata
node is an error that names the key to remove.

`libre-dictum --cameras` prints every camera it can see and the `camera` value that pins each one.
It runs alongside a live session and opens nothing. A failure is retried five times, at 1, 2, 4, 8
and 16 s, enumerating again each time, after which the reload phrase is the recovery.

### Pointer control (per mode)

| Key | Type | Default | Meaning |
|---|---|---|---|
| `ht_enabled` | boolean | `false` | Read from the **voice** layer's mode. Gestures still fire while it is off. |
| `ht_pointer_law` | `"rate"` / `"absolute"` | `"rate"` | See [Two control laws](#two-control-laws). |
| `ht_dead_angle_h`, `ht_dead_angle_v` | number | `2.0` | Degrees ignored around neutral. The two values are the radii of an **ellipse**, not of a rectangle. |
| `ht_full_speed_angle` | number | `18.0` | Angle at which top speed is reached. It MUST lie outside the dead angle. |
| `ht_max_speed_px_per_sec` | number | `800.0` | The top speed itself, per **second**. |
| `ht_speed_power` | number | `1.5` | A value above 1 gives slow movement near the dead zone and faster movement further out. |
| `ht_invert_x`, `ht_invert_y` | boolean | `false` | |

Every setting except `ht_enabled`, `ht_pointer_law` and the two inversions describes the **rate**
law and is ignored under `absolute`.

```
speed = ht_max_speed_px_per_sec · ((angle − dead) / (ht_full_speed_angle − dead)) ^ ht_speed_power
```

The result is clamped, and `dead` is the ellipse edge in the direction the head turned, so the
direction is carried through exactly and a diagonal is no faster than an axis. At the defaults the
speed is 2 px/s at 2.5°, 31 at 4°, 277 at 10°, and 800 from 18° onward.

`ht_speed_mult` and `ht_max_speed` **no longer exist**. They were expressed in pixels per *frame*,
so the pointer slowed whenever the frame rate did, and lowering `ht_speed_mult` widened the
effective dead angle to 5.7° for a configured 2.2°. The loader rejects both keys and prints the
converted value.

#### Two control laws

The **rate** law requires the head to be held off neutral, so head and gaze disagree for the whole
journey. The **absolute** law places the pointer where the head points and asks for *less* neck
movement, but it **shimmers by several pixels**, because a head at rest wanders by a fraction of a
degree through breathing rather than through sensor noise. Configure one mode of each: absolute to
travel, and a slow rate mode for the last few pixels. See
[absolute pointing](absolute-pointing.md).

```json
"ht_pointer_law": "absolute",
"modes": {
  "mouse mode": { "ht_enabled": true },
  "precision mode": {
    "ht_enabled": true, "ht_pointer_law": "rate",
    "ht_max_speed_px_per_sec": 90, "ht_full_speed_angle": 12,
    "ht_dead_angle_h": 0.8, "ht_dead_angle_v": 0.8
  }
}
```

The absolute law emits through a **second uinput device** carrying absolute axes, because relative
motion passes through libinput's pointer acceleration, which would amplify a fast sweep into an
overshoot. `recenter()` and `ht_auto_recenter_seconds` have no effect under it, and setting the
latter beside an absolute mode is a load error, because both move neutral and a screen does not
drift.

### Screen calibration

Produced by `libre-dictum --calibrate-screen`, which is the form to prefer; see
[Calibration order](calibration.md) for where this step sits. The alternative is three measured
dimensions, which assume the screen is square on to the resting head and centred on it, and a tape
measure cannot verify either assumption:

```json
"ht_screen_calibration": { "width_mm": 597, "height_mm": 336, "distance_mm": 600 }
```

The fitted block uses nine dots by default, where `4` requests 4×4 and 3 to 6 a side are accepted.
Each dot is aimed at with the **nose** and held still. The procedure requires `libre-dictum-hud` to
draw the dots and refuses to run without it, and it cannot share a camera with a live session,
although it claims no session and emits no input.

```json
"ht_screen_calibration": {
  "x": [-1.004, 0.0, 0.5], "y": [0.0, 1.785, 0.5],
  "perspective": [0.0, 0.0], "residual": 0.004, "samples": 9,
  "origin": [0.0, 0.0, 0.0]
}
```

The block is a homography in `(tan yaw, tan pitch / cos yaw)`, where a `perspective` of `[0, 0]`
means the screen is square on. **The signs are not the intuitive ones.** `+yaw` is a head turned
toward the screen's *left* and `+pitch` is *down*, so a correct `x` has a negative first term and a
correct `y` a positive second term. The report prints two figures, and **the second is the one to
judge by**: the residual is the error on the dots the mapping was fitted to, whereas the expected
error applies anywhere else, and the two move in opposite directions as dots are added.

#### Two monitors

`--calibrate-screen` draws on **one** output, whereas the tablet device spans the **whole desktop**.
With one monitor those are the same rectangle. With two they are not, and the mapping is then wrong
*silently*, because every number in the chain remains a valid fraction of something.

```json
"ht_screen_bounds": {
  "x": 1920, "y": 0, "width": 1920, "height": 1080,
  "desktop_width": 3840, "desktop_height": 1080
}
```

The first four values describe the calibrated monitor as the compositor positions it, and the last
two describe every monitor together. All six are in pixels, and are reported by `wlr-randr`,
`hyprctl monitors`, `swaymsg -t get_outputs` or `xrandr --listmonitors`. Omit the key with a single
monitor. **The pointer is confined to the calibrated monitor**, because the mapping is one flat
plane. Use a rate-law mode to cross between monitors.

## Gestures

A gesture is a set of conditions on named scores: mediapipe's 52 blendshapes, plus
[hand scores](#hand-scores) when hand tracking is on. It fires once when every condition holds, and
re-arms when any feature crosses its `release`.

```json
"ht_custom_gestures": {
  "left_wink": {
    "hold": 250,
    "eyeBlinkLeft":  { "min": 0.6, "release": 0.3 },
    "eyeBlinkRight": { "max": 0.2, "release": 0.3 }
  }
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `hold` | number | `0` | Milliseconds for which the conditions MUST hold **continuously**. |
| *feature name* | object | — | Accepts `min`, `max` and `release`. At least one of `min` and `max` is required. |

**`hold` is what prevents a gesture firing on the way *into* another one.** Every gesture that
matches a frame fires, and no expression is perfectly symmetric, so an expression ramping up passes
through a state in which one side has moved and the other has not. A `hold` of 200 to 300 ms closes
that class of misfire. It is also the *only* mechanism that keeps a gesture out of ordinary
activity: measured over five distractor passes, every feature sits near zero for sustained
stretches, so **a `max` provides distinctness rather than safety**.

**The more specific gesture wins.** A gesture requiring every condition of another plus at least one
more suppresses that other, and a narrower gesture already held is *released* rather than left
stranded. Condition **names** decide this, never thresholds, and only an *active* gesture suppresses,
never one still serving its `hold`. That is what allows both hands to be used at once: write the
one-handed shapes against one hand and let the two-handed shape be the wider one.

**A release runs what its press recorded.** A hold outlives the mode it started in, because an
unqualified `mode()` moves all three layers, so a release looked up afresh would find a mode that
does not bind the shape and would leave `hold(left_mouse)` down until the panic phrase. A shape
pressed where nothing bound it stays silent on release, and a successful reload drops every recorded
release.

**A feature that no frame reported is absent rather than zero**, so the gesture is *released* rather
than evaluated. Read as zero, a gesture built from `max` conditions would begin to hold the moment
its sensor stopped reporting, fire, and never release.

**Names and shapes are checked at load**, with the nearest match suggested, because a misspelled
feature would score zero permanently, which is indistinguishable from a failed camera. A hand
feature while `ht_hand_enabled` is off is a warning rather than an error, because one configuration
is carried between machines.

The shipped set is facial only, for the reason given under [Calibration](#calibration).

### Per edge

| Form | Meaning |
|---|---|
| `"fist": "left_mouse"` | run when the gesture becomes **active** |
| `"fist": {"press": …, "release": …}` | either may be omitted |
| `"fist": null` | deliberately nothing |

The release edge is what makes a held button safe. A fist pressing `hold(left_mouse)` is a drag, and
that drag ends when the hand opens, when the hand leaves the frame, or when the hand model fails. A
click and a drag are then different shapes rather than different mechanisms. An unbound edge is
silent and produces no warning.

The phrase `overlay gestures` draws the table. It is the only place a gesture bound to a key or a
button is visible, because the modes page shows only the gestures that switch modes.

## Hand scores

Each score runs from 0 to 1 and is one-sided, in the way mediapipe splits `eyeLookUpLeft` from
`eyeLookDownLeft`.

| Score | Meaning |
|---|---|
| `handPresent`, `bothHandsPresent` | What is in frame. |
| `handIsLeft`, `handIsRight` | Which hand the unprefixed scores describe. |
| `handThumbCurl` … `handLittleCurl` | 0 straight, 1 folded as far as it goes. |
| `handFist`, `handOpen` | Every finger folded / straight. |
| `handPinchIndex`, `handPinchMiddle` | 1 is touching. |
| `handSpread` | Fingers splayed. |
| `handPalmVisible` | **Unsigned** — it cannot tell a palm from the back of a hand. |
| `handHigh`, `handNear` | **Where** the hand is: wrist height in the frame, and proximity inferred from apparent size. |
| `handPointing{Up,Down,Left,Right}` | Index direction. |
| `handThumbPointing{Up,Down,Left,Right}` | Thumb direction, which is the half of a thumbs-up that states the orientation. |

- **`handHigh` and `handNear` are deliberately not shape scores.** Every other score is a ratio of
  the hand against itself and therefore reads the same wherever the hand is, but nothing about a
  hand's *shape* separates one held up deliberately from the same hand resting in the frame, because
  an idle open palm scores `handOpen` at 1.00 for over a second. The cost is that both scores change
  if the camera is moved or the seating position changes.
- **Every shape score also exists per hand**, with the side first: `leftHandFist` and
  `rightHandPointingUp`. A suffix would spell the second of those `handPointingUpRight`, which is
  ambiguous.
- **The unprefixed scores describe one hand**, the *primary* one, which retains that role from frame
  to frame while it remains visible and otherwise passes it to the nearer hand. A maximum across
  both hands would report a shape that neither hand made.
- **With one hand raised, the other hand's scores read zero rather than being absent.** No hand at
  all reports nothing, but once *any* hand is visible the model has looked. That is what makes an
  exclusive pair writable, as in `leftHandFist` above 0.8 with `rightHandFist` below 0.3, and it has
  to be writable, because a hand resting on the desk is in the frame and a lowered hand is not.
  `handPresent` is the exception and has no zero.
- **Left and right refer to the hands themselves, not to the frame.** Mediapipe's handedness label
  already names the physical side. The constant that decides this was set from a measurement rather
  than from the documentation, which states the opposite.

A hand feature and a face feature can share one gesture, because both reach the recognizer as a
single mapping. There is no hand layer for exactly that reason: a hand gesture is not a category on
which a binding table could be split.

### Calibration

Produced by `libre-dictum --gesture-report`, which proposes the whole block; see
[Calibration order](calibration.md) for where this step sits. It MUST be done before any hand
threshold is written.

**No hand gesture ships as a default.** The ends of a score are constants derived from the hand's
own geometry. One of them wrong in the same direction as a threshold means the gesture never fires,
and that result is indistinguishable from a failed camera. A single constant guessed wrongly has
been measured to make four gestures out of nine unable to fire at all.

`libre-dictum --hand-scores` prints every score live with the **raw ratio** beneath it. The ratios
are the values to record, because a score has already been normalised by the very constants under
calibration. It needs no session and no configured hands: it looks for the model at a path given on
the command line, then at `ht_hand_model_path`, then beside the face model. For values to configure
from, record a session instead; see [hand gesture tuning](gesture-tuning.md).

```json
"ht_hand_calibration": {
  "folded_extension": 0.36, "folded_extension_thumb": 0.58,
  "pinch_closed": 0.21, "pinch_open": 0.55,
  "spread_closed": 0.20, "spread_open": 0.50,
  "palm_area_face_on": 0.30, "near_span": 0.14, "far_span": 0.07
}
```

| Constant | Measure |
|---|---|
| `folded_extension` | A finger's `extension` ratio when folded as far as it goes. Straight is 1.0 by construction. |
| `folded_extension_thumb` | The same for the thumb, which folds far less. It has its own constant, so `handThumbCurl: {"min": 0.8}` means as folded as a thumb reaches. |
| `pinch_closed` / `pinch_open` | The `pinch` ratio when touching, and when relaxed open. |
| `spread_closed` / `spread_open` | The `spread` ratio when together, and when splayed. |
| `palm_area_face_on` | The `palmArea` ratio with the palm square to the camera. |
| `near_span` / `far_span` | The `span` ratio when held up, and when resting far back. `handHigh` needs no constant, because the frame has a top and a bottom. |

Set each constant from the **median** across takes rather than from the extreme. Set to the furthest
a hand ever folded, a typical fist scores well below 1 and every threshold then has to be lowered to
match. A pair whose ends coincide is refused at load, because it would pin its score at zero.
**Calibration is shared and thresholds are not**, so correcting `folded_extension` corrects every
fist-shaped gesture at once. `overlay meters` shows the ratios beside the scores, which is how a
wrong constant is distinguished from a wrong threshold.

### Face baseline

Produced by `libre-dictum --face-report`, which emits this block and the thresholds written against
it together; see [Calibration order](calibration.md).

**A blendshape does not idle at zero, nor at the same level on two faces, nor necessarily at the
same level on the two sides of one face.** About forty of the fifty-two idle near zero. The dozen
that do not are the ones gestures are most often built on, so a single fixed threshold can be a
deliberate expression on one feature and a resting level on its counterpart.

`ht_face_baseline` names those resting levels, and every condition is then evaluated against the
**rise** above them:

```
rise = clamp((raw − idle) / (1 − idle), 0, 1)
```

```json
"ht_face_baseline": { "eyeBlinkLeft": 0.30, "eyeBlinkRight": 0.40 },
"ht_custom_gestures": {
  "left_wink":  {"hold": 300, "eyeBlinkLeft":  {"min": 0.35}, "eyeBlinkRight": {"max": 0.09}},
  "right_wink": {"hold": 300, "eyeBlinkRight": {"min": 0.35}, "eyeBlinkLeft":  {"max": 0.09}}
}
```

Blendshapes only. A hand score here is a load error, because hand scores are already calibrated and
an absent hand is *absent* rather than resting. A level of `1` is refused. Any feature omitted is
taken to idle at zero, so an empty block evaluates raw scores directly.

Write the block with `--face-report`, which measures both halves from a recorded rest pass; see
[facial gesture tuning](face-gesture-tuning.md).

> **A baseline and the thresholds written against it MUST be replaced together.** Nothing at load
> can distinguish a migrated threshold from a stale one, and half a migration silences every facial
> gesture at once. `--face-report` emits both halves as one block for that reason, and
> `--face-replay` reports the mismatch as no gesture firing.

## The on-screen display

The surfaces are drawn by the separate `libre-dictum-hud` program. The **chip** is always present
and states the mode, the held modifiers, the held gestures and what they hold, whether anything is
being heard, the last utterance and its effect, what is asleep, and the reason when something
fails. The **sheet** is summoned. Every phrase below is a configured command, reserved and placed in
the grammar like any other.

Where the surfaces sit and how opaque they are are properties of a *screen* rather than of a voice
configuration, so they are command-line flags: `--chip-anchor`, `--sheet-anchor`, `--opacity`,
`--scale`, `--outputs` for the connector names of the session's video outputs, and `--probe` for
whether the compositor granted the overlay layer.

**Which output** is the exception, because a second monitor is not a second session. The restriction
has to survive a reload and be changeable without editing a unit file, so it is a configuration key
and the core sends it over the protocol. Confining a surface to one output requires the layer shell.
An ordinary window is placed wherever the compositor puts it, is **not** hidden for being there, and
the display reports the restriction it could not apply.

```json
"overlay": {
  "chip":   { "command": "chip toggle", "enabled": true },
  "meters": { "command": "overlay meters", "enabled": true },
  "output": { "name": "DP-1", "only": true },
  "sleep":  { "hide": true },
  "sheet": {
    "open": "overlay open", "close": "overlay close", "modes": "overlay modes",
    "pedals": "overlay pedals", "gestures": "overlay gestures", "navigate": "open"
  },
  "groups": { "::alphabet": { "say": "spelling" }, "::dots": { "hide": true } }
}
```

| Key | Default | Meaning |
|---|---|---|
| `command` | `"chip toggle"` | Shows and hides the chip. `null` removes the phrase rather than the chip. |
| `enabled` | `true` | Whether the chip is shown at startup. On reload it is applied only if *this setting* changed, so a reload cannot undo a toggle applied by voice. |
| `open` / `close` | `"overlay open"` / `"overlay close"` | A **pair** rather than a toggle, because a misheard phrase repeated must not undo itself. |
| `modes` | `"overlay modes"` | The current mode and what reaches every other mode, one column per mechanism, derived from the configuration. |
| `pedals` | `"overlay pedals"` | Both edges of every pedal the **board** has, so an unbound one is a drawn gap. |
| `gestures` | `"overlay gestures"` | Both edges of every gesture `ht_custom_gestures` **defines**, read off the gesture layer. |
| `navigate` | `"open"` | One word, prefixed to a group's name. `null` generates none, which is the control to use if extra words reduce the accuracy of a small model. |
| `say` | the group's name | For a name that cannot be pronounced, or a generated phrase that collides with another. |
| `hide` | `false` | Drops a group from the sheet **and** the grammar. |
| `meters.command` | `"overlay meters"` | Toggles the live readings. |
| `meters.enabled` | `true` | On by default, because the readings are what answer the question of why a gesture did not fire. |
| `name` | `null` | The single video output on which every surface may appear, by connector name; `--outputs` prints the available names. `null` leaves the placement to the compositor. |
| `only` | `true` | What a disconnected output means. With `true` nothing is drawn; with `false` the compositor places the surfaces until that output returns. |
| `sleep.hide` | `false` | Whether a sleep that reaches **every** mechanism also hides the chip and the sheet. The first half of a wake restores the chip for its confirmation, so no part of waking is performed without feedback. |

### Live gesture readings

The gestures page carries a third column, stating how close each gesture is and **the single
condition preventing it from firing**.

```
right_fist   hold(right_mouse)  release(right_…)  ▓▓▓▓▓▓▁▁ rightHandFist 0.62 >0.80
left_pinch   left_mouse         —                 ▓▓▓▓▓▓▓▓ held
both_palms   mode(voice:comm…)  —                 ▓▁▁▁▁▁▁▁ leftHandOpen -- >0.80
```

Green means the shape is correct. Amber means one value is short, and names it. **Dim, with `--`,
means the sensor reported nothing**, because no hand or no face was visible, which the recognizer
treats as a release and which is the most common reason a hand gesture is quiet. Only the *least
satisfied* condition is named, because with two conditions unmet, correcting either one changes
nothing. Beneath the column, while hand tracking is on, the **raw ratios** are shown, which are the
values `ht_hand_calibration` is set from, placed where they are readable while a shape is held to
the camera. They are published only while that page is open, at 10 Hz.

### Behaviour

The sheet **never times out**, because a surface that disappears part-way through being read costs
one utterance to dismiss and another to reopen at the same place. Across a mode switch it keeps the
group it had, provided the new mode has a group of that name. **A dictation mode cannot be navigated
by voice at all**, because its `{rest}` types whatever it hears and a transformer mode has no closed
grammar to add a phrase to. Both surfaces state that rather than appearing unresponsive.

`--no-hud` turns off the *socket*, and is therefore the privacy control, because a display is sent
the most recent utterance. Setting a phrase to `null` is what removes it from the recognizer.

**Group names are derived from the configuration** in two ways that require no extra authoring. A
template or an included file names what it holds, and a command written inline is grouped by its
first word wherever two or more commands share one, with singletons collected into `misc`. A group
whose phrases factor into two axes is drawn as a **table**, where the empty cells are significant:
they state which combinations are *not* configured, which no list of the configured commands can
state. The first word is not always the head word, as in `buffer save` against `save file`, so
`misc` will hold apparent families. The sheet then acts as a lint tool indicating where a rename
would help. Use `say` and `hide` for exceptions rather than as the general mechanism.

## Load-time validation

The following conditions are checked at load.

- Every mode has what its recognizer needs, and its model exists on disk.
- `starting_mode` is runnable, and nothing collides with a reserved phrase.
- `{rest}` is absent from VOSK modes, and `{0}` is absent everywhere.
- Every response parses **after aliases**: every key is known, every verb is known, and every
  `mode()` is reachable by *its own layer*. Unqualified and `voice:` targets need a runnable mode,
  whereas `gesture:` and `pedal:` targets accept a template.
- Every gesture, pedal and `hud()` target is defined, and every gesture definition is well shaped
  over real feature names.
- `icon` is three 0–255 values.
- `ht_full_speed_angle` is outside the dead angle.
- An absolute mode has a calibration and no `ht_auto_recenter_seconds`.
- `ht_face_baseline` holds only blendshapes below 1.
- No `ht_hand_calibration` pair has coinciding ends, and unknown `ht_*` keys are rejected.
- `overlay.sheet.navigate` is one word, and `overlay.output.name` is a non-empty string or null.
- No generated phrase collides with a command.
- Every `control.commands` response passes the response checks above.

A `hud(group:…)` naming no group is a **warning** rather than an error, because group names are
derived, so deleting one command can merge a family into `misc`. A display asked for a group it does
not have shows its index instead.

Reload applies the same checks and rejects an invalid candidate whole, so no edit can leave the
session without a working configuration.
