@head-tracking
Feature: Measuring where a facial threshold belongs

  Every facial threshold in the live config was reasoned about rather than measured, and not one
  of the gestures carries a dwell — so each fires the instant its conditions coincide, which is
  what makes a smile ramping up asymmetrically fire a smirk on the way past.

  The hand instrument answers this shape of question already. What does not carry over is the one
  thing that made it cheap: a hand delimits its own take by arriving and leaving, and a face is in
  shot for the whole session. There is therefore no absence to segment on, no undifferentiated
  "nothing in shot" to measure a threshold against, and some of the muscles involved are not
  voluntary at all.

  For the accessibility user this has to be performable with no keyboard and no timing pressure —
  and it has to be possible to say "this face does not do that", because a facial gesture is
  personal in a way a fist is not. For the tinkerer the output has to be an `ht_custom_gestures`
  fragment they can argue with and paste.

  Rule: A take is delimited by the face leaving rest, and rest is measured first

    Blendshapes idle above zero, and at different levels per face: `mouthClose`, `browDown*`,
    `eyeBlink*` and `eyeSquint*` all sit above zero on a completely still face. A gate against an
    assumed floor would therefore be a gate against a different face.

    Scenario: The rest pass runs before anything that has to be gated
      Given a session with a rest pass and one performance
      Then the rest pass is asked for first
      And it runs on a clock, because nothing in particular delimits doing nothing
      And the idle level of every blendshape is measured from it

    Scenario: A gate opens a share of the range that feature has left above its idle level
      Given a face whose resting `eyeBlinkLeft` is 0.2
      When the prompt is a left wink
      Then the take starts once `eyeBlinkLeft` has climbed part of the way from 0.2 towards 1
      And the level that would open it is shown on screen beside the live value

    Scenario: A feature that already idles high is asked for proportionally less
      Given one eye whose resting blink is 0.30 and another whose resting blink is 0.40
      Then the gate on the second asks for a smaller absolute rise than the gate on the first
      And a flat margin would have asked the higher-idling eye for a rise it never makes,
        recording twelve seconds of trying as a face that cannot do the performance

    Scenario: A performance the model cannot see is visible immediately
      Given a prompt whose gate feature never rises far enough
      Then the screen shows the live value against the level that would open it
      And that is a diagnosis in the first ten seconds rather than six empty takes in the report

    Scenario: A gated take refuses to run before rest has been measured
      Given a recording with no rest pass in it
      When a performance is asked for
      Then the session says so and stops
      And nothing is gated against a floor nobody measured

  Rule: The gate watches the prompted performance, not the whole face

    A blink is not a gesture and it happens twenty times a minute. A gate on "any feature moved"
    would start a take on every one of them.

    Scenario: Each performance names the features that constitute an attempt at it
      Given the prompt is a smile
      Then only the two mouth-corner features open the take
      And either corner alone is enough, because no real smile is symmetric

    Scenario: An involuntary blink is discarded without a word
      Given the prompt is a left wink
      When a blink closes the eye for a fraction of a second
      Then the span is too short to keep
      And nothing is said about it, because a note every three seconds is noise not feedback

    Scenario: A real attempt that was too quick is said out loud and asked for again
      Given a span longer than a reflex but shorter than a take
      Then the session names it and re-prompts

  Rule: Sitting still gives up on a performance; walking away costs nothing

    The correct answer to a prompt may be that the face in front of the camera does not make that
    expression. Six takes of asking again is not a session that gets completed, and there is no
    keyboard available to report the fact with.

    Scenario: A face in shot and motionless skips the performance
      Given a prompt nobody can answer
      When the face sits in front of the camera doing nothing for long enough
      Then the performance is skipped
      And every remaining take of it is skipped with it
      And a resumed session does not ask for it again

    Scenario: A face that has left is not a face at rest
      Given a prompt is live and no face is in shot
      Then the session waits indefinitely
      And nothing is skipped, because getting up mid-session has to be free

  Rule: The neutral pass becomes several, because an always-visible face has a structured day

    For a hand, "no hand in shot" is most of the day. A face is always in shot, so one
    undifferentiated neutral minute would report a misfire without saying what caused it.

    Scenario: Each ordinary activity is recorded under its own label
      Given passes for resting, talking, reading, being amused and moving the head
      Then each is a column in the matrix
      And a misfire is attributed to the activity that caused it

    Scenario: Speech is measured, because this tool dictates
      Given a pass of talking out loud
      Then `jawOpen`, `mouthFunnel`, `mouthPucker` and both `mouthStretch` are measured under it
      And no mouth threshold is proposed that a minute of dictating would clear

    Scenario: A natural smile is measured against a deliberate one
      Given a pass of being amused
      Then whether `smile` separates from it is measured rather than assumed
      And that matters because `smile` is bound to a mode switch

  Rule: Scores are recorded, where the hand recording keeps landmarks

    A hand score has already been squashed onto 0–1 by the calibration the session exists to
    measure. Nothing sits between mediapipe and a blendshape: the blendshape is the primitive.

    Scenario: Every blendshape comes back under its own name
      Given a recording written as an array in the model's own feature order
      When it is read back
      Then each value is attributed to the feature it was measured on

    Scenario: A recording made against a different face model is refused
      Given a recording whose frames hold a different number of blendshapes
      Then reading it raises rather than reporting numbers about the wrong features

    Scenario: A frame the landmarker found no face in is absent, not zero
      Given a frame with no face
      Then it holds no scores at all
      And the count of such frames inside each take is reported, because a `max` condition
        would hold through every one of them if absence read as zero

    Scenario: A face recording and a hand recording are not read as each other
      Given a directory holding a face recording
      When the hand report is asked for it
      Then it says which kind is there rather than reporting a page of zeroes
      And a recording with no kind at all is a hand one, which is what every older one is

  Rule: The dwell is measured, not inherited

    A deliberate expression is held and an incidental one is not, so a longer dwell costs the
    performance a little and the distractor a lot. Where they cross is a measurement.

    Scenario: The shortest dwell at which every condition still has room is the one proposed
      Given a performance that separates from every distractor at the shortest dwell measured
      Then that dwell is what the fragment asks for

    Scenario: A longer dwell is chosen when a short one cannot separate
      Given a distractor that reaches the same level in bursts rather than holding it
      Then a dwell long enough to outlast the bursts is proposed
      And that is the answer to "how long do I have to hold this"

    Scenario: A dwell nothing measured is never invented
      Given a gesture no dwell could separate
      Then it keeps whatever dwell it came in with
      And a number nothing measured would read as an answer

  Rule: The report proposes the thresholds and the replay decides

    Scenario: A threshold sits between the worst take and the strongest rival
      Given a performance whose worst take holds a feature higher than any other label sustains
      Then the proposed `min` sits between the two, nearer the rival
      And both numbers are printed, because a threshold is an argument about two numbers

    Scenario: A release is measured up from the resting level, not down from the threshold
      Given a recording scored as each feature's rise from its own resting level
      Then `release` sits a fraction of the way up from rest towards the threshold
      And that is the one number a guessed configuration could not have had

    Scenario: The resting levels are proposed beside the thresholds measured against them
      Given a proposal read off a recording
      Then the fragment holds both `ht_face_baseline` and `ht_custom_gestures`
      And pasting one without the other would leave every facial gesture quiet at once
      And nothing at load can tell a migrated threshold from a stale one, since both are
        numbers between zero and one

    Scenario: A condition with no room at any dwell is not proposed
      Given a feature the performance never drives past what another label sustains
      Then the condition comes back exactly as it went in
      And the report names the label it collides with, and the dwell that came closest

    Scenario: A gesture the recording never asked for is passed through untouched
      Given a candidate holding a gesture with no label of that name in the recording
      Then it appears in the fragment unchanged
      And the report says why, so pasting the fragment does not silently delete it

    Scenario: A candidate is tried without performing anything again
      Given a recording and a fragment holding `ht_custom_gestures`
      When the candidate is replayed
      Then each performance reports how many of its takes the gesture of that name was active for
      And a gesture firing during another label's take is reported as cross-fire
      And a gesture firing during a pass is reported, because nobody meant anything there

    Scenario: The replay runs at the rate the session runs at
      Given the face model runs on every frame in a session
      Then every recorded frame reaches the recognizer
      And there is no stride to choose, which is where the hand tool and this one differ

  Rule: A condition is written against a score's rise from rest, not against its raw value

    A blendshape does not idle at zero and does not idle at the same level on two faces, so a raw
    threshold silently encodes the face it was measured from. This is the facial counterpart of the
    hand calibration and exists for the same reason: without it one setting is inert, and the
    symptom is a gesture that never fires.

    Scenario: The same threshold means the same thing on both sides of a face
      Given one eye that rests at 0.30 and another that rests at 0.40
      When a wink is defined as a rise of 0.35 on the closing eye
      Then either eye's wink fires on that one number
      And raw thresholds would have needed two different numbers to say the same thing

    Scenario: A face nobody measured is left exactly as it is
      Given a configuration with no resting levels in it
      Then every score reaches the conditions untouched
      And that is what every configuration written before the key existed means

    Scenario: Hand scores are not put through it
      Given a configuration naming resting levels for blendshapes
      Then a hand score reaches its conditions unchanged
      And naming a hand score as a resting level is refused at load, because a hand that is
        not in shot is absent rather than resting

    Scenario: A resting level at the top of the range is refused
      Given a resting level of 1 for some feature
      Then the configuration is refused at load
      And the reason is that nothing could clear it, so every gesture over that feature would
        go quiet in the way that looks exactly like a dead camera
