# Call paths

The traces themselves are readable from the code. The order and the threading below are not.

## Dispatch order

`dispatch._dispatch`, after `text.normalize`:

```
panic phrase → reload phrase → sleep gate (drop the rest) → previous-mode phrase → mode name
→ HudService.handle(phrase) → banned_strings (dropped, not shown) → commands, first match
```

The display sits above the command table, so that a phrase spoken *because* a command cannot be
remembered reaches the display rather than the command table. A VOSK command fires
from a **partial** result, so it runs before the speaker stops. The report to the display is sent
*before* the response runs.

## Callback threads

`rotation_callback` and `gesture_callback` run on the `head-tracking` thread, and pedal edges run on
the `pedals` thread. `chunk_callback` runs on that mode's recognizer worker, and therefore so do
**the whole command, the executor and the keystrokes**.

## Edges

A gesture and a pedal share `app._edge`. The press records `(mode, binding)` and the release runs
what the press recorded, because a hold outlives the mode it started in. Both are gated by sleep
*before* the press is recorded, so no release is ever left behind.

## Meters

`tracker._report_meters` asks `HudService.wants_meters` **before** building anything, and is capped
at `METER_INTERVAL_SECONDS`. That flag is false unless the sheet is open at the gestures page. Held
gestures take the other path and are always published, as names only, because the bindings are
already in the display's catalogue.

## Publishing

Every `HudService.set_*` assigns under its own lock and hands the picture to each display's own
thread. It **MUST NOT** perform I/O: `set_mode` arrives with the modes lock held, and `set_held`
arrives one statement after the executor lock was released. The latest picture wins, and a changed
catalogue is written before the state that names it.

## Symptoms

| Symptom | Where to look |
|---|---|
| A command does nothing | the chip's utterance line, or `_report_near_miss` at DEBUG |
| Nothing responds at all | whether a mechanism is asleep: the chip's italic line, which `overlay.sleep.hide` may have removed |
| A command stopped working after an edit | `model.check_display` — a generated phrase may have taken it |
| A command fires twice | `partials.PartialTracker` |
| A command fires late | a blocked worker; rule 1 in [concurrency.md](concurrency.md) |
| A mode will not activate | `modes._unavailable`, then `failed_modes` |
| An edit did not take effect | `modes._stream_signature` |
| The pointer drifts | `PointerFilter.filter`, `ht_dead_angle_*` |
| The pointer is on the wrong monitor | `ht_screen_bounds` |
| A gesture will not fire | `overlay meters`, where the `now` column names the blocking condition |
| Head tracking stopped | `tracker._run` retry loop, `RETRY_DELAYS` |
| The overlay is blank or stale | `HudService.failure`, then `hud-client-N` in the log |
| The overlay never appears | `libre-dictum-hud --dump`. Output there means the toolkit is at fault |
| The overlay is on no screen | `overlay.output.name` against `libre-dictum-hud --outputs` |
