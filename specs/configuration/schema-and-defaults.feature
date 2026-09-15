@tinkerer
Feature: Configuration schema and defaults

  A single JSON file at ~/.config/libre-dictum/config.json is the whole user-facing surface.
  It declares global settings and a set of named modes. Every setting a user can omit has a
  default, and those defaults are part of the contract.

  These scenarios describe the *on-disk* schema and the *effective resolved value* of each
  setting. They deliberately say nothing about how settings are represented in memory, so the
  refactor is free to replace nested-dict flattening with typed objects.

  Background:
    Given the app has write access to /dev/uinput

  Rule: An absent or empty configuration starts a running but inert app

    The app must never crash for want of a config file. It should instead say clearly that it
    has nothing to do.

    Scenario: No configuration file exists
      Given no configuration file exists
      When the app starts
      Then a warning is logged mentioning "config.json"
      And the warning names the path it looked in
      And the app reports that no modes are defined
      And the app exits cleanly with a non-zero status

    Scenario: The configuration defines no modes
      Given the configuration:
        """
        { "reload_command": "reload config", "modes": {} }
        """
      When the app starts
      Then a warning is logged mentioning "no modes"
      And the app exits cleanly with a non-zero status

    @accessibility-critical
    Scenario: The configuration directory is created if missing
      Given no directory exists at ~/.config/libre-dictum
      When the app starts
      Then the directory ~/.config/libre-dictum/scripts is created
      And the app does not crash

  Rule: Global settings have documented defaults

    Scenario: An empty configuration object resolves every global to its default
      Given the configuration:
        """
        { "modes": {} }
        """
      When the configuration is loaded
      Then the effective global settings are:
        | setting                | value          |
        | reload_command         | reload config  |
        | panic_command          | release everything |
        | enable_systray         | false          |
        | enable_head_tracking   | false          |
        | previous_mode_keyword  | previous mode  |
        | starting_mode          | (first mode)   |

    @design-decision
    Scenario: starting_mode defaults to the first runnable mode in file order
      Given the configuration:
        """
        {
          "modes": {
            "::base":     { "commands": { "open terminal": "alt + enter" } },
            "root mode":  { "type": "vosk", "path": "models/small-en", "imports": ["::base"] },
            "other mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      When the configuration is loaded
      Then the effective starting_mode is "root mode"
      # "::base" is an import template, not a runnable mode, so it is skipped

  Rule: A mode declares which recognizer drives it

    A mode's "type" selects its recognizer and therefore which settings apply to it. Omitting
    "type" makes the mode an import template rather than a runnable mode.

    Scenario Outline: Recognizer types and their required settings
      Given a mode of type "<type>"
      Then it requires the setting "<required>"
      And it is <runnable>

      Examples:
        | type        | required   | runnable      |
        | vosk        | path       | runnable      |
        | transformer | model_name | runnable      |
        | import      | (none)     | not runnable  |

    Scenario: Omitting type makes a mode an import template
      Given the configuration:
        """
        { "modes": { "shared": { "commands": { "copy": "ctrl + c" } } } }
        """
      When the configuration is loaded
      Then "shared" is not a runnable mode
      And "shared" is available as an import template

  Rule: Per-mode defaults are applied to runnable modes only

    Scenario: A runnable mode inherits per-mode defaults it did not set
      Given the configuration:
        """
        {
          "modes": {
            "root mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective settings:
        | setting         | value |
        | input_delay     | 0.01  |
        | commands        | {}    |
        | gestures        | {}    |
        | aliases         | {}    |
        | banned_strings  | []    |
        | enter_command   | null  |
        | exit_command    | null  |
        | icon            | null  |

    @transformer
    Scenario: A transformer mode inherits dictation defaults
      Given the configuration:
        """
        {
          "modes": {
            "dictate": { "type": "transformer", "model_name": "whisper-turbo" }
          }
        }
        """
      When the configuration is loaded
      Then mode "dictate" has effective settings:
        | setting            | value |
        | silence_seconds    | 0.3   |
        | max_chunk_seconds  | 30.0  |
        | energy_threshold   | 0.01  |
        | pre_roll_seconds   | 0.25  |
        | lang               | en    |

  Rule: A subsystem block applies only to modes of the matching type

    The "vosk", "transformer" and "head_tracking" blocks let one import template carry settings
    for several kinds of mode. Each block is applied only where it is meaningful, so a shared
    template can be imported by both a command mode and a dictation mode without conflict.

    Scenario: A shared template carries settings for both recognizer types
      Given the configuration:
        """
        {
          "modes": {
            "::shared": {
              "vosk":        { "path": "models/small-en" },
              "transformer": { "model_name": "whisper-turbo", "silence_seconds": 0.5 }
            },
            "root mode": { "type": "vosk",        "imports": ["::shared"] },
            "dictate":   { "type": "transformer", "imports": ["::shared"] }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "path" of "models/small-en"
      And mode "root mode" has no effective setting "model_name"
      And mode "dictate" has effective setting "model_name" of "whisper-turbo"
      And mode "dictate" has effective setting "silence_seconds" of 0.5
      And mode "dictate" has no effective setting "path"

  @head-tracking @design-decision
  Rule: Head-tracking settings are global, overridable per mode

    Every ht_* knob may be set globally and overridden by any mode, inversion included. This
    makes a low-sensitivity "precision" mode expressible without duplicating calibration.

    Scenario: A mode overrides one global head-tracking knob and inherits the rest
      Given the configuration:
        """
        {
          "enable_head_tracking": true,
          "ht_model_path": "models/face_landmarker.task",
          "ht_speed_mult": 2.0,
          "ht_max_speed": 20,
          "ht_invert_y": true,
          "modes": {
            "root mode":  { "type": "vosk", "path": "models/small-en", "ht_enabled": true },
            "precision":  { "type": "vosk", "path": "models/small-en", "ht_enabled": true,
                            "head_tracking": { "ht_speed_mult": 0.5 } }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "ht_speed_mult" of 2.0
      And mode "precision" has effective setting "ht_speed_mult" of 0.5
      And mode "precision" has effective setting "ht_max_speed" of 20
      And mode "precision" has effective setting "ht_invert_y" of true

    Scenario: Inversion is overridable per mode
      # closes S8 — ht_invert_x/y are global-only today while sibling knobs are per-mode
      Given the configuration:
        """
        {
          "enable_head_tracking": true,
          "ht_model_path": "models/face_landmarker.task",
          "ht_invert_x": false,
          "modes": {
            "mirrored": { "type": "vosk", "path": "models/small-en", "ht_enabled": true,
                          "head_tracking": { "ht_invert_x": true } }
          }
        }
        """
      When the configuration is loaded
      Then mode "mirrored" has effective setting "ht_invert_x" of true

    Scenario: Head-tracking settings resolve even when the global feature is off
      # Consumers must not depend on attribute presence as a feature flag
      Given the configuration:
        """
        {
          "enable_head_tracking": false,
          "modes": {
            "root mode": { "type": "vosk", "path": "models/small-en", "ht_enabled": true }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "ht_dead_angle_h" of 2.0
      And the head tracking subsystem is not started
