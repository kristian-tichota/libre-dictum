# Hand gesture tuning

```bash
libre-dictum --gesture-capture DIR      # guided, resumable, interruptible
libre-dictum --gesture-report  DIR      # takes, matrix, per-gesture, ratios, proposal
libre-dictum --gesture-replay  DIR --candidate try.json
```

No hand gesture ships as a default. A `Calibration` constant that is wrong in the same direction as
a threshold produces a gesture that never fires, and that result is indistinguishable from a failed
camera. None of the three modes claims the session lock, opens `/dev/uinput` or emits input, so all
three remain usable while the configuration is open for editing.

## Recorded quantities

Capture records **landmarks, not scores**. A score has already been normalised onto 0-1 by the
calibration constants under measurement, so a recording of scores could only be interpreted under
the calibration that was guessed before the recording began.

The recognizer is a geometric threshold set rather than a classifier, and mediapipe's
`gesture_recognizer.task` cannot replace it: one softmax label out of 33 mutually exclusive labels
cannot be combined with a facial feature, thresholded, or given hysteresis, which is the entire
configuration surface. Landmark extraction is the whole cost either way. If geometry does not
separate a shape, the replacement is a classifier in *landmark* space. The obvious training set is
HaGRID, which MUST NOT be vendored: this project is AGPL-3.0-or-later, the dataset licence is a
CC BY-SA 4.0 variant, and the pretrained weights state no licence at all.

## Capture order

Capture runs eight rounds round-robin, then one minute of hands performing no gesture. Takes
recorded back to back share a hand position, a time of day and a state of fatigue, so their spread
understates the real spread and the resulting threshold comes out too tight. A take is one span with
a hand present, with grace at both ends. A span shorter than `Segmenter.minimum` is discarded and
requested again, which is how a take performed wrongly is rejected.

## Report

- **matrix** — feature against label, holding the peak each label sustained. Read across a row: a
  `min` below any cell in that row also fires during that label. Cross-fire is visible only here,
  because the gesture that fires by mistake is the one under no observation.
- **per gesture** — one value per take. Read down a column, where the *worst* take MUST clear the
  threshold, because a peak conceals the spread it came from. Each gesture is measured at its own
  `hold`. Judged instead at a fixed 200 ms, a pinch performed as repeated opening and closing
  sustains nothing.
- **ratios and proposal** — each constant is the **median** across takes rather than the extreme.
  Set to the furthest a hand ever folded, a typical fist scores well below it and every threshold
  must then be lowered to match. A proposal that would not load is withdrawn and named.
- **replay** — a per-feature statistic cannot know that conditions MUST hold simultaneously, or that
  `hold` restarts whenever one condition lapses, so the replay decides. `--candidate` takes a
  configuration fragment verbatim. `--stride` defaults to the session's `ht_hand_stride`; replaying
  every frame gives `hold` a resolution no session achieves, and passes gestures a session misses.

A neutral pass of one minute is not sufficient evidence that a gesture does not misfire. The neutral
pass SHOULD run for at least six minutes.

Unbuilt and preferable: hand height relative to the **face**, which is independent of camera
placement. The face model already runs on the same frame, but the feature crosses two models and
needs a recording that retains face landmarks.
