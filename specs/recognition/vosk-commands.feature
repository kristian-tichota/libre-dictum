@vosk
Feature: Command recognition with VOSK

  A VOSK mode recognizes from a closed grammar built out of that mode's own command patterns
  plus the reserved phrases. Restricting the vocabulary is what makes recognition fast and
  reliable enough to drive a keyboard, and it is why {rest} cannot work here.

  Commands fire from *partial* results rather than waiting for an utterance to finish, because a
  keystroke that arrives after the speaker has stopped talking feels broken.

  Background:
    Given the configuration:
      """
      {
        "reload_command": "reload config",
        "panic_command": "release",
        "previous_mode_keyword": "previous mode",
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en",
            "commands": {
              "open terminal": "alt + enter",
              "press {any}": "{1}",
              "line {numeric}": "ctrl + g"
            }
          },
          "dictate": { "type": "transformer", "model_name": "whisper-turbo" }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: The grammar contains exactly the phrases the mode can act on

    Scenario: The vocabulary is built from commands, mode names and reserved phrases
      Then the recognizer vocabulary for mode "root mode" includes:
        | phrase         | why                |
        | open terminal  | command            |
        | press {any}    | command            |
        | root mode      | mode name          |
        | dictate        | mode name          |
        | reload config  | reserved           |
        | release        | reserved, panic    |
        | previous mode  | reserved           |
        | [unk]          | out-of-vocabulary  |

    Scenario: Another mode's commands are not in this mode's vocabulary
      Given mode "dictate" has the command "special phrase" mapped to "f9"
      Then the recognizer vocabulary for mode "root mode" excludes "special phrase"

    Scenario: Speech outside the vocabulary produces no command
      When I say "what is the weather like today"
      Then no input is emitted

  Rule: {numeric} is expanded into spoken digits for the grammar

    A closed grammar cannot express "a number", so each {numeric} is expanded into one grammar
    entry per digit word. This is why {numeric} in a VOSK mode recognizes single digits only.

    Scenario: One placeholder expands to ten phrases
      Then the recognizer vocabulary for mode "root mode" includes "line zero"
      And the recognizer vocabulary for mode "root mode" includes "line five"
      And the recognizer vocabulary for mode "root mode" includes "line nine"
      And the recognizer vocabulary for mode "root mode" contains 10 phrases starting with "line "

    Scenario: Two placeholders expand combinatorially
      Given mode "root mode" has the command "go to {numeric} {numeric}" mapped to "ctrl + g"
      Then the recognizer vocabulary for mode "root mode" contains 100 phrases starting with "go to "

    @tinkerer
    Scenario: A multi-digit number cannot be spoken as one word
      # Documented limitation of the closed grammar, not a bug. "line 42" needs two utterances
      # or a transformer mode.
      When I say "line forty two"
      Then no input is emitted

    @tinkerer
    Scenario: A pattern with many placeholders is reported as too large
      Given mode "root mode" has the command "code {numeric} {numeric} {numeric} {numeric} {numeric} {numeric}" mapped to "esc"
      When the configuration is loaded
      Then a warning is logged naming the command
      And the warning states the number of generated grammar phrases

  Rule: {rest} cannot appear in a VOSK mode

    @design-decision
    Scenario: {rest} is rejected rather than silently dead
      # closes S1 — today "{rest}" reaches Kaldi as a literal token and VoskStream's partial
      # matcher does not handle it, so the command can never fire and nothing says so
      Given mode "root mode" has the command "search {rest}" mapped to "write({1})"
      When the configuration is loaded
      Then loading fails with an error naming the command "search {rest}"

  Rule: Commands fire from partial results

    Scenario: A command fires before the utterance ends
      When the recognizer emits the partial "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: A partial matches at the tail of what has been heard
      When the recognizer emits the partial "open"
      Then no input is emitted
      When the recognizer emits the partial "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: A command already fired does not fire again as the utterance continues
      When the recognizer emits the partial "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted
      When the recognizer emits the partial "open terminal open"
      Then no further input is emitted

    Scenario: Two commands in one utterance both fire, in order
      When the recognizer emits the partial "open terminal"
      And the recognizer emits the partial "open terminal press enter"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑ enter↓ enter↑" is emitted

    Scenario: A final result does not re-fire what a partial already fired
      When the recognizer emits the partial "open terminal"
      And the recognizer emits the final result "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: A final result fires a command no partial matched
      When the recognizer emits the final result "press enter"
      Then the key sequence "enter↓ enter↑" is emitted

    Scenario: Progress within an utterance resets at the next utterance
      When the recognizer emits the partial "open terminal"
      And the recognizer emits the final result "open terminal"
      And the recognizer emits the partial "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑ alt↓ enter↓ enter↑ alt↑" is emitted

  Rule: A disabled mode consumes audio without acting on it

    Every mode's audio stream keeps running while the app runs; only the active mode processes
    what it hears. Switching modes must not restart audio capture.

    Scenario: An inactive mode does not fire its commands
      Given the active mode is "dictate"
      When mode "root mode" hears "open terminal"
      Then no input is emitted

    Scenario: Switching modes does not restart audio capture
      When I say "dictate"
      Then the audio stream for mode "root mode" is still running
      And the audio stream for mode "dictate" is still running

    Scenario: A mode reactivated later starts from a clean utterance
      Given the recognizer for mode "root mode" emitted the partial "open"
      When I say "dictate"
      And I say "root mode"
      And the recognizer emits the partial "terminal"
      Then no input is emitted

    @accessibility-critical
    Scenario: The phrase that switched a mode away does not fire again on the way back
      # A recognizer is not fed while its mode is inactive, and commands fire from a partial,
      # so nothing ever finalises the utterance that caused the switch: Kaldi is still holding
      # "dictate" when "root mode" comes back. Firing it again switches straight out of the
      # mode the user just asked for, and because the same stale hypothesis is still there the
      # next time, every attempt to leave bounces. A dictation mode becomes a trap with no
      # hands-free way out.
      When I say "dictate"
      And I say "root mode"
      Then the active mode is "root mode"
      And the active mode is still "root mode" one second later

  Rule: Partial results are not printed as normal output

    Scenario: Partial results are logged at debug level
      # closes M3 — voskstream prints every unmatched partial unconditionally today, producing
      # continuous console spam during normal use
      Given the log level is "info"
      When the recognizer emits the partial "open"
      Then nothing is written to standard output
      When the log level is "debug"
      And the recognizer emits the partial "open"
      Then "open" is logged at debug level
