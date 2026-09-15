@head-tracking
Feature: Hand shapes as gesture conditions

  The face landmarker reports named blendshape scores and the whole gesture configuration is built
  on them, as thresholds with hysteresis over a name and a number. The hand landmarker reports no
  such scores, only 21 points per hand and no classification of the shape.

  The points are therefore converted into scores under the same convention, so that a hand gesture
  becomes an ordinary `ht_custom_gestures` block. Nothing downstream distinguishes a hand: the same
  recognizer, the same min, max and release windows, the same debug line, and the same response
  DSL.

  Every score is between 0 and 1 and one-sided, the way mediapipe splits a signed feature into
  `eyeLookUpLeft` and `eyeLookDownLeft` rather than reporting a signed one.

  This file covers the scoring contract, which is what a configuration is written against, and
  the camera-side half that feeds it: a second model on the same frame, sampled below the frame
  rate, merged into the one recognizer.

  Background:
    Given the gesture definitions:
      """
      {
        "fist": {
          "handFist": { "min": 0.8, "release": 0.4 }
        },
        "left_fist": {
          "leftHandFist": { "min": 0.8, "release": 0.4 }
        },
        "point_up": {
          "handIndexCurl":  { "max": 0.2, "release": 0.4 },
          "handMiddleCurl": { "min": 0.7, "release": 0.4 },
          "handPointingUp": { "min": 0.8, "release": 0.5 }
        },
        "pinch": {
          "handPinchIndex": { "min": 0.85, "release": 0.4 }
        }
      }
      """

  Rule: Two scores say where the hand is, because no shape score can

    Every other score is a ratio of the hand against itself, so it reads the same wherever the
    hand is. That invariance is deliberate and it is what makes a threshold portable — but it
    also means nothing in the shape of an open palm distinguishes one held up deliberately from
    the same palm resting in the frame. Measured against a minute of hands performing no gesture,
    `handOpen` sits at 1.00 for over a second, no threshold separates the two cases, no `hold`
    separates them, and palm orientation separates them in the wrong direction.

    `handHigh` and `handNear` therefore break that invariance deliberately. They are the cost of an
    open-hand gesture that does not fire while a hand reaches for the mouse, and they are the
    only two scores a configuration has to re-measure when the camera moves.

    Scenario: A hand higher up the frame scores higher
      Given a hand in view
      When it is raised toward the top of the frame
      Then `handHigh` rises
      And a hand mostly below the frame scores zero, because it is not being held up

    Scenario: Height is read off the wrist, so closing the fingers does not move it
      Given a hand at one height
      Then `handHigh` is the same whether it is open or in a fist
      And a gesture gated on height keeps its threshold as the shape changes

    Scenario: A nearer hand scores higher
      Given the same hand at two distances
      Then the closer one scores higher on `handNear`
      And the two ends of that scale are `near_span` and `far_span`, which are calibration

    Scenario: Every other score stays indifferent to where the hand is
      Given a hand moved across the frame and scaled
      Then every score except `handHigh` and `handNear` is unchanged
      And a new score belongs to one side of that line or the other, never neither

  Rule: A hand gesture is configured exactly like a facial one

    A hand that required its own condition syntax, its own recognizer or its own hysteresis rules
    would double the schema the tinkerer has to learn for no benefit.

    Scenario: A closed hand fires a gesture through the ordinary recognizer
      When a hand is scored with every finger folded
      Then the gesture "fist" fires

    Scenario: A held shape fires once
      Given a hand was scored with every finger folded and "fist" fired
      When the same hand is scored again
      Then no gesture fires
      # Without hysteresis a held fist would produce sixty clicks a second, as for any facial
      # gesture

    Scenario: Opening the hand re-arms the gesture
      Given a hand was scored with every finger folded and "fist" fired
      When a hand is scored with every finger straight
      And a hand is scored with every finger folded
      Then the gesture "fist" fires

    Scenario: Several conditions must hold together
      When a hand is scored with the index straight, the other fingers folded, pointing up the frame
      Then the gesture "point_up" fires

    @tinkerer
    Scenario: One gesture may mix a hand condition with a facial one
      # Both feature sets reach the recognizer as one mapping, so this needs nothing new
      Given the gesture definitions:
        """
        {
          "shout": {
            "handFist": { "min": 0.8 },
            "jawOpen":  { "min": 0.5 }
          }
        }
        """
      When a hand is scored with every finger folded
      And the blendshape score "jawOpen" is 0.7
      Then the gesture "shout" fires

  Rule: A score describes a shape, not a position

    A hand is not held at a fixed distance from the camera, and a threshold that varied with that
    distance would have to be retuned after every change of seating position.

    Scenario Outline: The same shape scores the same wherever the hand is
      Given a hand is scored with every finger half folded
      When the same hand is <change>
      Then every score is unchanged

      Examples:
        | change                       |
        | moved to the left of the frame |
        | moved down the frame          |
        | twice as large                |
        | less than half as large       |

    Scenario: Distances are measured against the hand's own palm span
      # This is what makes the preceding scenario hold: every distance is a ratio rather than a
      # pixel count
      When a hand is scored with the thumb tip laid on the index tip
      Then the score "handPinchIndex" is 1.0
      And the gesture "pinch" fires

  Rule: Which hand is asked for, or which hand is up

    Scenario: An unprefixed score describes the hand that is up
      When one hand is scored with every finger folded
      Then the gesture "fist" fires

    Scenario: A prefixed score names one hand
      When a right hand is scored with every finger folded
      Then the gesture "fist" fires
      And the gesture "left_fist" does not fire

    Scenario: Both hands are scored separately
      When a left hand with every finger straight and a right hand with every finger folded are scored
      Then the score "leftHandFist" is lower than "rightHandFist"
      And the score "bothHandsPresent" is 1.0

    Scenario: The unprefixed scores are one hand's, never a blend of two
      # A maximum across hands would combine one hand's folded index with the other's extended
      # thumb and report a shape neither hand made
      When a left hand with every finger straight and a right hand with every finger folded are scored
      Then the score "handFist" equals "leftHandFist"

    Scenario: A hand whose side the landmarker will not name still scores
      When a hand of unknown handedness is scored with every finger folded
      Then the gesture "fist" fires
      And it puts its shape on neither side's name

    @tinkerer
    Scenario: A direction keeps its own left and right
      # `rightHandPointingLeft` is the right hand pointing to the user's left. A suffix would
      # spell this `handPointingLeftRight`, which is ambiguous.
      When a right hand is scored pointing to the user's left
      Then the score "rightHandPointingLeft" is 1.0

  Rule: A thumb has its own direction, because a thumbs-up needs one

    `handFist` deliberately ignores the thumb, so a fist and a thumbs-up score the same on it.
    What separates them is the thumb: folded in one, straight and pointing somewhere in the other.
    And nothing but a thumb direction can tell a thumbs-up from a thumbs-down.

    Scenario: A thumbs-up is a fist with a straight thumb pointing up
      When a hand is scored with the fingers folded and the thumb straight and up
      Then the score "handFist" is high
      And the score "handThumbCurl" is low
      And the score "handThumbPointingUp" is high

    Scenario: Turning the hand over turns the thumb over
      When the same hand is scored upside down
      Then "handThumbPointingDown" reads what "handThumbPointingUp" read
      And "handThumbPointingUp" is 0.0

    Scenario: A closed fist is not a thumbs-up
      # This is what separates the two gestures: a `max` on handThumbCurl excludes the fist and
      # a `min` excludes the thumbs-up, so a fist bound to a mouse button does not also page the
      # document
      When a hand is scored with every finger folded and the thumb folded with them
      Then the score "handFist" is high
      And the score "handThumbCurl" is high

    Scenario: The thumb and the index are scored apart
      When a hand is scored with the index folded and the thumb straight
      Then "handThumbPointingUp" is above "handPointingUp"

    Scenario: A thumb of no length points nowhere
      When a hand is scored collapsed to a single point
      Then every thumb direction is 0.0

  Rule: One hand up means the other hand is answered, not absent

    No hand at all reports nothing, so a gesture naming a hand feature is released rather than
    evaluated. Once *any* hand is visible, however, the model has looked, and it can say the other
    hand is not making a fist. The distinction is between the model not having looked and the model
    having looked and found nothing there.

    This is what makes an exclusive pair writable, and it has to be writable, because a hand
    resting on the desk is in the frame while a lowered hand is not, and "left fist, right hand
    not a fist" means the same in both cases.

    Scenario: With one hand up the other hand's shape scores read zero
      When a right hand is scored with every finger folded
      Then the score "rightHandFist" is high
      And the score "leftHandFist" is 0.0
      And the score "leftHandOpen" is 0.0

    Scenario: An exclusive pair fires with the other hand out of frame
      Given the gesture "left_only_fist" needs "leftHandFist" above 0.8 and "rightHandFist" below 0.3
      When only a left hand is scored, with every finger folded
      Then the gesture "left_only_fist" fires

    Scenario: The same pair fires with the other hand in frame but open
      When a left hand with every finger folded and a right hand with every finger straight are scored
      Then the gesture "left_only_fist" fires

    Scenario: And does not fire when both hands are fists
      When both hands are scored with every finger folded
      Then the gesture "left_only_fist" does not fire

    Scenario: One hand up answers bothHandsPresent with zero
      # Rather than leaving it out, which would make "one hand only" unwritable
      When one hand is scored
      Then the score "bothHandsPresent" is 0.0

    Scenario: handPresent has no zero
      When one hand is scored
      Then the score "handPresent" is 1.0
      # and when no hand is visible the score is absent rather than 0.0, because a gesture MUST
      # NOT fire on an absent sensor

  @accessibility-critical
  Rule: The two hands are used at the same time

    One hand holds a shape, such as an open right hand selecting a precision pointer law, while
    the other clicks and drags. That is the purpose of the per-hand scores, and the recognizer
    needs nothing further, because each gesture is judged on its own conditions and one hand's
    conditions never name the other.

    A set of one-handed gestures MUST NOT be written with gates. Distinguishing `both_open` from
    `left_open` and `right_open` by gating each one-handed shape on `bothHandsPresent` being low
    releases the first hand's gesture, and whatever it holds, as soon as the second hand is raised.
    `both_open` requires strictly more than either half, so
    [the more specific gesture wins](gestures.feature) performs that role and no gate is needed.

    Scenario: Each hand fires its own gesture
      Given the gesture "left_fist" needs "leftHandFist" above 0.8
      And the gesture "right_open" needs "rightHandOpen" above 0.8
      When a left hand with every finger folded and a right hand with every finger straight are scored
      Then the gesture "left_fist" fires
      And the gesture "right_open" fires

    Scenario: The second hand arriving does not disturb the first
      Given the gesture "right_open" is held
      When a left hand enters the frame with every finger folded
      Then the gesture "right_open" is not released
      And the gesture "left_fist" fires

    Scenario: A one-handed gesture needs no gate against the other hand
      # The misfire guard is handHigh together with a dwell, both of which describe one hand
      Given the gesture "right_open" needs "rightHandOpen" above 0.8 and "rightHandHigh" above 0.16
      When a right hand held high is scored and a left hand rests low in shot
      Then the gesture "right_open" fires

    Scenario: Both hands open still means the two-handed gesture
      Given the gesture "both_open" needs "leftHandOpen" and "rightHandOpen" above 0.8 and "bothHandsPresent" above 0.5
      When both hands are scored with every finger straight
      Then the gesture "both_open" fires
      And the gesture "right_open" does not fire

  Rule: Left and right are the user's, not the frame's

    An unmirrored webcam sees a raised right hand on the left of the frame. A configuration author
    means the physical right hand, and exactly one constant resolves that.

    Scenario: A finger travelling toward the frame's left edge points to the user's right
      When a hand is scored with the index finger pointing toward the frame's left edge
      Then the score "handPointingRight" is 1.0
      And the score "handPointingLeft" is 0.0

    Scenario: Up the frame is up
      When a hand is scored with the index finger pointing up the frame
      Then the score "handPointingUp" is 1.0
      And the score "handPointingDown" is 0.0

  Rule: The thumb is not a finger

    Scenario: A fist is a fist whether the thumb is inside or outside it
      When a hand is scored with the fingers folded and the thumb extended
      And a hand is scored with the fingers folded and the thumb folded
      Then the score "handFist" is the same for both

    Scenario: The thumb is scored against how far a thumb can fold
      # A thumb cannot fold as far as a finger, so `handThumbCurl: {min: 0.8}` has to mean as
      # folded as a thumb reaches. Sharing the fingers' scale would make it unreachable.
      When a hand is scored with every joint bent the same amount
      Then the score "handThumbCurl" is well above "handIndexCurl"

  Rule: No hand means no score

    @accessibility-critical
    Scenario: A hand out of frame reports nothing rather than zero
      # Absent rather than zero, because a `max` condition MUST NOT hold on a hand that is not
      # present, or a gesture would fire as soon as the hands left the frame
      When no hand is scored
      Then no hand score is reported

    Scenario: A hand leaving the frame re-arms a gesture it was holding
      Given a hand was scored with every finger folded and "fist" fired
      When no hand is scored
      And a hand is scored with every finger folded
      Then the gesture "fist" fires

    Scenario: A partial hand is a dropped hand
      When a hand is scored with fewer than 21 landmarks
      Then no hand score is reported

    Scenario: A dropped hand does not hide a good one
      When a partial hand and a complete hand are scored together
      Then the score "handPresent" is 1.0
      And the score "bothHandsPresent" is 0.0

  Rule: A score never leaves the unit range

    A score stays between 0 and 1 whatever the hand does, because a threshold is written against
    that range and a value outside it would make a `max` condition unsatisfiable.

    Scenario Outline: Extremes stay in range
      When a hand is scored <shape>
      Then every score is between 0.0 and 1.0

      Examples:
        | shape                              |
        | with every finger straight         |
        | with every finger folded past flat |
        | with the fingers pressed together  |
        | with the fingers splayed wide      |
        | collapsed to a single point        |

    Scenario: A hand collapsed to one point scores without raising
      # Landmarks can arrive degenerate, and the scoring path runs on the camera thread, where
      # rule 2 states that nothing may raise
      When a hand is scored collapsed to a single point
      Then no error is raised

  Rule: Where a score's ends sit is calibration, not code

    @tinkerer
    Scenario: The calibration decides how far a finger must fold to read as folded
      Given a hand bent halfway
      When it is scored against a calibration that folds easily
      And it is scored against a calibration that folds hard
      Then the first reports the higher curl

    Scenario: A calibration whose two ends meet scores zero rather than dividing by zero
      Given a calibration whose pinch is open and closed at the same distance
      When a hand is scored
      Then the score "handPinchIndex" is 0.0

  @design-decision
  Rule: Hand gestures share the gesture layer

    There is no fourth input layer for hands. A single gesture can name a hand feature and a
    facial feature at once, so a hand gesture is not a category on which a binding table could be
    split, and a `hand:` layer would need an arbitrary rule for which table a mixed gesture reads
    from. Applying hand bindings across modes is already the purpose of an import template.

    Scenario: A hand gesture is bound in the same block as a facial one
      Given the mode "root mode" maps the gesture "fist" to "left_mouse"
      And it maps the facial gesture "pucker" to "right_mouse"
      When the gesture "fist" fires
      Then the mouse sequence "left_mouse↓ left_mouse↑" is emitted

    Scenario: Moving the gesture layer moves hand bindings with it
      Given the gesture layer is in mode "root mode"
      When a response runs "mode(gesture:other mode)"
      Then the gesture "fist" fires whatever "other mode" binds it to

    Scenario: A template makes hand bindings apply in every mode
      # This is how a gesture that applies in every mode is written, with no new layer
      Given a template "::hands" maps the gesture "pinch" to "left_mouse"
      And every mode imports "::hands"
      When the gesture "pinch" fires in any mode
      Then the mouse sequence "left_mouse↓ left_mouse↑" is emitted

  Rule: The hand model runs below the frame rate, and its scores stand in between

    The hand model costs about 11.5 ms a frame against the face model's 2.3, and a 60 fps loop
    has 16.7 ms in total. `ht_hand_stride` samples it every *n*th frame — 15 Hz at the default,
    which reads as instant for a shape held 200–400 ms.

    Scenario: The first frame is landmarked
      Given "ht_hand_stride" is 4
      When the first frame arrives
      Then the hand model is asked about it

    Scenario: The frames in between are skipped
      Given "ht_hand_stride" is 4
      When eight frames arrive
      Then the hand model was asked about two of them

    Scenario: A stride of one landmarks every frame
      Given "ht_hand_stride" is 1
      When five frames arrive
      Then the hand model was asked about five of them

    Scenario: The last scores stand between strided frames
      # Otherwise a gesture mixing a hand feature with a face feature, and any `hold` on it,
      # would see the hand disappear in three frames out of four
      Given "ht_hand_stride" is 4
      And a fist was scored on the last landmarked frame
      When the next frame arrives without being landmarked
      Then "handFist" still reads what it read

    @accessibility-critical
    Scenario: The pointer is not slowed by the second model
      # Pointer speed is expressed per second, so an 11 ms delay arrives as a longer frame
      # interval and the pointer travels exactly as far. This is what made one thread sufficient.
      Given the pointer is moving at a steady head angle
      When hand tracking is switched on
      Then the pointer covers the same distance per second

  Rule: One mapping, one recognizer

    Scenario: A gesture spanning both models fires
      Given the gesture "shout" needs "handFist" above 0.6 and "jawOpen" above 0.5
      When a fist is scored and "jawOpen" reads 0.7
      Then the gesture "shout" fires

    Scenario: Half of it is not enough
      Given the gesture "shout" needs "handFist" above 0.6 and "jawOpen" above 0.5
      When "jawOpen" reads 0.7 with no hand in frame
      Then the gesture "shout" does not fire

    @accessibility-critical
    Scenario: A hand gesture fires with the face out of frame
      # The pointer needs a face, whereas a hand gesture does not, and requiring one would mean
      # facing the camera in order to click
      Given no face is detected
      When a fist is scored
      Then the gesture "fist" fires

  Rule: One place turns mediapipe's handedness label into a side

    Mediapipe's documentation states that the label assumes a mirrored image, which with an
    unmirrored capture would make its "Left" the physical right hand. Measured against a real
    camera, that is not the case: the label already names the physical side. Exactly one constant
    resolves this, because getting it backwards is silent, and a `leftHandFist` that fires for the
    right hand appears to be a working feature.

    Scenario: The label already names the user's own side
      When the landmarker reports one hand labelled "Left"
      Then its scores are reported under "leftHand*"

    Scenario: A label naming no side stays unrecognised
      When the landmarker reports a hand labelled something that is neither side
      Then the hand still scores
      And it claims neither side

  Rule: A dead hand model costs hand gestures and nothing else

    Rule 2 applies. The camera, the pointer and every facial gesture are on the other side of
    this boundary.

    @accessibility-critical
    Scenario: A hand model that will not load leaves the pointer alone
      Given "ht_hand_model_path" points at a file that is not a model
      When the session starts
      Then an error is logged saying head tracking is unaffected
      And the pointer still moves
      And facial gestures still fire

    Scenario: It is reported apart from the camera
      Given the hand model would not load
      Then "hand tracking" is a fault
      And "head tracking" is not

    Scenario: A model that starts failing gives up rather than retrying every frame
      Given hand scores are working
      When the hand model raises on a frame
      Then hand scores stop for the rest of the camera session
      And the camera loop continues

    Scenario: The stale scores are cleared when it gives up
      # Left in place, a score frozen mid-fist holds its gesture down indefinitely, and a
      # gesture that cannot release is worse than one that cannot fire
      Given the gesture "fist" is active
      When the hand model raises on a frame
      Then the gesture "fist" is re-armed

  @tinkerer
  Rule: A hand feature is checked at load, like every other feature

    Scenario: A misspelled hand feature is refused
      Given a gesture names the feature "handIndexPinch"
      Then loading fails suggesting "handPinchIndex"

    Scenario: A hand gesture with the hands switched off is reported, not refused
      # One configuration is carried between machines, so a gesture that is inert on a given
      # machine is legitimate to have written. The silence is nonetheless the same silence a
      # misspelling produces.
      Given "ht_hand_enabled" is off
      And a gesture names "handFist"
      Then loading succeeds
      And a warning names the gesture and "ht_hand_enabled"

  @tinkerer
  Rule: Calibration is measured, not guessed

    The ends of a score are constants derived from the hand's own geometry, and no synthetic hand
    can establish where a real folded finger lands. Nothing ships as a default gesture until they
    are measured, because a constant wrong in the same direction as a threshold gives a gesture
    that never fires, which is indistinguishable from a failed camera.

    Scenario: No hand gesture ships as a default
      Given a configuration that sets no "ht_custom_gestures"
      Then the shipped gestures are all facial

    Scenario: The probe prints the raw ratio beside every score
      # The ratio is what a constant is set from, because a score has already been normalised
      # onto 0-1 by the very constants being calibrated
      When "--hand-scores" is run
      Then each score is shown
      And the raw extension, pinch, spread and palm-area ratios are shown
      And the palm span is shown, because every other number is divided by it

    Scenario: The probe needs no session
      # Calibrating happens while the configuration file a session would be holding is open
      When "--hand-scores" is run
      Then no uinput device is opened
      And no instance claim is taken
      And no recognizer is started

    Scenario: The probe works before the hands are switched on
      # Refusing until "ht_hand_enabled" is set would be refusing in exactly the case the tool
      # exists for: calibration first, and the thresholds afterwards
      Given "ht_hand_enabled" is off
      When "--hand-scores" is run with a model path
      Then it reads hands from that model

    Scenario: With no path at all it looks beside the face model
      # Both models are published on the same page and are placed in the same directory
      Given "ht_hand_enabled" is off
      And "hand_landmarker.task" sits in the same directory as "ht_model_path"
      When "--hand-scores" is run with no arguments
      Then it reads hands from that model
      And the log says where it found it, because it is a guess

    Scenario: A configured model wins over the one it would have guessed
      Given "ht_hand_model_path" names a model
      When "--hand-scores" is run with no arguments
      Then it reads hands from the configured model

    Scenario: A path given on the command line wins over both
      Given "ht_hand_model_path" names a model
      When "--hand-scores" is run with a different path
      Then it reads hands from the path that was given
      And the other hand settings are still the configured ones

    Scenario: Finding nothing anywhere names the file it looked for
      Given "ht_hand_enabled" is off
      And there is no "hand_landmarker.task" beside the face model
      When "--hand-scores" is run with no arguments
      Then it fails naming the file and both places it looked

    Scenario: An empty frame says so rather than printing zeroes
      When no hand is in frame
      Then the probe says no hand in frame

  @tinkerer
  Rule: Calibration is shared, a threshold is not

    Correcting a calibration constant corrects every gesture built on the score it feeds, whereas
    raising one gesture's threshold corrects one gesture. When a fist reads below its threshold,
    the first question is which of the two is wrong, and the answer is the ratio behind the score,
    which is why the readings carry both.

    Scenario: The constants are set by name
      Given "ht_hand_calibration" sets "folded_extension" to 0.36
      Then every curl score is measured against 0.36 instead of the default
      And "handFist" over four folded fingers moves with it
      And a constant left out keeps its default

    Scenario: A name that is not a constant is a load error
      # A misspelling there would silently leave the default in place, and the gesture built on
      # it would continue not to fire for a reason no longer present in the file
      Given "ht_hand_calibration" sets "folded_extention"
      Then the configuration is refused, listing the constants

    @accessibility-critical
    Scenario: A pair whose ends coincide is refused
      # It pins the score it feeds at zero, and every gesture over that score falls silent in
      # the one way that is indistinguishable from a failed camera
      Given "ht_hand_calibration" sets "pinch_closed" and "pinch_open" to the same value
      Then the configuration is refused, saying which score it would pin

    Scenario: The probe and the session score the same way
      # A calibration the probe ignored would print values from a different scoring than the one
      # that is failing to fire
      Given "ht_hand_calibration" is set
      When "--hand-scores" is run
      Then it applies the same constants the session does
