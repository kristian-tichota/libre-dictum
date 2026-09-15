@head-tracking
Feature: Measuring where a hand threshold belongs

  Every hand threshold, and the seven calibration constants underneath them, was reasoned about
  rather than measured. A constant wrong in the same direction as a threshold gives a gesture that
  never fires, and that is indistinguishable from a broken camera — which is why no hand gesture
  ships as a default.

  Measuring them needs several repetitions of every gesture, the approach into each one, and a
  stretch of hands doing nothing on purpose. Ten minutes of that at 60 fps is forty thousand
  frames, and no threshold can be chosen by reading forty thousand of anything. The session is
  therefore recorded once and reduced afterwards, and a candidate configuration is tried against the
  recording rather than against another ten minutes of holding shapes up to a camera.

  For the accessibility user this has to be performable with no keyboard and no timing pressure,
  because they are the person whose gestures are being tuned. For the tinkerer the output has to
  be numbers they can argue with, and a fragment they can paste.

  Rule: A take is delimited by the hand, so the session needs no controls

    The gestures are performed by raising a hand into shot and taking it away again. That is
    already a start and an end, so the tool needs no controls, which is the difference between a
    procedure that can be run unaided and one that cannot.

    Scenario: A hand coming into shot starts a take and leaving ends it
      Given the frame has been empty long enough for the prompt to be live
      When a hand is in shot for longer than the enter grace
      Then a take is recording
      And when the hand has been gone for longer than the exit grace the take closes

    Scenario: A dropped frame does not saw one gesture into two takes
      Given a take is recording
      When the landmarker loses the hand for a single frame
      Then the take is still recording
      And the report holds one gesture rather than two halves of one

    Scenario: The take stops at the last hand, not at the grace timeout
      Given a take that ended
      Then its span runs to the last frame that had a hand in it
      And the empty frames the exit grace waited through are not part of it

    Scenario: Showing a hand and pulling it straight back out asks for the take again
      Given a hand in shot for less than the minimum length
      Then the span is not kept
      And the session says so rather than silently re-prompting, which looks like a missed hand

    Scenario: Getting up mid-session costs a wait, not a take
      Given a prompt is live and nothing is in shot
      Then the session waits indefinitely
      And nothing is recorded until a hand appears

  Rule: Stopping is safe, and starting again continues

    Ten minutes is long enough that something will interrupt it, and a procedure that has to be
    restarted from the beginning is a procedure nobody finishes.

    Scenario: An interrupted session resumes from what the file holds
      Given a recording that already holds eight takes of the plan
      When the same directory is captured again
      Then only the takes the file does not hold are asked for

    Scenario: A resumed recording says where the break was
      Given a recording that was stopped and continued
      Then the first frame after the break is marked
      And no held statistic spans the break, because the seconds either side are not adjacent
      And a replay resets its recognizer there, so a shape left active does not consume the next

  Rule: Landmarks are recorded, not scores

    A score has already been squashed onto 0–1 by the very constants the session exists to
    measure. Recording scores would pin the analysis to the calibration that was guessed before
    it, and a recalibration would mean performing everything again.

    Scenario: A recording is re-analysed under a calibration nobody had thought of
      Given a recording made while the guessed calibration was in force
      When the report is asked for with a different `ht_hand_calibration`
      Then every score is re-derived from the landmarks
      And the ratios beneath them are unchanged, because a ratio is a measurement of the hand

  Rule: A gesture is judged at the dwell it asks for

    A shape held still clears any dwell. A pinch performed as a repeated open and close is closed
    for a fraction of a second at a time, and judged at a longer one it holds nothing — so the
    report would say a working gesture never reaches its threshold.

    Scenario: A repeated pinch is measured at its own hold
      Given a gesture with a `hold` of 120 ms over a pinch performed repeatedly
      Then its block is measured over 120 ms
      And the score it reaches there is reported, not the nothing it holds for half a second

  Rule: The report proposes and the replay decides

    A per-feature statistic cannot know that a gesture's conditions must hold simultaneously, or
    that `hold` restarts its clock the moment one of them lapses. Only the recognizer knows that,
    and it is the one that will run in the session.

    Scenario: A candidate configuration is tried without performing anything again
      Given a recording and a fragment holding `ht_custom_gestures`
      When the candidate is replayed over the recording
      Then each label reports how many of its takes the matching gesture was active during
      And a gesture firing during another label's take is reported as cross-fire
      And a gesture firing during the neutral pass is reported, because nobody meant anything

    Scenario: A gesture that fires before the shape is finished is reported as such
      Given a gesture that goes active during the approach rather than during the take
      Then the take still counts as fired, because the gesture did its job
      And the gap between the take starting and the firing is reported as negative

    Scenario: The replay runs at the rate the session runs at
      Given a recording landmarked on every frame
      And a session that runs the hand model every fourth frame
      When the candidate is replayed
      Then only every fourth frame reaches the recognizer
      And a gesture too brief for that rate is reported as missed rather than as passing

  Rule: A proposal that would not load is not proposed

    The report ends in a JSON block whose whole purpose is to be pasted into `config.json`. One
    the loader refuses costs a session; one that says a constant could not be measured costs a
    sentence.

    Scenario: A pair of calibration ends that measured the same is backed out
      Given a recording in which the open and closed extremes of one score coincide
      Then that pair is left at the values it came in with
      And the report says which, and that it would not have loaded

    Scenario: A constant no label reaches keeps its existing value
      Given a recording with no pinch takes in it
      Then `pinch_closed` and `pinch_open` are left alone
      And the report names them rather than inventing a number
