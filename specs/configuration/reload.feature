@accessibility-critical @tinkerer
Feature: Reloading configuration at runtime

  Saying the reload phrase re-reads config.json and applies it to the running session. This is
  the tinkerer's edit-test loop and the accessibility user's only hands-free way to change
  behaviour, so it has to work, and it MUST NOT be possible to break the session with it.

  Two properties govern everything here:

    Atomicity — a candidate configuration is fully parsed and fully validated before any part of
    it is applied. If anything is wrong, nothing changes.

    Completeness — applying a configuration means applying it to the recognizers too, not just to
    the in-memory tables. A VOSK recognizer's grammar is built from its mode's command set, so a
    changed command set means a rebuilt recognizer.

  Background:
    Given the configuration:
      """
      {
        "reload_command": "reload config",
        "starting_mode": "root mode",
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en", "icon": [0, 255, 0],
            "commands": { "open terminal": "alt + enter" }
          },
          "dictate": {
            "type": "transformer", "model_name": "whisper-turbo", "icon": [255, 0, 0],
            "commands": { "{rest}": "write({1})" }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: A reload applies to the recognizers, not only to the tables

    @vosk
    Scenario: A command added to the active vosk mode becomes recognizable
      # closes C1 — the Kaldi grammar is frozen at VoskStream construction today, so this is the
      # single most visible symptom: reload appears to succeed and changes nothing
      When config.json is edited to add the command "open browser" mapped to "exec(firefox)" to mode "root mode"
      And I say "reload config"
      Then the reload is reported as applied
      When I say "open browser"
      Then the command "exec(firefox)" is executed

    @vosk
    Scenario: A command removed from the active vosk mode stops firing
      # closes C1
      When config.json is edited to remove the command "open terminal" from mode "root mode"
      And I say "reload config"
      And I say "open terminal"
      Then no input is emitted

    Scenario: A newly added mode becomes switchable
      # closes C1 — the stream table is built once at startup, so the new mode has no recognizer
      # and change_active_mode refuses the switch with a warning
      When config.json is edited to add a vosk mode "numbers mode" with model path "models/small-en"
      And I say "reload config"
      And I say "numbers mode"
      Then the active mode is "numbers mode"

    Scenario: A removed mode stops being switchable
      # closes C1
      When config.json is edited to remove mode "dictate"
      And I say "reload config"
      And I say "dictate"
      Then the active mode is still "root mode"
      And a warning is logged mentioning "dictate"

  Rule: A reload disturbs no more than it must

    Rebuilding a recognizer costs a model reload and a gap in audio capture. Modes whose relevant
    settings did not change keep their existing recognizer.

    Scenario: An unrelated mode's recognizer is not rebuilt
      When config.json is edited to add the command "open browser" mapped to "exec(firefox)" to mode "root mode"
      And I say "reload config"
      Then the recognizer for mode "root mode" is rebuilt
      And the recognizer for mode "dictate" is not rebuilt

    Scenario: Changing a non-vocabulary setting does not rebuild the recognizer
      When config.json is edited to set "input_delay" to 0.05 on mode "root mode"
      And I say "reload config"
      Then no recognizer is rebuilt
      And mode "root mode" has effective setting "input_delay" of 0.05

    Scenario: The active mode survives a reload that does not touch it
      When config.json is edited to add the command "open browser" mapped to "exec(firefox)" to mode "root mode"
      And I say "reload config"
      Then the active mode is still "root mode"
      And the tray icon shows the "root mode" colour

    Scenario: Tray icons are resynchronised
      When config.json is edited to set the icon of mode "root mode" to [0, 0, 255]
      And I say "reload config"
      Then the tray icon shows the colour [0, 0, 255]

  Rule: An invalid candidate configuration changes nothing

    Scenario: Invalid JSON is rejected atomically
      # closes C1
      When config.json is replaced with:
        """
        { "modes": { "root mode": { "type": "vosk", }
        """
      And I say "reload config"
      Then an error is logged mentioning the parse location
      And the previously loaded configuration stays active
      And the active mode is still "root mode"
      And the tray icon shows the fault colour
      When I say "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: A validation failure in any mode rejects the whole reload
      # closes C1 — a partial apply would leave the app in a state matching no file on disk
      When config.json is edited to add a vosk mode "broken" with no model path
      And I say "reload config"
      Then an error is logged naming mode "broken"
      And the previously loaded configuration stays active
      And mode "broken" is not a runnable mode
      And the tray icon shows the fault colour

    Scenario: A later successful reload clears the fault indication
      When config.json is replaced with invalid JSON
      And I say "reload config"
      And the tray icon shows the fault colour
      When config.json is restored to a valid configuration
      And I say "reload config"
      Then the reload is reported as applied
      And the tray icon shows the "root mode" colour

  Rule: A reload never strands the user

    @design-decision
    Scenario: The active mode is removed by an otherwise valid configuration
      # closes C1 — today callback() dereferences cfg.modes[active_mode] on the next utterance
      # and raises an uncaught KeyError on the recognizer thread
      When config.json is edited to remove mode "root mode"
      And I say "reload config"
      Then the reload is reported as applied
      And the active mode is the new starting_mode
      And a warning is logged explaining that the active mode was removed
      And speech recognition is still working

    @design-decision
    Scenario: Held keys are released before a reload is applied
      # A reload can redefine or delete the command that would have released a held key,
      # so held state cannot be allowed to outlive the configuration that created it.
      Given the key "ctrl" is held
      When I say "reload config"
      Then the key sequence "ctrl↑" is emitted
      And no key is held

    @design-decision
    Scenario: Saved variables are discarded on reload
      Given a saved variable "5"
      When I say "reload config"
      Then no saved variable remains

    Scenario: The reload phrase itself can be changed by a reload
      When config.json is edited to set "reload_command" to "refresh settings"
      And I say "reload config"
      Then the reload is reported as applied
      When I say "refresh settings"
      Then the reload is reported as applied

  Rule: The reload phrase is always available

    @vosk
    Scenario: The reload phrase is in every vosk mode's vocabulary
      Given the active mode is "root mode"
      Then the recognizer vocabulary for mode "root mode" includes "reload config"

    Scenario: The reload phrase works from a dictation mode
      Given the active mode is "dictate"
      When I say "reload config"
      Then the reload is reported as applied
      And no text is typed
