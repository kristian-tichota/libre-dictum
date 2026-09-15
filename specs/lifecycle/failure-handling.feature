@accessibility-critical @design-decision
Feature: Degrading instead of terminating

  For a user who cannot fall back to a keyboard, the least acceptable outcome is the program
  terminating, and the next least acceptable is the program appearing to work while a subsystem has
  silently stopped. Both occur today: background threads raise into nothing, and the main thread
  waits on input() without detecting it.

  The rule adopted here has two halves:

    Nothing a subsystem does can end the process. A dead webcam must never take voice control
    down with it.

    No failure is silent. Every failure is logged with enough detail to act on, and shown on the
    tray so a user who is not watching a terminal can see that something is wrong.

  A separate question is what a failure means for *starting up*: a subsystem that cannot
  initialise at all is a configuration problem and worth reporting loudly, but it still must not
  prevent the working subsystems from running.

  Rule: A camera failure costs only head tracking

    @head-tracking
    Scenario: The camera cannot be opened at startup
      Given head tracking is enabled
      And no video device will deliver a frame
      When the app starts
      Then an error is logged naming every video device it tried
      And the tray icon shows the fault colour
      And speech recognition is still working
      And the app keeps running

    @head-tracking
    Scenario: The camera is unplugged mid-session
      # closes C7 — the worker thread terminates silently today and the pointer stops moving
      Given head tracking is running
      When the camera stops returning frames
      Then an error is logged mentioning the camera
      And the tray icon shows the fault colour
      And speech recognition is still working

    @head-tracking
    Scenario: The camera rejects the requested capture format
      # closes the known webcam bug: MJPG/1920x1080/60fps is hardcoded today and some devices
      # reject it, which looks to the user like the feature is broken
      Given the camera does not support the configured capture format
      When head tracking starts
      Then a warning is logged naming the requested and the accepted format
      And head tracking runs at the format the camera accepted

    @head-tracking
    Scenario: The face landmarker model is missing from disk
      Given the configured ht_model_path does not exist
      When the app starts
      Then loading fails with an error naming the path
      And the app exits cleanly with a non-zero status
      # A missing model is a configuration error, caught before any subsystem starts

  Rule: A recognizer failure costs only that mode

    Scenario: A recognizer thread raises
      # closes C3 and C7 — an unhandled exception on a recognizer thread deafens that mode for
      # the remainder of the session with nothing logged and nothing shown
      Given the app is running with modes "root mode" and "dictate"
      When the recognizer thread for mode "root mode" raises an unhandled exception
      Then an error is logged naming mode "root mode"
      And the error includes a traceback
      And the tray icon shows the fault colour
      And mode "dictate" still recognizes speech

    Scenario: A failed mode reports itself rather than appearing to work
      Given the recognizer for mode "root mode" has failed
      And the active mode is "dictate"
      When I say "root mode"
      Then the active mode is still "dictate"
      And a warning is logged explaining that mode "root mode" is unavailable

    Scenario: One mode's missing model does not block the others
      Given mode "dictate" names a model that cannot be loaded
      When the app starts
      Then an error is logged naming mode "dictate"
      And mode "root mode" recognizes commands normally
      And the app keeps running

    Scenario: An audio device that disappears is reported
      When the audio input device is removed
      Then an error is logged mentioning the audio device
      And the tray icon shows the fault colour
      And the app keeps running

  Rule: A user action that fails does not damage the session

    Scenario: A script raising leaves input state clean
      Given the command "boom" mapped to "hold(shift) + script(explode)"
      And the script "explode" raises an exception when called
      When I say "boom"
      Then an error is logged naming the script "explode"
      And the recognizer thread keeps running

    Scenario: A command that cannot be executed emits nothing
      Given a response that fails validation reaches execution
      Then nothing is emitted
      And no key is left held

  Rule: Faults are visible without watching a terminal

    Scenario: A fault is shown on the tray
      Given the tray is enabled
      When any subsystem fails
      Then the tray icon shows the fault colour

    Scenario: The fault indication persists until resolved
      Given a subsystem has failed
      When the active mode changes
      Then the tray icon still shows the fault colour

    Scenario: A successful reload clears the fault indication
      Given a subsystem has failed
      When a reload succeeds and every subsystem is healthy
      Then the tray icon shows the active mode's colour

    Scenario: The app can be asked what is wrong
      Given head tracking has failed
      When the health of the app is queried
      Then the report names "head tracking" as failed
      And the report names the working subsystems

  @design-decision
  Rule: Recovery from a transient failure

    Confirmed: a failure that is plausibly transient — a camera that will not open or that
    stops delivering frames — is retried a bounded number of times with a growing delay, and
    then given up on loudly. Retrying for ever would keep a thread and a log line alive for
    the rest of the session; not retrying at all would leave a user whose webcam glitched with
    a dead pointer.

    Giving up is not the end of it: applying a reload restarts head tracking, so the reload
    phrase is a hands-free way back. A configuration error is never retried, because it cannot
    start working on its own.

    @head-tracking
    Scenario: A camera that comes back within the retry window is picked up again
      Given head tracking has failed because the camera was unplugged
      When the camera becomes available again
      Then head tracking resumes
      And the tray icon shows the active mode's colour
      And the recovery is logged

    @head-tracking
    Scenario: Retries back off and then stop
      Given the camera cannot be opened
      When head tracking retries
      Then the retry interval grows on each attempt
      And retrying stops after a bounded number of attempts
      And the final give-up is logged

    @head-tracking @accessibility-critical
    Scenario: The reload phrase restarts head tracking that gave up
      Given head tracking gave up on the camera
      And the camera is available again
      When I say "reload config"
      Then head tracking is running again
      And the tray icon shows the active mode's colour

    Scenario: A configuration error is not retried
      Given the configured ht_model_path does not exist
      Then no retry is attempted
