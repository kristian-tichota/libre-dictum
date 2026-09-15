# Threads and locks

There are eight thread kinds, of which three can type. A command **executes on the recognizer
thread that heard it**, with no pool and no queue between recognition and typing, so anything slow
in a response stalls that mode.
Every mode captures all the time; `enable()`/`disable()` only gate whether a block is processed,
which is what makes a switch instant.

A recognizer belongs to its own worker: `disable()` runs on whichever thread asked for the switch,
so `_on_disabled` may only touch plain Python state. Anything said to the decoder waits for
`_on_resumed`, the only moment at which it can be told that the gap happened. A VOSK stream that is
not reset there re-delivers the phrase that switched the mode away.

## Lock ordering — gap 1

Two of the locks are ordered, `modes` outer and `executor` inner, and the code currently takes them
in **both orders**:

```
ModeManager.switch()     modes lock    → runs enter/exit_command → needs executor lock
InputExecutor.execute()  executor lock → runs mode(...)          → needs modes lock
```

Both paths are documented features. With two threads, opposite order and no timeout, the session
deadlocks permanently, the daemons keep the tray and the watchdog reporting health, and the panic
phrase cannot help because it needs the executor lock. The rule to restore is that `modes` MUST be
the outer lock and `executor` the inner one, so `mode(...)` MUST hand the switch off rather than
perform it inline.

`app`'s own `_sleep_lock` and `_held_lock` nest inside both and MUST remain leaves. Each is held
around a plain read or write of pure state and never across an outward call, which is what allows
`sleep(...)` to run under the executor lock. Publishing and performing happen after the release.

## Rules

1. **Unbounded work MUST NOT run under the executor lock**, because `panic()` needs it. `exec()`
   and `script()` violate this today (gap 2).
2. **The PortAudio callback MUST only enqueue.** Blocking there drops audio at the driver.
3. **A worker MUST NOT terminate silently.** It catches broadly, logs, and sets `failure`. The
   watchdog reads attributes and cannot read a thread that has gone.
4. **The audio queue is unbounded**, so a slow response delays commands rather than dropping them.
   Stall detection runs only when the queue is *empty*, so a blocked worker is never mistaken for a
   dead microphone.
5. **A display listener may only draw.** It is called with both executor locks released, so a
   wedged display cannot delay `panic()`. It MUST NOT take the modes lock, which would invert the
   order above, because a reload calls `release_all()` under that lock. Deliveries can arrive out
   of order, which is why `health` re-states the current picture every tick.
6. **Publishing does no I/O** (see [call-paths](call-paths.md#publishing)).

Unguarded shared fields (`AudioStream.enabled`, every `failure`, `_faults`) are single references
assigned in one statement. A field that has to change together with one of them MUST NOT be added
beside it.

## Shutdown

`Application.stop()` proceeds in order: tracker, streams (queued audio dropped, half-transcribed
chunk abandoned), held keys released, tray, displays, devices, and the session claim last, because
that claim is what keeps a second core out. There is no closing frame; a closed socket is the
signal. Typing, switching modes and opening a device during shutdown are all defects (decision 13).
