# Behavioural specification

This directory describes how libre-dictum behaves. Every `@fails-today` scenario has been
implemented and the tag removed, but a scenario MUST NOT be treated as a description of current
behaviour until it has been checked against
[known-gaps](../docs/development/known-gaps.md). A `# closes C1` comment names the defect a scenario
was written for, and it remains in place so that the behaviour is not simplified back out.
[`../tests/`](../tests) mirrors this directory.

## Personas

Two personas settle every priority call. **The accessibility user** cannot fall back to a keyboard,
so a silent failure is worse than a loud one, a stuck modifier is an emergency, and every state
requires a hands-free exit (`@accessibility-critical`). **The tinkerer** treats the configuration
schema as the product, so errors MUST be specific and MUST arrive at load time rather than as
silence three utterances later (`@tinkerer`). The other tags are `@design-decision`, `@vosk`,
`@transformer`, `@head-tracking`, `@pedals` and `@concurrency`.

## Conventions

`Rule:` blocks require a Gherkin 6 parser. There are no step definitions yet, and phrases are
deliberately reused, so the `.feature` files MUST be searched before a new phrase is introduced.
Configuration fragments are real, parseable JSON. Keystrokes are written as the emitted sequence,
for example `"ctrl↓ c↓ c↑ ctrl↑"`, because modifier bookkeeping defects are invisible at any coarser
granularity.

## Decisions

The decisions below were chosen during review rather than read off the code. Each is tagged
`@design-decision` at the scenario that pins it down and is cited as "decision *n*". The numbers are
stable, so a new decision MUST be appended and MUST NOT be inserted.

1. **Failure is degraded, never fatal.** A failed webcam MUST NOT take voice control down with it.
2. **A panic command is built in.** It is reserved, present in every grammar, and releases
   everything from any mode.
3. **`{rest}` is rejected in VOSK modes** at load, because a closed Kaldi grammar cannot honour it.
4. **Reload is atomic.** An invalid candidate is rejected whole and the session is untouched.
5. **Bare mode names keep switching priority** ahead of the command table. The cost, that a
   dictation mode cannot type its own mode names, is accepted.
6. **Every head-tracking setting is global with a per-mode override**, inversion included.
7. **Gestures and pointer control are independently switchable.**
8. **Saved variables are a count prefix**, consumed positionally in the way a number typed before a
   vi motion is. `{1=1}` is what allows one command to work with or without one.
9. **A second `save()` replaces the first**, because spoken corrections have to work. Only one value
   is ever pending, and that cost is accepted.
10. **A missing model file is a configuration error and a model that fails to load is not.** The
    first is reported before anything starts, and the second costs only its own mode.
11. **A transient device failure is retried and a configuration error is not.** A camera and a pedal
    board each get five growing delays, then are given up on with an error, with the reload phrase
    as the recovery.
12. **Reserved phrases cannot be reused.** A command with the same phrase would be unreachable
    configuration, so it is rejected at load, naming the setting that reserved the phrase.
13. **Shutdown abandons in-flight work.** Text arriving after a request to quit would land in
    whatever window holds focus.
14. **Text is typed, not pasted.** No paste chord works everywhere, so a pasting response works in
    whatever application its author had open and misfires elsewhere, which for this persona is
    silent corruption of dictated text. The cost is layout independence: uinput emits codes, the
    keymap decides the glyph, and the table is a US layout that nothing can verify.
    `script(copy;;…)` remains available for when the clipboard is the actual goal.
15. **The display is a separate program and the socket is one-way.** The core needs `/dev/uinput`
    and a Wayland client needs the session's display, so they cannot be one process. The core writes
    and never reads, which is enforced by shutting the accepted socket down for reading, so a
    display that crashes, wedges or is replaced cannot ask the process holding `/dev/uinput` to
    type. Publishing does no I/O on the caller's thread, so a stalled display cannot delay a mode
    switch or a panic and is dropped instead. Each frame is a whole picture, so a display that
    missed one is correct after the next frame rather than after a replay.
16. **Every phrase reaching the display is configuration, and the per-group phrases are generated.**
    A VOSK grammar is closed and only `{numeric}` expands, so a single `open {rest}` cannot work and
    each group needs a concrete phrase. Generating them is what makes the phrase the sheet *draws*
    beside a group the same phrase that *opens* it, and a discoverability tool that needed its own
    phrase memorised would not serve this persona. The sheet gets a **pair** of phrases, because a
    misheard phrase said twice must not undo itself, and the chip gets a toggle, because it is
    always on screen. The display is asked after mode names and **before** the command table, which
    is safe only because colliding generated phrases are refused at load.
17. **Three mechanisms, three mode pointers, one table.** Only the voice pointer owns anything,
    namely streams, enter and exit commands, and tray colour, so moving the others costs nothing and
    any mechanism may move any other. Separate tables per mechanism were rejected, because gestures
    are already written per voice mode and splitting them would mean keeping two tables in step by
    hand. The voice layer is the only one that can refuse, and it refuses for all three, because a
    mode that cannot hear cannot hear the phrase that leaves it and a partial switch is worse than
    none. `enter_command` and `exit_command` remain the voice layer's. A gesture or pedal layer may
    sit on a template, because a pedal layout needs no recognizer and requiring one would mean
    loading a multi-gigabyte model to change what a pedal does.
18. **A pedal binds both edges, and nothing else.** Both, because sustaining a state is the one
    thing a pedal does better than a voice. Tap-versus-hold and chords were rejected, because each
    needs a timing window and a window stops the plain press being instant.
19. **A pedal the device took with it is released.** A short report is *not* read as a release,
    because a truncated read is not evidence that a pedal moved.
20. **A layer is shown only when it has something to report.** A row that is always present carries
    no information by being present, and the tray uses an arc rather than a colour so that the solid
    disc survives.
21. **Hands share the gesture layer.** One gesture can name `handFist` *and* `jawOpen`, so a hand
    gesture is not a category a table can be split on, and a `hand:` layer would need an arbitrary
    rule for which table a mixed gesture reads from. A hand still fails on its own. The second model
    runs on the **same thread and frame**, every `ht_hand_stride`-th frame: it costs five times the
    face model and so has to be sampled below the frame rate in any case, and once pointer speed was
    expressed per second the resulting hitch stopped costing the pointer anything. One thread means
    no queue, no hand-off, and a recognizer that need not be re-entrant.
22. **A feature no frame reported is absent rather than zero, but a hand that is not there is
    zero.** Read as zero, a gesture of `max` conditions would begin to hold the instant its sensor
    stopped reporting, fire, and never release. Releasing is also what taking a hand out of the
    frame ought to mean. The distinction is between the model not having looked and the model having
    looked and found nothing: once *any* hand is visible the other hand's scores read zero, which is
    what makes an exclusive pair writable. `handPresent` is the exception and has no zero.
23. **A feature name is checked at load, and no hand gesture ships.** A misspelled feature never
    crashes; it scores zero permanently, which is indistinguishable from a failed camera. The
    shipped set stays facial for the same reason: the hand constants are per-hand measurements, and
    a default on the wrong side of a real hand ships exactly that silence.
24. **A gesture binds both edges, exactly as a pedal does.** The release edge was always available
    and was being discarded. Without it a gesture could start a `hold()` that only the panic phrase
    could end. Neither edge may be lost: a failed board releases every pedal, and a hand leaving the
    frame releases every gesture. A click and a drag then become different shapes rather than
    different mechanisms.
25. **Mediapipe's handedness label already names the physical side.** Its documentation states the
    opposite; measured against a real camera the documentation was wrong, and every per-side score
    landed on the opposite hand. One constant decides this and is set from the measurement, because
    getting it backwards is silent.
26. **The wider gesture wins, and a release runs what its press recorded.** Suppression by strict
    superset of *condition names* needs no configuration key and nothing to remember. An explicit
    `suppressed_by` list was rejected, because a forgotten entry is a double fire. Only an *active*
    gesture suppresses. Separately, a hold outlives the mode it started in, so a release looked up
    afresh would find a mode that does not bind the shape.
27. **Sleep is a state rather than a mode, and it takes two requests.** A sleep *mode* cannot be
    written: every mode name is in every grammar, so that a mode can always be left hands-free,
    which means a mode binding nothing would still switch modes whenever the room said its name. A
    request arms, and only its repeat inside `sleep_confirm_seconds` applies it. `sleep` and `wake`
    are deterministic and never a toggle. **The mechanism that confirms a sleep owns it** and a wake
    from elsewhere is refused, which is what makes a deliberately harder wake worth configuring,
    while a successful reload wakes everything and is subject to no ownership check, so that an
    edited-away binding or a disconnected board cannot make a sleep permanent.
28. **A run of bare pedal taps is a toggle that a held modifier can carry.** A tap is a press under
    `tap_ms` with *nothing typed* inside it, and both halves are load-bearing. Without the first, a
    run fires on ctrl+c followed quickly by ctrl+v. Without the second, a modifier held in order to
    zoom by scrolling is indistinguishable, because that press reaches this process as no key events
    at all. A bare modifier tap changes nothing in any application, which is what makes it safe at
    any moment and impossible to produce by working.
29. **The surfaces are confined to one named video output, and the name is configuration.** Where a
    surface sits on a screen is a command-line flag, being a property of that screen, but *which*
    screen has to survive a reload and a cable being disconnected, so it is a configuration key and
    the core sends it with the catalogue. An output that is not connected then draws nothing, because
    naming one is a statement about where the user is looking, so the other screens are not a
    fallback. `only: false` is the control for a machine that is sometimes docked, and pinning
    requires the layer shell either way.
30. **A full sleep may take the display with it, and hands the chip back to be woken.** This is off
    by default, because a display that disappears is indistinguishable from one that crashed, which
    is the problem the chip exists to solve. When on, it applies only once *every* mechanism is
    asleep, because one mechanism resting is a session still working and still with something to
    report. The wake confirmation and a refusal both restore the chip, since waking takes two
    requests inside a window and the line stating so is drawn there. Waking restores exactly what
    was showing.
31. **Other programs run named commands over a second socket, and nothing else.** The display socket
    stays one-way (decision 15). The control socket is off by default, opens for this user alone, and
    runs only the names `control.commands` declares, so a process that connects can run what the
    configuration names and cannot type text of its own. Sleep does not gate it: no mechanism sent
    the request, and a layout change dropped while asleep would leave the pedals out of step with
    the program that asked for it.
