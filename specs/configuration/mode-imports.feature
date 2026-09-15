@tinkerer
Feature: Mode composition through imports

  A mode with no "type" is an import template: a reusable bundle of commands, aliases, banned
  strings and settings that runnable modes pull in via their "imports" list. Templates are
  merged into their importers and then removed, so they never appear as switchable modes.

  This is the tinkerer's main tool for avoiding duplication across modes, so the merge rules
  have to be exact and predictable.

  Rule: Templates are merged into importers and then removed

    Scenario: A template's commands appear in the importing mode
      Given the configuration:
        """
        {
          "modes": {
            "::shared": { "commands": { "copy": "ctrl + c", "paste": "ctrl + v" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::shared"],
              "commands": { "save": "ctrl + s" }
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has commands:
        | pattern | response |
        | save    | ctrl + s |
        | copy    | ctrl + c |
        | paste   | ctrl + v |
      And "::shared" is not a runnable mode

    Scenario: A template is never a switch target
      Given the app is running with a template "::shared" and a mode "root mode"
      And the active mode is "root mode"
      When I say "shared"
      Then the active mode is still "root mode"

  Rule: Merging recurses into objects and extends lists

    Scenario: Nested objects merge key by key
      Given the configuration:
        """
        {
          "modes": {
            "::shared":  { "aliases": { "^copy$": "ctrl + c" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::shared"],
              "aliases": { "^save$": "ctrl + s" }
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has aliases:
        | pattern  | replacement |
        | ^save$   | ctrl + s    |
        | ^copy$   | ctrl + c    |

    Scenario: Lists are extended, not replaced
      Given the configuration:
        """
        {
          "modes": {
            "::shared":  { "banned_strings": ["thank you", "okay"] },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::shared"],
              "banned_strings": ["um"]
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has banned strings "um", "thank you", "okay"

    Scenario: The importing mode's own value wins for scalars
      Given the configuration:
        """
        {
          "modes": {
            "::shared":  { "input_delay": 0.05 },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::shared"],
              "input_delay": 0.01
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "input_delay" of 0.01

  Rule: Identity settings are never inherited

    A mode's "type", "imports" list and tray "icon" identify that specific mode. Inheriting them
    would make templates able to silently change what kind of mode an importer is, or give two
    modes the same tray colour.

    Scenario: type, imports and icon do not cross the merge
      Given the configuration:
        """
        {
          "modes": {
            "::shared": {
              "type": "import",
              "icon": [255, 255, 0],
              "imports": [],
              "commands": { "copy": "ctrl + c" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "icon": [0, 255, 0],
              "imports": ["::shared"]
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "type" of "vosk"
      And mode "root mode" has effective setting "icon" of [0, 255, 0]
      And mode "root mode" has commands:
        | pattern | response |
        | copy    | ctrl + c |

    Scenario: A mode without its own icon does not inherit one
      Given the configuration:
        """
        {
          "modes": {
            "::shared":  { "icon": [255, 255, 0] },
            "root mode": { "type": "vosk", "path": "models/small-en", "imports": ["::shared"] }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has effective setting "icon" of null
      And the tray shows no icon for mode "root mode"

  Rule: Imports are resolved in dependency order, not file order

    @tinkerer
    Scenario: A template importing a template declared later still resolves
      # closes S7 — imports are resolved in JSON iteration order today, so this silently
      # produces a "root mode" with no "copy" command and no error
      Given the configuration:
        """
        {
          "modes": {
            "::editing": { "imports": ["::clipboard"] },
            "::clipboard": { "commands": { "copy": "ctrl + c" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::editing"]
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has commands:
        | pattern | response |
        | copy    | ctrl + c |

    Scenario: A chain of templates resolves transitively
      Given the configuration:
        """
        {
          "modes": {
            "::a": { "commands": { "one": "1" } },
            "::b": { "imports": ["::a"], "commands": { "two": "2" } },
            "::c": { "imports": ["::b"], "commands": { "three": "3" } },
            "root mode": { "type": "vosk", "path": "models/small-en", "imports": ["::c"] }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has commands:
        | pattern | response |
        | one     | 1        |
        | two     | 2        |
        | three   | 3        |

  Rule: Broken import graphs are reported, not tolerated

    Scenario: Importing a mode that does not exist
      Given the configuration:
        """
        {
          "modes": {
            "root mode": { "type": "vosk", "path": "models/small-en", "imports": ["::missing"] }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error mentions "::missing"

    Scenario: A cyclic import is rejected with the cycle named
      # closes S7 — no cycle detection today
      Given the configuration:
        """
        {
          "modes": {
            "::a": { "imports": ["::b"] },
            "::b": { "imports": ["::a"] },
            "root mode": { "type": "vosk", "path": "models/small-en", "imports": ["::a"] }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error mentioning "cycle"
      And the error names both "::a" and "::b"

    Scenario: Importing a runnable mode is allowed
      # Runnable modes may be imported too; only their identity settings are withheld
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "copy": "ctrl + c" }
            },
            "strict mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["root mode"]
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "strict mode" has commands:
        | pattern | response |
        | copy    | ctrl + c |
      And mode "root mode" is still a runnable mode
