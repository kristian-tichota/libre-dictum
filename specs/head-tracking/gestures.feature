@head-tracking
Feature: Facial gestures as commands

  The face landmarker reports a score from 0 to 1 for each of a set of named blendshapes —
  jawOpen, eyeBlinkLeft, mouthPucker and so on. A gesture is a set of conditions over those
  scores, and a mode maps gesture names to the same response DSL that voice commands use.

  Gestures matter because they cover what voice cannot: they are instant, they work while
  speaking, and they work in a room where speaking aloud is not an option.

  Every gesture uses hysteresis. A gesture fires once when all its conditions are met, and will
  not fire again until it has released. Without this, holding an expression fires the action
  once per camera frame — sixty clicks a second.

  Background:
    Given the configuration:
      """
      {
        "enable_head_tracking": true,
        "ht_model_path": "models/face_landmarker.task",
        "ht_custom_gestures": {
          "blink": {
            "eyeBlinkLeft":  { "min": 0.5, "release": 0.2 },
            "eyeBlinkRight": { "min": 0.5, "release": 0.2 }
          },
          "left_wink": {
            "eyeBlinkLeft":  { "min": 0.6, "release": 0.3 },
            "eyeBlinkRight": { "max": 0.2, "release": 0.3 }
          },
          "pucker": {
            "mouthPucker": { "min": 0.9, "release": 0.1 }
          },
          "pucker_surprise": {
            "mouthPucker":      { "min": 0.9, "release": 0.1 },
            "browOuterUpLeft":  { "min": 0.3, "release": 0.06 }
          }
        },
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en", "ht_enabled": true,
            "gestures": { "blink": "left_mouse", "pucker": "right_mouse" }
          },
          "dictate": {
            "type": "transformer", "model_name": "whisper-turbo", "ht_enabled": false,
            "gestures": { "left_wink": "mode(root mode)" }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: A gesture fires when every condition is met

    Scenario: All minimums satisfied
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      Then the gesture "blink" fires
      And the mouse sequence "left_mouse↓ left_mouse↑" is emitted

    Scenario: One minimum unsatisfied
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.3   |
      Then the gesture "blink" does not fire

    Scenario: A maximum condition excludes the other eye
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.1   |
      Then the gesture "left_wink" fires
      And the gesture "blink" does not fire

    Scenario: A feature this frame did not report cannot satisfy the condition
      When the blendshape scores are:
        | feature      | score |
        | eyeBlinkLeft | 0.7   |
      Then the gesture "blink" does not fire

    Scenario: A condition is met exactly at its minimum
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.5   |
        | eyeBlinkRight | 0.5   |
      Then the gesture "blink" fires

  Rule: A gesture fires once and must release before firing again

    Scenario: Holding an expression fires once
      When the blendshape scores stay at:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      And 60 frames are processed
      Then the gesture "blink" fires 1 time

    Scenario: Releasing and repeating fires twice
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      And the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.1   |
        | eyeBlinkRight | 0.1   |
      And the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      Then the gesture "blink" fires 2 times

    Scenario: Any one feature crossing its release value re-arms the gesture
      When the gesture "blink" has fired
      And the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.1   |
        | eyeBlinkRight | 0.7   |
      Then the gesture "blink" is re-armed

    Scenario: Falling below the minimum but above the release value does not re-arm
      # This gap is the purpose of hysteresis: a score hovering around the threshold MUST NOT
      # produce a stream of fires
      When the gesture "blink" has fired
      And the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.35  |
        | eyeBlinkRight | 0.35  |
      Then the gesture "blink" is not re-armed
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      Then the gesture "blink" fires 1 time

    Scenario: An omitted release value falls back to the threshold
      Given the gesture "nod" is defined with only "jawOpen" min 0.4
      When "jawOpen" scores 0.5
      Then the gesture "nod" fires
      When "jawOpen" scores 0.4
      Then the gesture "nod" is re-armed

  @accessibility-critical
  Rule: An absent feature releases a gesture rather than scoring zero

    A gesture is only evaluated when every feature it names was reported this frame. Otherwise
    the sensor is not there, and the gesture is *released* instead.

    This exists because of `max` conditions. "Mouth closed" is `jawOpen` below 0.1, and read as
    zero an absent feature satisfies that — so looking away from the camera would fire it, and
    once fired it could never release, because a score that is always zero never crosses a
    maximum upwards. The same holds for a hand: `handFist` below 0.2 is "hand open", and it must
    not mean "hand gone".

    Releasing is also the behaviour a user expects from taking a hand out of shot, or looking
    away and back: the gesture re-arms, so doing it again fires again.

    Scenario: A gesture built from maximums does not fire when nothing reported its features
      Given the gesture "mouth_closed" is defined with only "jawOpen" max 0.1
      When no face is detected
      Then the gesture "mouth_closed" does not fire

    Scenario: It still fires when the feature is reported
      Given the gesture "mouth_closed" is defined with only "jawOpen" max 0.1
      When "jawOpen" scores 0.05
      Then the gesture "mouth_closed" fires

    Scenario: An active gesture re-arms when its feature stops being reported
      Given the gesture "mouth_closed" has fired
      When no face is detected
      Then the gesture "mouth_closed" is re-armed
      When "jawOpen" scores 0.05
      Then the gesture "mouth_closed" fires

    Scenario: A dwell in progress is dropped when the feature goes away
      # Otherwise a sensor that went quiet mid-dwell is credited for the time it was gone
      Given the gesture "mouth_closed" has a "hold" of 200
      When "jawOpen" reads 0.05 for 100 milliseconds
      And no face is detected
      And "jawOpen" reads 0.05 again for 150 milliseconds
      Then no gesture fires

    Scenario: One sensor reporting does not fire the other sensor's gesture
      # Hand and face scores arrive as one mapping, so a hand in shot with the face out of it
      # leaves the face features absent from a mapping that is otherwise full
      Given a hand gesture and a facial gesture are both defined
      When a hand is scored and no face is detected
      Then only the hand gesture fires

  @design-decision
  Rule: The more specific gesture wins

    Gestures are otherwise independent and every one matching a frame fires. The exception is a
    gesture that asks for strictly more than another — every condition of its own and at least one
    besides — which means the narrower one is not happening while the wider one is active.

    It exists so that two hands can be used at the same time: `both_open` is `left_open` and
    `right_open` and a condition besides, and without this rule the only way to keep the three
    apart was to gate each one-handed shape on the other hand being out of frame — which is
    precisely what stopped the second hand from being usable for anything else. See
    [hand-gestures](hand-gestures.feature) for that, the case it was built for.

    Condition *names* decide it, never thresholds: two gestures over the same names are told apart
    by their windows alone, which `blink` and `left_wink` here rely on.

    Scenario: The wider gesture fires alone
      When "mouthPucker" reads 0.95 and "browOuterUpLeft" reads 0.5
      Then the gesture "pucker_surprise" fires
      And the gesture "pucker" does not fire

    @accessibility-critical
    Scenario: A narrower gesture already held is released, not stranded
      # Whatever it was holding is let go, rather than left down by the gesture that has
      # taken over from it
      Given mode "root mode" maps gesture "pucker" to press "hold(left_mouse)" and release "release(left_mouse)"
      And the gesture "pucker" is held
      When "browOuterUpLeft" rises to 0.5
      Then the gesture "pucker" is released
      And the button "left_mouse" comes up
      And the gesture "pucker_surprise" fires

    Scenario: The release is dispatched before the fire
      # Or the mode the narrower one switches back to lands on top of the mode the wider one
      # has just asked for
      Given the gesture "pucker" is held
      When "browOuterUpLeft" rises to 0.5
      Then "pucker" reaches its binding before "pucker_surprise" does

    Scenario: Letting the wider one go hands the gesture back
      Given the gesture "pucker_surprise" is held
      When "browOuterUpLeft" drops to 0.0
      Then the gesture "pucker_surprise" is released
      And the gesture "pucker" fires

    Scenario: A suppressed gesture does not flicker underneath
      Given the gesture "pucker_surprise" is held
      When 60 frames are processed with both conditions holding
      Then only "pucker_surprise" is active
      And no further edge is reported

    Scenario: A suppressed gesture serves its dwell again afterwards
      # A dwell run down under suppression would be spent, and the narrower gesture would fire
      # the instant the wider one relaxed
      Given the gesture "pucker" has a "hold" of 300
      And the gesture "pucker_surprise" is held
      When "browOuterUpLeft" drops to 0.0
      Then the gesture "pucker" does not fire for another 300 milliseconds

    Scenario: A gesture still dwelling suppresses nothing
      # A gesture that has not fired has no effect on anything, which is what hold means
      Given the gesture "pucker_surprise" has a "hold" of 300
      When "mouthPucker" reads 0.95 and "browOuterUpLeft" reads 0.5
      Then the gesture "pucker" fires

    Scenario: Gestures over the same conditions suppress neither way
      # blink and left_wink name the same two features and differ only in their windows
      Then neither "blink" nor "left_wink" suppresses the other

    Scenario: Gestures sharing no condition suppress neither way
      When "mouthPucker" reads 0.95 and both eyes blink
      Then the gesture "pucker" fires
      And the gesture "blink" fires

    Scenario: The display order is still the configured one
      # Judged widest first, drawn in the order the config declares: a table that reorders
      # itself as a face moves does not allow a settling value to be read
      Then the gestures page lists "blink", "left_wink", "pucker", "pucker_surprise" in that order

  Rule: A dwell time stops a gesture firing on the way into another

    Every gesture matching this frame fires, with no mutual exclusion, so an expression on its
    way to somewhere else sets off whatever it passes through. No real smile is symmetric: while
    one ramps up there is a window where the left corner is past its threshold and the right has
    not yet reached its own maximum, which is a lopsided smirk by any definition. `hold` closes
    that window for the whole class, and it is milliseconds the conditions must hold
    continuously.

    Scenario: Without a hold, a gesture fires the first frame its conditions meet
      Given the gesture "pucker" has no "hold"
      When "mouthPucker" reads 0.95
      Then the gesture "pucker" fires

    Scenario: A hold delays the firing until the conditions have lasted
      Given the gesture "pucker" has a "hold" of 200
      When "mouthPucker" reads 0.95 for 100 milliseconds
      Then no gesture fires
      When "mouthPucker" has read 0.95 for 200 milliseconds
      Then the gesture "pucker" fires

    Scenario: Conditions broken before the dwell elapses restart the clock
      Given the gesture "pucker" has a "hold" of 200
      When "mouthPucker" reads 0.95 for 150 milliseconds
      And "mouthPucker" drops to 0.1
      And "mouthPucker" reads 0.95 again for 150 milliseconds
      Then no gesture fires
      # The first 150 ms is not credited toward the second attempt

    Scenario: A gesture merely passed through does not fire
      Given the gesture "left_smirk" has a "hold" of 250
      And the gesture "left_smirk" needs "mouthSmileLeft" above 0.4 and "mouthSmileRight" below 0.3
      When I smile over one second, the left corner leading the right
      Then the gesture "left_smirk" does not fire
      # It was true for part of the ramp, but never for 250 ms together

    Scenario: The same gesture still fires when it is held
      Given the gesture "left_smirk" has a "hold" of 250
      When I hold a left smirk for one second
      Then the gesture "left_smirk" fires exactly once

    Scenario: A hold of zero is the same as no hold at all
      Given the gesture "pucker" has a "hold" of 0
      When "mouthPucker" reads 0.95
      Then the gesture "pucker" fires
      # So adding the key changes nothing until it is given a value

    Scenario: A dwell in progress is forgotten when the recognizer resets
      Given the gesture "pucker" has a "hold" of 200
      When "mouthPucker" reads 0.95 for 150 milliseconds
      And the gesture set is reloaded
      Then the dwell starts again from zero

  Rule: A gesture definition is checked at load, not on the camera thread

    `hold` sits beside the feature names, so a mistyped one would be read as a feature whose
    limits are a bare number — and the crash would land on a recognizer thread rather than at
    load, which rule 4 exists to prevent.

    A feature *name* is checked for the same reason but a worse symptom: a misspelling never
    crashes at all. It scores zero on every frame for ever, so the gesture silently never fires
    — indistinguishable from a dead camera, a wrong threshold or an unplugged webcam.

    Scenario Outline: A definition that could never work is refused
      Given the configuration defines a gesture <definition>
      Then loading fails explaining why

      Examples:
        | definition                                  |
        | {"holdd": 250, "jawOpen": {"min": 0.4}}     |
        | {"hold": "fast", "jawOpen": {"min": 0.4}}   |
        | {"hold": -5, "jawOpen": {"min": 0.4}}       |
        | {"hold": 250}                               |
        | {"jawOpen": {"minimum": 0.4}}               |
        | {"jawOpen": {"release": 0.2}}               |
        | {"jawOpen": {"min": "high"}}                |

    Scenario: A mistyped hold is reported as the hold it was meant to be
      Given the configuration defines a gesture {"holdd": 250, "jawOpen": {"min": 0.4}}
      Then loading fails suggesting "hold"

    Scenario: A misspelled feature is refused with the name that was meant
      Given the configuration defines a gesture {"eyeBlinkleft": {"min": 0.4}}
      Then loading fails suggesting "eyeBlinkLeft"

    Scenario: The features are mediapipe's blendshapes and the hand scores, and nothing else
      Given the configuration defines a gesture {"leftHandFist": {"min": 0.8}}
      Then loading succeeds

  Rule: A gesture binds both of its edges, and nothing else

    A gesture goes active and later re-arms, and both moments are bindable -- the same shape a
    pedal binding has, because it is the same question. A gesture that only fired on the way in
    could not hold a mouse button down: nothing would ever let it go, and the spoken panic phrase
    would be the only way back.

    Scenario: A bare response is the press
      # Which is what every gestures block written before this said
      Given mode "root mode" maps gesture "pucker" to "esc"
      When the gesture "pucker" fires
      Then the key sequence "esc↓ esc↑" is emitted

    Scenario: A held gesture holds a key and letting go releases it
      Given mode "root mode" maps gesture "pucker" to press "hold(left_mouse)" and release "release(left_mouse)"
      When the gesture "pucker" fires
      Then the mouse button "left_mouse" is held
      When the gesture "pucker" is re-armed
      Then no button is held

    Scenario: An edge with nothing bound is silence, not a warning
      Given mode "root mode" maps gesture "pucker" to "esc"
      When the gesture "pucker" is re-armed
      Then no input is emitted

    Scenario: The release edge alone is allowed
      Given mode "root mode" maps gesture "pucker" to release "esc" only
      When the gesture "pucker" fires
      Then no input is emitted
      When the gesture "pucker" is re-armed
      Then the key sequence "esc↓ esc↑" is emitted

    Scenario: An edge that does not exist is refused at load
      Given mode "root mode" maps gesture "pucker" to {"hold": "esc"}
      Then loading fails saying a gesture has 'press' and 'release' and no other edge

    Scenario: Null takes back a binding a template gave
      Given a template maps gesture "pucker" to "esc"
      And mode "root mode" imports it and maps gesture "pucker" to null
      When the gesture "pucker" fires
      Then no input is emitted

    @accessibility-critical
    Scenario: A gesture is not re-pressed while it stays held
      # Otherwise a held fist would press the button on every frame rather than once
      Given mode "root mode" maps gesture "pucker" to press "hold(left_mouse)"
      When "mouthPucker" stays at 0.95
      And 60 frames are processed
      Then the mouse button "left_mouse" went down 1 time

    @accessibility-critical
    Scenario: Only the frame that ends a hold reports a release
      # Every frame the conditions do not hold is not a release, or a release binding would
      # fire sixty times a second
      Given the gesture "pucker" has never fired
      When "mouthPucker" stays at 0.0 for 60 frames
      Then no release is reported

    Scenario: A dwell that never completed releases nothing
      # It was never active, so there is nothing to let go of
      Given the gesture "pucker" has a "hold" of 200
      When "mouthPucker" reads 0.95 for 100 milliseconds
      And "mouthPucker" drops to 0.1
      Then no release is reported

    @accessibility-critical
    Scenario: The sensor going quiet releases what it was holding
      # A drag cannot outlive the hand that started it, in the same way that a failed pedal
      # board releases every pedal it left down
      Given the gesture "fist" is held and holding "left_mouse" down
      When no hand is scored
      Then the gesture "fist" is released
      And the button "left_mouse" comes up

    Scenario: The release waits for the release value, not the threshold
      # The hysteresis gap belongs to both edges: a score hovering under the threshold must not
      # produce a stream of press-release pairs
      Given the gesture "left_wink" has fired
      When "eyeBlinkLeft" reads 0.45, above its release value of 0.3
      Then no release is reported
      When "eyeBlinkLeft" reads 0.2
      Then the gesture "left_wink" is released

  @accessibility-critical
  Rule: A release runs what its press captured

    A hold outlives the mode it started in. An unqualified `mode(...)` moves all three layers by
    design, so a spoken mode name lands under a hand that is still closed — and looking the release
    edge up afresh then finds a mode that does not bind this shape. The `hold` it started never
    comes back up: stuck until the panic phrase.

    So the press remembers the mode and the binding it saw, and the release runs that. It is also
    what makes a held gesture free to move its own layer, rather than having to spell every
    gesture-driven switch `mode(voice:...)` to leave the gesture layer where the release will be
    found. The same rule applies to a pedal — see [pedals](../input/pedals.feature).

    Scenario: A held gesture survives its own layer moving
      Given mode "root mode" maps gesture "pucker" to press "hold(left_mouse)" and release "release(left_mouse)"
      And the gesture "pucker" has fired
      When the gesture layer moves to "dictate"
      And the gesture "pucker" is re-armed
      Then the button "left_mouse" comes up

    Scenario: The captured release wins over the one bound now
      Given mode "root mode" maps gesture "pucker" to press "hold(left_mouse)" and release "release(left_mouse)"
      And mode "dictate" maps gesture "pucker" to press "pageup" and release "pagedown"
      And the gesture "pucker" has fired
      When the gesture layer moves to "dictate"
      And the gesture "pucker" is re-armed
      Then the button "left_mouse" comes up
      And no key is emitted

    Scenario: No capture means no release
      # Otherwise moving the layer under a hand that is already closed fires a release
      # nobody pressed
      Given the gesture layer is in "dictate"
      And mode "dictate" maps no action to gesture "pucker"
      And mode "root mode" maps gesture "pucker" to release "esc" only
      When the gesture "pucker" fires
      And the gesture layer moves to "root mode"
      And the gesture "pucker" is re-armed
      Then no input is emitted

    Scenario: A reload drops what the press captured
      # The configuration that validated the response is gone, and a reload already releases
      # every held key for that reason
      Given mode "root mode" maps gesture "pucker" to release "esc" only
      And the gesture "pucker" has fired
      When the configuration is reloaded
      And the gesture "pucker" is re-armed
      Then no input is emitted

    Scenario: A rejected reload keeps it
      # Nothing changed underneath, so the way back is still the right way back
      Given mode "root mode" maps gesture "pucker" to release "esc" only
      And the gesture "pucker" has fired
      When a reload is rejected
      And the gesture "pucker" is re-armed
      Then the key sequence "esc↓ esc↑" is emitted

  Rule: Gesture actions are ordinary responses

    Scenario: A gesture emits keystrokes
      Given mode "root mode" maps gesture "pucker" to "ctrl + c"
      When the gesture "pucker" fires
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: A gesture can switch modes
      Given the active mode is "dictate"
      When the gesture "left_wink" fires
      Then the active mode is "root mode"

    Scenario: A gesture can run a script
      Given mode "root mode" maps gesture "pucker" to "script(notify)"
      When the gesture "pucker" fires
      Then the function "script" in "notify" is called with no arguments

    Scenario: A gesture can hold a key
      Given mode "root mode" maps gesture "pucker" to "toggle(shift)"
      When the gesture "pucker" fires
      Then the key "shift" is held
      When the gesture "pucker" is re-armed
      And the gesture "pucker" fires
      Then no key is held

    Scenario: A gesture defined globally but unmapped in the active mode does nothing
      Given mode "root mode" maps no action to gesture "left_wink"
      When the gesture "left_wink" fires
      Then no input is emitted

  @design-decision
  Rule: Gestures and pointer control switch independently

    A dictation mode can use a wink to stop dictating without the pointer drifting while the
    user reads what was typed.

    Scenario: Gestures work in a mode with pointer control off
      Given the active mode is "dictate"
      And mode "dictate" has effective setting "ht_enabled" of false
      When the gesture "left_wink" fires
      Then the active mode is "root mode"
      And no pointer event was emitted

    Scenario: A mode's own gesture map applies, not the previous mode's
      Given the active mode is "dictate"
      When the gesture "blink" fires
      Then no input is emitted
      # "blink" is mapped in "root mode" only

    Scenario: Gesture state is per gesture, not per mode
      # Switching modes mid-expression must not let a still-held expression fire again
      When the gesture "blink" has fired
      And I say "dictate"
      And I say "root mode"
      And the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.7   |
        | eyeBlinkRight | 0.7   |
      Then the gesture "blink" fires 1 time

  @concurrency
  Rule: A gesture never corrupts a command in flight

    Scenario: A gesture waits for a command's keystrokes to finish
      # closes C2 — the gesture thread shares keys_held, modifiers_held and the UInput handles
      # with every recognizer thread, with no synchronisation
      Given the command "copy" mapped to "ctrl + c"
      When I say "copy"
      And the gesture "blink" fires while the command is executing
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted
      And then the mouse sequence "left_mouse↓ left_mouse↑" is emitted
      And no key is held

    @accessibility-critical
    Scenario: A gesture action that raises does not stop tracking
      Given mode "root mode" maps gesture "pucker" to "script(explode)"
      And the script "explode" raises an exception when called
      When the gesture "pucker" fires
      Then an error is logged naming the script "explode"
      And the camera loop continues
      And the gesture "blink" still fires normally

  @tinkerer
  Rule: Debug output helps calibrate thresholds

    Choosing thresholds requires seeing live scores, because blendshape values differ
    substantially between faces and lighting conditions.

    Scenario: Debug output shows live scores for the named gestures
      Given the configuration sets "ht_debug_gestures" to ["blink"]
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.45  |
        | eyeBlinkRight | 0.12  |
      Then the debug output shows "eyeBlinkLeft: 0.45" with its requirement ">0.5"
      And the debug output shows "eyeBlinkRight: 0.12" with its requirement ">0.5"
      And the debug output shows the gesture state as idle

    Scenario: Debug output shows the active state after firing
      Given the configuration sets "ht_debug_gestures" to ["blink"]
      When the gesture "blink" fires
      Then the debug output shows the gesture state as active

    Scenario: Only the named gestures are shown
      Given the configuration sets "ht_debug_gestures" to ["blink"]
      Then the debug output does not mention "pucker"

    Scenario: A bare feature name shows its score alone
      # The only way to see a number before the gesture that thresholds it exists, which is
      # where tuning starts
      Given the configuration sets "ht_debug_gestures" to ["jawOpen"]
      When "jawOpen" scores 0.42
      Then the debug output shows "jawOpen: 0.42"

    Scenario: A feature this frame did not report reads as absent
      # Not 0.00, because on the hand path that is the difference between an open hand and no
      # hand, and calibrating against the wrong one of the two invalidates the session
      Given the configuration sets "ht_debug_gestures" to ["handFist"]
      When no hand is in frame
      Then the debug output shows "handFist: --"

    Scenario: A name that is neither a gesture nor a feature is refused at load
      # A misspelling there prints nothing, which presents as a broken debug line
      Given the configuration sets "ht_debug_gestures" to ["blimk"]
      Then loading fails listing the defined gestures

    Scenario: Debug output is off by default
      # closes C5 — the guard reads `a and b or c`, so the debug branch is entered even with
      # ht_debug_gestures empty, and the throttle never applies
      Given the configuration does not set "ht_debug_gestures"
      When the blendshape scores are:
        | feature       | score |
        | eyeBlinkLeft  | 0.45  |
        | eyeBlinkRight | 0.12  |
      Then no debug output is produced
      And no debug formatting work is performed

    Scenario: Debug output is rate limited
      # closes C5
      Given the configuration sets "ht_debug_gestures" to ["blink"]
      When 60 frames are processed over 1 second
      Then at most 10 debug lines are written
