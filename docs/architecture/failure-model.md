# Failure model

Decision 1 states that **failure is degraded, never fatal and never silent.** Exactly one point may
end the process: `main()`, before anything has started, where a configuration or device problem
cannot be worked around. After `start()` returns, nothing may take the session down.

## Scope of a failure

| Failure | Costs | Recovery |
|---|---|---|
| Config missing / invalid, model path absent, `/dev/uinput` unwritable, another session running | the process, before start | exit 1 with a message naming the cause |
| Model or microphone will not open, or the recognizer thread raises | **one mode** | reload phrase |
| Microphone stops delivering (5 s) | one mode | **automatic** when audio returns |
| Camera will not open or stops (2 s) | head tracking | **automatic** ×5 on `RETRY_DELAYS`, then reload phrase |
| Tracker raises anything else | head tracking | reload phrase, no retry |
| Hand model fails | **hand scores only** — held gestures are **released**, so a drag cannot outlive the model | reload phrase |
| Pedal board will not open, or fails | the pedals; every pedal still down is **released** | retry ×5, then the reload phrase |
| A missing optional extra (mediapipe, hidapi, pystray, pycairo) | that subsystem | a warning naming the extra |
| Display socket will not bind | the displays | reload phrase |
| A display wedges (2 s write), exits, or receives a bad frame | **that display** | it reconnects, and a bad frame leaves the picture it had |
| No `gtk4-layer-shell`, no typelib, or no layer shell in the compositor | **the layer**: one desktop, no keep-above, no click-through | the `ERROR` names the package, the build option or the window rule |
| A response will not parse, or a script or program raises | one response, or one verb | logged, and the rest of the response still runs |
| Reload rejected | nothing | `_faults["configuration"]` reaches the tray, and the previous configuration keeps running |

Three of those rows do not hold today: a wrongly typed configuration value, a malformed
`ht_custom_gestures` block, and a program launched by `exec()`. See
[known-gaps](../development/known-gaps.md).

## Not failures

A mode with no recognizer is not a failure. `_unavailable` refuses to switch *into* such a mode,
because no phrase could then leave it. Neither are an absent tray, absent head tracking, no
connected display, or a sleeping mechanism.

## Reporting

`Application.refresh_health` polls every 2 s, because a thread that has died cannot report
anything. `HealthMonitor` is handed the **whole picture** and reports only differences, which is
what makes a recovery reportable and keeps a camera that fails 60 times a second out of the log. On
the tray a fault is an **exclamation mark rather than a colour**, because every colour is one a mode
may claim. The chip receives the whole `Health`, so it can state the reason.

## Hands-free recovery

The panic phrase releases held keys. **The reload phrase performs the general repair**: it rebuilds
failed streams, reopens a dead camera and pedal board, retries the socket, and wakes every sleeping
mechanism. Both phrases are reserved, present in every grammar, handled before the command table,
and heard while a mechanism is asleep.

## New failure paths

Decide the scope of the failure first. A new path MUST set a `failure` attribute rather than terminate
silently, and MUST report through `logging`, never through `print` and never through
`warnings.warn`, which deduplicates and would silence a repeated fault. Add the path to
`Application._failures` and `_optional_subsystems` so that it reaches the tray, and give it a
hands-free recovery, preferably the reload phrase.
