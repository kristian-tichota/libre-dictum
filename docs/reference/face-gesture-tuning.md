# Facial gesture tuning

```bash
libre-dictum --face-capture DIR
libre-dictum --face-report  DIR
libre-dictum --face-replay  DIR --candidate try.json
```

The statistics are reused whole from [hand tuning](gesture-tuning.md). What does not transfer is the
pacing. A hand bounds its own take by entering and leaving the frame, whereas a face is present for
the whole session, so a take has to be bounded by the expression instead.

## Resting level

A blendshape does not idle at zero, and does not idle at the same level on two faces. A 20 s rest
pass therefore opens every session, and its result is `ht_face_baseline`
([reference](configuration.md#face-baseline)).

Each capture gate is a rise of `ACTIVE_MARGIN` of the range remaining above the idle level, which is
a share of what is left rather than a flat step. A flat step cannot serve: a feature that idles high
leaves less room above idle than the step itself, so the gate sits above the highest value that
feature can reach and the performance is recorded as one the face cannot make.

The gate watches the prompted performance rather than the whole face, because an ordinary blink
occurs about twenty times a minute. `expressions.GATES` names one or two features per performance.
It is deliberately coarse, and it MUST NOT be read from the candidate configuration, or the
recording would only be interpretable under the thresholds guessed before it. A span shorter than
0.5 s is treated as a reflex and discarded silently. Twelve seconds of stillness abandons that
performance into `skipped.json`.

## Recorded quantities

Capture records **scores, not landmarks**, because nothing sits between mediapipe and a blendshape.
Yaw and pitch are recorded alongside as the session's confound, and `session.json` carries a `kind`
per pass. There are **five labelled passes** over six rounds rather than eight, because facial
muscles fatigue faster than a hand: `rest`, `talking` (`jawOpen`, `mouthFunnel`, `mouthPucker`,
`mouthStretch*`), `reading` (`browDown*`, `eyeSquint*`), `amused` and `moving`.

## Proposal

The proposal covers **both halves**, because they are one answer. Per condition, `own` is the worst
take of the gesture's own performance and `rival` is the most any other label sustained at the same
dwell; the threshold is placed between the two, nearer the rival. `release` is measured upward from
the idle level. `hold` is the **shortest** dwell at which every condition still has room, reported
at the candidate dwell so that `hold` stays selectable. A `--` means no room at any dwell. There is
no `--stride`, because every frame carries a score.

A baseline and the thresholds written against it MUST be replaced together. The migration is exact,
`(t − idle) / (1 − idle)`, but nothing at load can distinguish a migrated threshold from a stale one
— both are numbers in [0, 1] — and half a migration silences every facial gesture at once.
`--face-replay` reports that condition as no gesture firing at all.
