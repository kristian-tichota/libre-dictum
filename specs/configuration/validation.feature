@tinkerer
Feature: Configuration validation

  Everything knowable about a configuration is checked once, at load time, before any audio
  stream or camera is started. A config author must never discover a mistake by noticing that a
  command silently does nothing three utterances later.

  Every error names the mode it came from, and where applicable the specific command pattern, so
  the author can find it in a file that may hold hundreds of commands.

  Rule: A mode must declare what its recognizer needs

    Scenario: A vosk mode without a model path
      Given the configuration:
        """
        { "modes": { "root mode": { "type": "vosk" } } }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "path"

    Scenario: A transformer mode without a model name
      Given the configuration:
        """
        { "modes": { "dictate": { "type": "transformer" } } }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "dictate"
      And the error mentions "model_name"

    Scenario: An unknown mode type
      Given the configuration:
        """
        { "modes": { "root mode": { "type": "whisper" } } }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error lists the valid types "vosk", "transformer", "import"

    Scenario: A vosk model directory that does not exist on disk
      Given the configuration:
        """
        { "modes": { "root mode": { "type": "vosk", "path": "models/does-not-exist" } } }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "models/does-not-exist"

  Rule: starting_mode must name a runnable mode

    @accessibility-critical
    Scenario: starting_mode names a mode that does not exist
      # closes C10 — survives loading today and crashes with a bare KeyError at enable()
      Given the configuration:
        """
        {
          "starting_mode": "typo mode",
          "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
        }
        """
      When the configuration is loaded
      Then loading fails with an error mentioning "typo mode"
      And the error lists the available modes "root mode"

    Scenario: starting_mode names an import template
      # closes C10 — the template is deleted after merging, leaving an unresolvable start state
      Given the configuration:
        """
        {
          "starting_mode": "::shared",
          "modes": {
            "::shared":  { "commands": { "copy": "ctrl + c" } },
            "root mode": { "type": "vosk", "path": "models/small-en", "imports": ["::shared"] }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error mentioning "::shared"
      And the error explains that import templates cannot be activated

  Rule: Command patterns are validated against the mode's recognizer

    @vosk @design-decision
    Scenario: {rest} is rejected in a vosk mode
      # closes S1 — today {rest} is passed to Kaldi as a literal grammar token, so the command
      # is unreachable. A closed grammar cannot capture arbitrary speech.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "search {rest}": "write({1})" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error names the command "search {rest}"
      And the error explains that {rest} requires a transformer mode

    @vosk
    Scenario: {any} and {numeric} are accepted in a vosk mode
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "press {any}": "{1}", "number {numeric}": "save({1})" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading succeeds

    @transformer
    Scenario: All placeholders are accepted in a transformer mode
      Given the configuration:
        """
        {
          "modes": {
            "dictate": {
              "type": "transformer", "model_name": "whisper-turbo",
              "commands": { "{rest}": "write({1})" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading succeeds

    Scenario: {0} is rejected as a placeholder
      # closes C4 — {0} resolves to the *last* capture group today via negative indexing
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "press {any}": "{0}" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error explains that placeholders are numbered from 1

    Scenario: A response referring to more groups than the pattern captures
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "press {any}": "{1} + {2}" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading succeeds
      # {2} does not refer to a capture group; it is reserved for a saved variable.
      # See input/variables.feature for the two-stage substitution rule.

  Rule: Response strings are validated at load time, not at execution time

    @accessibility-critical
    Scenario: A hold() naming an unknown key is rejected at load
      # closes C3 — passes the pre-flight validator today, then raises on the recognizer thread
      # at execution, silently killing that thread and deafening the mode for the whole session
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "hold thing": "hold(bogus)" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error names the command "hold thing"
      And the error mentions "bogus"

    Scenario Outline: Every verb's argument is checked at load
      # closes C3
      Given a mode "root mode" with the command "do it" mapped to "<response>"
      When the configuration is loaded
      Then loading <outcome>

      Examples:
        | response          | outcome  |
        | ctrl + c          | succeeds |
        | hold(ctrl)        | succeeds |
        | release(ctrl)     | succeeds |
        | toggle(shift)     | succeeds |
        | left_mouse        | succeeds |
        | 3(pagedown)       | succeeds |
        | hold(bogus)       | fails    |
        | release(bogus)    | fails    |
        | toggle(bogus)     | fails    |
        | ctrl + nonsense   | fails    |

    Scenario: A mode() naming an unknown mode is rejected at load
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "go away": "mode(nowhere)" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "nowhere"

    Scenario: A response reachable only through an alias is validated after alias expansion
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "aliases": { "^shout$": "hold(shift) + bogus" },
              "commands": { "shout": "shout" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "bogus"

  Rule: Head tracking requires its model

    @head-tracking
    Scenario: Head tracking enabled with no model path
      Given the configuration:
        """
        {
          "enable_head_tracking": true,
          "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
        }
        """
      When the configuration is loaded
      Then loading fails with an error mentioning "ht_model_path"

    @head-tracking
    Scenario: A gesture action referring to an undefined gesture
      Given the configuration:
        """
        {
          "enable_head_tracking": true,
          "ht_model_path": "models/face_landmarker.task",
          "ht_custom_gestures": { "blink": { "eyeBlinkLeft": { "min": 0.5 } } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "gestures": { "eyebrow_raise": "esc" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "eyebrow_raise"
      And the error lists the defined gestures "blink"

  Rule: Reserved phrases may not collide with mode names or commands

    @accessibility-critical @design-decision
    Scenario: A mode named the same as the panic command
      Given the configuration:
        """
        {
          "panic_command": "release",
          "modes": { "release": { "type": "vosk", "path": "models/small-en" } }
        }
        """
      When the configuration is loaded
      Then loading fails with an error mentioning "panic_command"
      And the error explains that the phrase would be unreachable

    Scenario: A command pattern identical to the reload command
      Given the configuration:
        """
        {
          "reload_command": "reload config",
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "reload config": "f5" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error explains that the phrase would be unreachable

  Rule: Malformed JSON is reported with its location

    Scenario: A missing comma between commands
      Given a configuration file containing invalid JSON at line 26
      When the configuration is loaded
      Then loading fails with an error mentioning "line 26"
      And the error names the configuration file path
