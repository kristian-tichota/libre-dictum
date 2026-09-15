Feature: Switching modes

  A mode bundles a recognizer, a command table, aliases, banned strings, gestures and timing.
  Switching between them is how one voice interface covers both issuing commands and dictating
  prose.

  A switch is a handover, in a fixed order: the outgoing mode's exit command runs, the mode
  changes, then the incoming mode's enter command runs. Enter and exit commands are what let a
  mode leave the target application in a sensible state — dismissing a completion popup before
  dictating into it, say.

  Background:
    Given the configuration:
      """
      {
        "previous_mode_keyword": "previous mode",
        "starting_mode": "root mode",
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en", "icon": [0, 255, 0],
            "commands": { "open terminal": "alt + enter" }
          },
          "dictate": {
            "type": "transformer", "model_name": "whisper-turbo", "icon": [255, 0, 0],
            "enter_command": "esc",
            "exit_command": "ctrl + s",
            "commands": { "{rest}": "write({1})" }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: A switch is a handover in a fixed order

    Scenario: Entering a mode runs its enter command
      When I say "dictate"
      Then the active mode is "dictate"
      And the key sequence "esc↓ esc↑" is emitted

    Scenario: Leaving a mode runs its exit command
      Given the active mode is "dictate"
      When I say "root mode"
      Then the key sequence "ctrl↓ s↓ s↑ ctrl↑" is emitted
      And the active mode is "root mode"

    Scenario: The exit command runs before the enter command
      Given mode "root mode" has an enter_command of "f1"
      And the active mode is "dictate"
      When I say "root mode"
      Then the key sequence "ctrl↓ s↓ s↑ ctrl↑ f1↓ f1↑" is emitted

    Scenario: The outgoing mode stops listening before the handover
      When I say "dictate"
      Then mode "root mode" is disabled
      And mode "dictate" is enabled

    Scenario: An enter command may itself switch modes
      Given mode "dictate" has an enter_command of "mode(root mode)"
      When I say "dictate"
      Then the active mode is "root mode"

    Scenario: A switch to the already-active mode is a no-op
      # Running exit then enter against the same mode emits stray keystrokes for no reason
      Given the active mode is "dictate"
      When I say "dictate"
      Then the active mode is still "dictate"
      And no input is emitted

  Rule: Switching does not restart audio

    Scenario: Both streams keep capturing across a switch
      When I say "dictate"
      Then the audio stream for mode "root mode" is still running
      And the audio stream for mode "dictate" is still running

    Scenario: A switch takes effect immediately
      When I say "dictate"
      Then no model is loaded
      And the switch completes without waiting on the recognizer

  Rule: The previous mode is remembered

    Scenario: The previous-mode phrase returns to the previous mode
      # closes C9
      When I say "dictate"
      And I say "previous mode"
      Then the active mode is "root mode"

    Scenario: The previous-mode phrase alternates
      # closes C9
      When I say "dictate"
      And I say "previous mode"
      And I say "previous mode"
      Then the active mode is "dictate"

    Scenario: A refused switch does not change the remembered mode
      # closes C9
      Given a mode "broken" that failed to load
      When I say "dictate"
      And I say "broken"
      And I say "previous mode"
      Then the active mode is "root mode"

  Rule: A refused switch changes nothing

    @accessibility-critical
    Scenario: Switching to an unknown mode leaves the session intact
      When I say "mode that does not exist"
      Then the active mode is still "root mode"
      And no input is emitted
      And mode "root mode" is still enabled

    @accessibility-critical
    Scenario: An exit command that fails does not abandon the switch
      # Otherwise a bad exit command traps the user in one mode with no hands-free way out
      Given mode "dictate" has an exit_command of "script(explode)"
      And the script "explode" raises an exception when called
      And the active mode is "dictate"
      When I say "root mode"
      Then an error is logged naming the script "explode"
      And the active mode is "root mode"

    @accessibility-critical
    Scenario: Switching to a mode whose model failed to load is refused
      Given mode "dictate" failed to load its model
      When I say "dictate"
      Then the active mode is still "root mode"
      And a warning is logged explaining that mode "dictate" is unavailable

  Rule: A switch resynchronises everything mode-scoped

    Scenario: The tray follows the active mode
      When I say "dictate"
      Then the tray icon shows the "dictate" colour

    Scenario: The new mode's aliases and banned strings take effect
      Given mode "root mode" bans the string "thank you"
      And mode "dictate" bans nothing
      When I say "dictate"
      And I say "thank you"
      Then the text "thank you" is typed

    @head-tracking
    Scenario: The new mode's head-tracking settings take effect
      Given mode "root mode" has ht_enabled true
      And mode "dictate" has ht_enabled false
      When I say "dictate"
      And my head yaws 20 degrees
      Then the pointer does not move

    @design-decision
    Scenario: Held keys are not cleared by a switch
      # Held state belongs to the virtual keyboard, not to a mode. See
      # input/hold-release-toggle.feature; the panic phrase is the escape hatch.
      Given the key "shift" is held
      When I say "dictate"
      Then the key "shift" is held
