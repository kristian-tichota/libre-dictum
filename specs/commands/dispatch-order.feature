Feature: Dispatch order for recognized speech

  Every recognized utterance runs down one ladder of checks, and the first match wins. The order
  is load-bearing: it decides whether "reload config" reloads or gets typed, and whether a mode
  name switches modes or reaches the command table.

  The ladder, in order:

    1. the panic phrase        — always, from any mode, before anything else
    2. the reload phrase       — always, from any mode
    3. the previous-mode phrase
    4. a mode name             — any mode name is a switch phrase
    5. the display's phrases   — show and hide the sheet, toggle the chip, open a group
    6. banned strings          — recognized, deliberately ignored
    7. the active mode's command table — first matching pattern wins
    8. no match                — dropped

  Background:
    Given the configuration:
      """
      {
        "reload_command": "reload config",
        "panic_command": "release",
        "previous_mode_keyword": "previous mode",
        "starting_mode": "root mode",
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en",
            "banned_strings": ["thank you"],
            "commands": {
              "open terminal": "alt + enter",
              "dictate": "f2"
            }
          },
          "dictate": {
            "type": "transformer", "model_name": "whisper-turbo",
            "commands": { "{rest}": "write({1})" }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: The panic phrase outranks everything

    @accessibility-critical
    Scenario: The panic phrase releases everything that is held
      Given the keys "ctrl" and "shift" are held
      When I say "release"
      Then the key sequence "ctrl↑ shift↑" is emitted
      And no key is held

    @accessibility-critical
    Scenario: The panic phrase works from a dictation mode
      Given the active mode is "dictate"
      And the key "ctrl" is held
      When I say "release"
      Then the key sequence "ctrl↑" is emitted
      And no text is typed

    @accessibility-critical @design-decision
    Scenario: A command the panic phrase would shadow is rejected at load
      # The phrase is handled before the command table, so the command could never fire.
      # configuration/validation.feature has the error message.
      Given mode "root mode" has the command "release" mapped to "esc"
      When the configuration is loaded
      Then loading fails with an error mentioning "panic_command"

    Scenario: A saved value does not survive the panic phrase
      When I say "release"
      Then no saved variable remains

  Rule: The reload phrase outranks mode names and commands

    Scenario: The reload phrase is not treated as dictation
      Given the active mode is "dictate"
      When I say "reload config"
      Then the reload is reported as applied
      And no text is typed

  Rule: Any mode name is a switch phrase, ahead of the command table

    @design-decision
    Scenario: A mode name wins over a command with the same pattern
      # Accepted consequence: a command can be shadowed by a mode of the same name.
      When I say "dictate"
      Then the active mode is "dictate"
      And the command "f2" is not executed

    @design-decision
    Scenario: A dictation mode cannot type a mode name
      # Accepted consequence of keeping bare mode names as switch phrases. Config authors
      # should choose mode names unlikely to appear in dictated prose.
      Given the active mode is "dictate"
      When I say "root mode"
      Then the active mode is "root mode"
      And no text is typed

    Scenario: A phrase containing a mode name is not a switch
      # Matching is whole-utterance, so only an exact mode name switches
      Given the active mode is "dictate"
      When I say "the root mode is fine"
      Then the active mode is still "dictate"
      And the text "the root mode is fine" is typed

    Scenario: The previous-mode phrase switches back
      # closes C9 — the phrase is not a mode name, so dispatch never routes it today, and it is
      # absent from the VOSK vocabulary so it cannot even be recognized
      Given the active mode is "root mode"
      When I say "dictate"
      And I say "previous mode"
      Then the active mode is "root mode"

    @vosk
    Scenario: The previous-mode phrase is in the vocabulary
      # closes C9
      Then the recognizer vocabulary for mode "root mode" includes "previous mode"

    Scenario: The previous-mode phrase at startup is a no-op
      Given the app has just started
      When I say "previous mode"
      Then the active mode is still "root mode"

  Rule: The display is asked before the command table

    # The phrase reached for *because* a command cannot be remembered must not be
    # swallowed by the command table being looked up. Nothing is lost to it: the
    # four configured phrases are reserved like the reload phrase, and a configuration
    # whose generated group phrases collide with a command is refused at load. See
    # discoverability/navigating-by-voice.feature.

    Scenario: The sheet opens rather than reaching the command table
      When I say "overlay open"
      Then no input is emitted
      And the sheet is open at the index

    Scenario: A group phrase opens that group
      When I say "open misc"
      Then no input is emitted
      And the sheet is open at the group "misc"

    @accessibility-critical
    Scenario: The panic phrase still comes first
      Given the key "shift" is held
      When I say "release"
      Then the key sequence "shift↑" is emitted

    Scenario: A mode name still comes first
      # "dictate" is both a mode name and a command in this configuration, and the mode
      # wins. Adding the display below mode names does not change that.
      When I say "dictate"
      Then the active mode is "dictate"

  Rule: Banned strings are recognized and dropped

    Banned strings exist to absorb speech the recognizer reliably mishears into a command. They
    are checked after mode names so a banned string can never make a mode unreachable.

    Scenario: A banned string produces no input
      When I say "thank you"
      Then no input is emitted
      And the utterance is logged as ignored

    Scenario: A banned string is only banned in the mode that declares it
      Given the active mode is "dictate"
      When I say "thank you"
      Then the text "thank you" is typed

    Scenario: A mode name that is also a banned string still switches
      Given mode "root mode" bans the string "dictate"
      When I say "dictate"
      Then the active mode is "dictate"

  Rule: The first matching command pattern wins

    Scenario: Patterns are tried in declaration order
      Given mode "root mode" has commands in this order:
        | pattern      | response |
        | press {any}  | {1}      |
        | press enter  | f1       |
      When I say "press enter"
      Then the key sequence "enter↓ enter↑" is emitted
      And the command "f1" is not executed

    Scenario: A more specific pattern must be declared first to win
      Given mode "root mode" has commands in this order:
        | pattern      | response |
        | press enter  | f1       |
        | press {any}  | {1}      |
      When I say "press enter"
      Then the key sequence "f1↓ f1↑" is emitted

  Rule: Unmatched speech is dropped without side effects

    Scenario: Unrecognized speech in a command mode does nothing
      When I say "what is the weather"
      Then no input is emitted
      And the utterance is logged at debug level

    @design-decision
    Scenario: Unmatched speech does not clear saved variables
      # A misheard word between "number five" and "page down" should not silently drop the count
      Given a saved variable "5"
      When I say "what is the weather"
      Then the saved variable "5" remains
