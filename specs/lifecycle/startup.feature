Feature: Starting up

  Startup validates everything it can before touching hardware, then brings up one recognizer
  per mode, then head tracking, then the tray. Ordering matters: a config mistake should be
  reported before the user has waited two minutes for models to load.

  All modes' recognizers are started, not just the active one. Switching modes later only flips
  which one is listening, so a switch is instant rather than a model reload.

  Rule: Validation precedes any expensive or privileged work

    @tinkerer
    Scenario: A config error is reported before models are loaded
      Given the configuration names a nonexistent gesture in a mode
      When the app starts
      Then loading fails with an error naming the mode
      And no speech model was loaded
      And no camera was opened

    @accessibility-critical
    Scenario: Missing permission on the input device is reported clearly
      Given the app does not have write access to /dev/uinput
      When the app starts
      Then an error is logged mentioning "/dev/uinput"
      And the error explains that membership of the "input" group is usually required
      And the app exits cleanly with a non-zero status

    Scenario: The package can be imported without an input device
      # closes S3 — importing input_handler opens two UInput devices at module import time
      # today, so the package cannot be imported, tested, or introspected without permission
      Given the app does not have write access to /dev/uinput
      When the libre_dictum package is imported
      Then the import succeeds
      And no input device was opened

    Scenario: The package can be imported without a sound library
      # sounddevice raises as soon as it is imported when PortAudio is absent, and both
      # recognizers import it — so an eager import in libre_dictum.audio took the voice
      # activity chunker, the grammar builder and the partial tracker down with it, and
      # the suite could not be collected at all. Invisible on a machine that has PortAudio
      Given PortAudio is not installed
      When the libre_dictum package is imported
      Then the import succeeds
      And the app does not require PortAudio until a recognizer is built

  Rule: One session per user, refused before anything is opened

    Two cores on one machine is not a degraded session. Both hold /dev/uinput and both
    listen, so **every command types twice**, and on the way there they contend for the
    GPU, the camera and the display socket -- which is how it was found: a CUDA
    out-of-memory loading a model, a camera reporting EBUSY, and a socket already taken,
    with nothing in any of the three saying "there are two of me".

    Asserted in tests/lifecycle/test_instance.py.

    @accessibility-critical
    Scenario: A second core refuses to start rather than typing everything twice
      Given a session is already running
      When another one is started
      Then it exits with a message naming the first one's pid
      And it says that two of them would type every command twice
      And it opened no input device and loaded no model

    Scenario: The claim is taken before the devices and released after them
      # The claim is the door that keeps a second core out of /dev/uinput, so it may not
      # open while the devices are still there to be taken.
      Given a session is running
      When it shuts down
      Then the input devices are closed before the claim is released

    Scenario: A crash leaves no lock to clear by hand
      # flock, not a pid file: the kernel drops it when the process dies, SIGKILL
      # included. Somebody whose only input device is their voice cannot be asked to
      # delete a file before their voice control will start.
      Given a session was killed without shutting down
      When another one is started
      Then it starts

    Scenario: A rejected configuration does not lock the next attempt out
      # The most likely thing to happen right after the claim, and the one the user fixes
      # and immediately tries again.
      Given the configuration is invalid
      When the app is started
      Then it reports the configuration error
      And a second attempt is not refused as a duplicate

  Rule: Every mode gets a recognizer, started up front

    Scenario: All modes are listening, only the active one acts
      Given the configuration defines modes "root mode" and "dictate"
      When the app starts
      Then the audio stream for mode "root mode" is running
      And the audio stream for mode "dictate" is running
      And mode "root mode" is enabled
      And mode "dictate" is disabled

    Scenario: The starting mode is enabled and shown
      Given the configuration sets "starting_mode" to "dictate"
      When the app starts
      Then the active mode is "dictate"
      And the tray icon shows the "dictate" colour

    Scenario: The starting mode's enter command runs
      Given mode "root mode" has an enter_command of "esc"
      And the configuration sets "starting_mode" to "root mode"
      When the app starts
      Then the key sequence "esc↓ esc↑" is emitted

  Rule: Optional subsystems start only when configured

    Scenario: The tray is not started when disabled
      Given the configuration sets "enable_systray" to false
      When the app starts
      Then no tray icon is created
      And the app does not require pystray

    Scenario: Head tracking is not started when disabled
      Given the configuration sets "enable_head_tracking" to false
      When the app starts
      Then no camera is opened
      And the app does not require mediapipe

    @head-tracking
    Scenario: Head tracking starts when enabled
      Given the configuration enables head tracking with a valid model path
      When the app starts
      Then the camera is capturing
      And the face landmarker is loaded

    Scenario: A missing optional dependency is reported by name
      Given the configuration sets "enable_systray" to true
      And pystray is not installed
      When the app starts
      Then an error is logged mentioning "pystray"
      And the error names the extra to install
      And speech recognition still works

  Rule: The scripts directory becomes importable

    @tinkerer
    Scenario: Scripts are importable from the config directory
      Given the script file ~/.config/libre-dictum/scripts/copy.py exists
      When the app starts
      Then the module "scripts.copy" can be imported

    Scenario: The scripts directory is created if absent
      Given no directory exists at ~/.config/libre-dictum/scripts
      When the app starts
      Then the directory is created
      And the app does not crash

  Rule: Startup reports what it brought up

    @accessibility-critical
    Scenario: The app states its ready state
      When the app starts successfully
      Then the active mode is logged
      And the number of loaded modes is logged
      And the enabled optional subsystems are logged

    Scenario: Startup progress is visible while models load
      # A user waiting on a multi-gigabyte model with no output cannot tell the app from a hang
      Given a mode using a large model
      When the app starts
      Then the model being loaded is logged before loading begins
