@tinkerer
Feature: A configuration spread over several files

  One file becomes hard to navigate long before it becomes hard to load: a config with two
  hundred commands is a config nobody can find anything in. "config.json" may therefore name
  other files in an "include" list, and the whole set is treated as one configuration.

  Merging across files is deliberately not a feature. Two files that both define a mode is an
  error naming both, because the alternative -- whichever file loaded last quietly wins -- is a
  change to what a command does that its author discovers by speaking it. Sharing between modes
  stays the job of templates, which work across files.

  In the scenarios below, "the configuration directory" is a JSON object mapping a file name to
  that file's contents.

  Rule: config.json names the other files that make up the configuration

    Scenario: A mode declared in an included file runs
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"] },
          "modes/root.json": {
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then "root mode" is a runnable mode

    Scenario: A template is imported across files
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["groups/*.json", "modes/*.json"] },
          "groups/navigation.json": {
            "modes": { "::navigation": { "commands": { "step up": "up" } } }
          },
          "modes/root.json": {
            "modes": {
              "root mode": {
                "type": "vosk", "path": "models/small-en",
                "imports": ["::navigation"]
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then mode "root mode" has commands:
        | pattern | response |
        | step up | up       |

    Scenario: A glob is expanded in sorted order
      # File order decides the default starting mode, so it may not be the order the
      # filesystem happens to hand back.
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"] },
          "modes/z-last.json": {
            "modes": { "zulu": { "type": "vosk", "path": "models/small-en" } }
          },
          "modes/a-first.json": {
            "modes": { "alpha": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then the active mode is "alpha"

    Scenario: config.json is not loaded twice by its own glob
      Given the configuration directory:
        """
        {
          "config.json": {
            "include": ["*.json"],
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          },
          "extra.json": {
            "modes": { "other mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then "root mode" is a runnable mode
      And "other mode" is a runnable mode

  Rule: Nothing merges across files

    @design-decision
    Scenario: A mode defined in two files is rejected, naming both
      # Rejected alternative: merge them, or let the last file loaded win. Both make the
      # meaning of a command depend on a filename sort order.
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"] },
          "modes/a.json": {
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          },
          "modes/b.json": {
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "modes/a.json" and "modes/b.json"

    Scenario: A setting given in two files is rejected, naming both
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"], "starting_mode": "root mode" },
          "modes/root.json": {
            "starting_mode": "root mode",
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "config.json" and "modes/root.json"

    @design-decision
    Scenario: Only config.json may include other files
      # One level keeps the load order readable in the file that owns it.
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"] },
          "modes/root.json": {
            "include": ["more/*.json"],
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "modes/root.json"

  Rule: A file set is read whole, before any of it is interpreted

    @accessibility-critical
    Scenario: An include entry that matches nothing is rejected, naming the entry
      # A mistyped directory would otherwise take a hundred commands out of the session
      # without a word, and the commands that are gone may be the ones needed to fix it.
      Given the configuration directory:
        """
        {
          "config.json": {
            "include": ["mods/*.json"],
            "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "mods/*.json"

    Scenario: Malformed JSON in an included file names that file
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["modes/*.json"] },
          "modes/root.json": "{\"modes\": }"
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "modes/root.json"

    @accessibility-critical @concurrency
    Scenario: A reload that cannot read one file keeps the running configuration
      Given the app is running
      When config.json is replaced with:
        """
        { "include": ["modes/*.json"] }
        """
      And I say "reload config"
      Then the previously loaded configuration stays active
      And an error is logged mentioning "matches no file"

  Rule: A command remembers which file it came from

    Scenario: An error about an imported command names the template and the file
      # Import resolution flattens a mode's command table; without provenance the answer
      # to "which of my eight group files holds this line" is gone.
      Given the configuration directory:
        """
        {
          "config.json": { "include": ["groups/*.json", "modes/*.json"] },
          "groups/navigation.json": {
            "modes": { "::navigation": { "commands": { "step up": "hold(bogus)" } } }
          },
          "modes/root.json": {
            "modes": {
              "root mode": {
                "type": "vosk", "path": "models/small-en",
                "imports": ["::navigation"]
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "::navigation" and "groups/navigation.json"
      And loading fails with an error naming mode "root mode"

    Scenario: Moving a command to another file does not reload the model
      # A recognizer is rebuilt when its grammar would have been built differently, and
      # which file a command was written in is not part of that.
      Given the app is running with mode "root mode" whose commands come from config.json
      When the same commands are moved into an included file
      And I say "reload config"
      Then mode "root mode" keeps its recognizer
