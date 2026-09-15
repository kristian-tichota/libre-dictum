Feature: Shutting down

  The app owns two virtual input devices, one audio stream per mode, a camera, a face
  landmarker and a tray icon. All of it has to be released, and any key still held has to be
  let go — a process that exits with ctrl held down leaves the user's desktop broken.

  Today none of this happens: shutdown is an input() call followed, on EOF, by an infinite
  sleep, with no signal handling and no cleanup at all.

  Rule: Shutdown is requested, not stumbled into

    Scenario: Interrupt requests a clean shutdown
      # closes S5
      Given the app is running
      When SIGINT is received
      Then the app shuts down cleanly
      And the exit status is zero

    Scenario: Terminate requests a clean shutdown
      # closes S5 — needed to run under a service manager at all
      Given the app is running
      When SIGTERM is received
      Then the app shuts down cleanly

    Scenario: The app runs without a terminal attached
      # closes S5 — the current EOFError fallback loops forever on sleep(3600), which is not a
      # runnable state for a background service
      Given the app is started with no controlling terminal
      When the app starts
      Then the app keeps running
      And the app responds to SIGTERM

    Scenario: A voice command can request shutdown
      # An accessibility user needs a hands-free way to stop the app
      Given the command "quit voice control" mapped to "exec(pkill libre-dictum)"
      When I say "quit voice control"
      Then the app shuts down cleanly

  Rule: A shutdown asked for during startup is still a shutdown

    A start is the longest thing this application does -- four models, one of them
    multi-gigabyte, then a camera and a tray. A signal arriving in the middle of it used
    to be queued up behind the rest: the session loaded every remaining model, opened the
    camera, bound the display socket, logged "Ready", and only then shut down. Decision
    13 says a shutdown opens no device. Asserted in
    tests/lifecycle/test_startup.py::TestAShutdownThatArrivesDuringStartup.

    @accessibility-critical
    Scenario: Terminating mid-startup stops loading rather than finishing first
      Given the app is starting and the first model has loaded
      When the app is asked to shut down
      Then no further model is loaded
      And no mode is activated
      And every device that was opened is closed

    @accessibility-critical
    Scenario: Terminating during the last model activates nothing either
      # The cancel was only checked before each model, so the final one -- the slowest,
      # the transformer -- had no check after it, and the mode switch happened anyway.
      Given the app is starting and every model but the last has loaded
      When the app is asked to shut down
      Then no mode is activated

    Scenario: A startup that raises still releases the devices
      # `run()` MUST call `start()` inside its try/finally, or a camera that is not there
      # leaves /dev/uinput open behind it.
      Given head tracking raises while the app is starting
      When the app is run
      Then the failure propagates
      And the input devices are closed

  Rule: Held keys are released before exit

    @accessibility-critical
    Scenario: A held modifier is released on shutdown
      # closes S5 — exiting with ctrl held leaves every subsequent keystroke on the desktop
      # modified, with no running process to fix it
      Given the key "ctrl" is held
      When the app shuts down
      Then the key sequence "ctrl↑" is emitted
      And no key is held

    @accessibility-critical
    Scenario: Every held key is released
      Given the keys "ctrl", "shift" and "space" are held
      When the app shuts down
      Then all three keys are released before the input devices are closed

    @accessibility-critical
    Scenario: Keys are released even when shutdown is triggered by a failure
      Given the key "ctrl" is held
      When the app exits because of an unrecoverable error
      Then the key sequence "ctrl↑" is emitted

  Rule: Every resource is released

    Scenario Outline: Resources are closed on shutdown
      # closes S5 — none of these are released today
      Given the app is running with head tracking and the tray enabled
      When the app shuts down
      Then <resource> is released

      Examples:
        | resource                          |
        | the virtual keyboard device       |
        | the virtual mouse device          |
        | every mode's audio input stream   |
        | the camera                        |
        | the face landmarker               |
        | the tray icon                     |

    Scenario: Worker threads are joined, not abandoned
      Given the app is running
      When the app shuts down
      Then every recognizer thread has stopped
      And the head tracking thread has stopped

    Scenario: A thread that will not stop does not hang shutdown
      Given a recognizer thread is blocked
      When the app shuts down
      Then a warning is logged naming the thread
      And the app exits within a bounded time

    @transformer
    Scenario: An in-flight transcription is abandoned
      Given a dictation chunk is being transcribed
      When the app shuts down
      Then the transcription is abandoned
      And no text is typed

  Rule: Shutdown is idempotent

    Scenario: A second interrupt during shutdown forces exit
      Given the app is shutting down
      When SIGINT is received again
      Then the app exits immediately

    Scenario: Cleanup runs once
      When the app shuts down
      Then each resource is released exactly once
      And no error is logged about a resource already closed
