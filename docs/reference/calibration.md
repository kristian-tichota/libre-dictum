# Calibration order

Head tracking, hands and facial gestures need numbers no default can supply, and the order matters:
a threshold written against an uncalibrated score cannot be corrected by adjusting the threshold.
Every step prints a block to paste into `config.json`. None of these commands claims the session
lock, opens `/dev/uinput` or emits input, so each runs with the configuration open for editing,
although a running session may hold the camera. Steps 3 onward are optional.
[`examples/configs/head-tracking.json`](../../examples/configs/head-tracking.json) shows where every
block below sits in a whole file, with the absolute law on one mode and a slow rate mode beside it.
Its `ht_hand_calibration` holds the **built-in defaults** rather than measured values, so its two
hand gestures will not fire until step 4 has been run.

**1. Pin the camera.** `libre-dictum --cameras` prints every video device and the `camera` value
that pins the one head tracking would open. Paste that value rather than an index: `/dev/videoN`
numbers move between boots, and one webcam usually claims two of them, of which one delivers no
frames. Set `enable_head_tracking` and `ht_model_path` at the same time.

**2. Set the pointer up under the rate law.** It needs no measurement, so it confirms that camera,
model and pointer all work before anything is calibrated. Set `ht_enabled` on one mode, then adjust
by trial: raise `ht_dead_angle_h`/`_v` if the pointer drifts while the head is still, and
`ht_max_speed_px_per_sec` with `ht_full_speed_angle` for how fast the screen is crossed
([reference](configuration.md#pointer-control-per-mode)). Stop here if that is sufficient.

**3. Measure where the head points.** `libre-dictum-hud` must be running to draw the dots.

```bash
libre-dictum --calibrate-screen      # nine dots; 4 for 4x4, 3 to 6 a side
```

Aim the **nose** at each dot and hold still. Judge the result by the **expected error** rather than
the residual; above roughly 90 px, repeat it covering more head angle, which is the only lever that
matters. With more than one monitor, `ht_screen_bounds` MUST be set beside the printed block or the
mapping is silently wrong. Then set `"ht_pointer_law": "absolute"` on one mode and pair it with a
slow rate mode carrying `recenter()` in its entry binding
([why](absolute-pointing.md)).

**4. Calibrate the hand scores.** Download `hand_landmarker.task`, point `ht_hand_model_path` at it
and set `ht_hand_enabled`. `libre-dictum --hand-scores` shows live scores with the raw **ratio**
under each, and the ratios are what a constant is set from, because a score has already been
normalised by the constants being measured. For values worth configuring, record a session:

```bash
libre-dictum --gesture-capture ~/hand-session    # eight rounds, then a neutral pass
libre-dictum --gesture-report  ~/hand-session    # ends in a proposed ht_hand_calibration
```

Paste the proposal whole. The constants are shared, so correcting one corrects every gesture built
on the score it feeds ([reading the report](gesture-tuning.md)).

**5. Write the hand thresholds** into a fragment rather than into `config.json`, and test against
the recording already made:

```bash
libre-dictum --gesture-replay ~/hand-session --candidate try.json
```

The replay decides, because no per-feature statistic knows that conditions MUST hold
simultaneously, or that `hold` restarts when one lapses. A gesture that still never fires is a
calibration problem rather than a threshold problem, so return to step 4.

**6. Measure the resting face.** A blendshape idles above zero at a different level on every face,
so a facial threshold is a **rise** from rest and rest has to be measured first.

```bash
libre-dictum --face-capture ~/face-session    # a 20 s rest pass, then five labelled passes
libre-dictum --face-report  ~/face-session    # ht_face_baseline + ht_custom_gestures, one block
libre-dictum --face-replay  ~/face-session --candidate try.json
```

**Paste both halves or neither.** Nothing at load can distinguish a migrated threshold from a stale
one, and replacing one half silences every facial gesture at once
([the columns](face-gesture-tuning.md)).

**7. Confirm it fires.** Start the session and say `overlay gestures`, then `overlay meters`. The
third column states how close each gesture is and names the single condition holding it shut. A dim
`--` means the sensor reported nothing, which is the commonest cause of a quiet hand gesture and is
not a threshold problem. The raw ratios appear beneath, which is how a wrong constant is told from
a wrong threshold.
