@design-decision
Feature: Opening the display by voice

  The sheet answers "what can I say here" and the chip says what the session is doing.
  Neither is worth anything if reaching them needs a keyboard.

  Two kinds of phrase, and every one of them is configuration rather than code. Four are
  written in the `overlay` block -- show the sheet, hide it, open the modes page, toggle the
  chip. The rest are *generated*, one per group of the active mode, because the recognizer
  grammar is closed: a single "open {rest}" command cannot work, since only {numeric} is
  expanded and the literal string would land in the lexicon. Because they are generated, the
  phrase that opens a group is exactly the phrase the sheet drew beside it.

  The defaults were deduced from a real 493-line configuration rather than invented.
  "overlay", "chip" and "sheet" appear in none of its 170 phrases; "open" is used there
  only ever as a second word -- "buffer open", "file open" -- so "open <group>" is free.
  And "overlay open"/"overlay close" is that file's own idiom, the one "buffer
  open"/"buffer close" already uses.

  Rule: the phrases that drive the display are configured commands

    Scenario: The defaults work with no overlay block at all
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When I say "overlay open"
      Then the sheet is open at the index
      And nothing is typed

    Scenario: And can be renamed
      Given the configuration:
        """
        {
          "overlay": { "sheet": { "open": "show me", "close": "sheet off" } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When I say "show me"
      Then the sheet is open at the index

    @accessibility-critical
    Scenario: Saying the open phrase twice leaves the sheet open
      # Why the sheet gets a pair of phrases and not one toggle: a misheard phrase said
      # twice must not undo itself. The chip is a single toggle because it is always on
      # screen, so its state is visible before and after.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When I say "overlay open"
      And I say "overlay open"
      Then the sheet is open at the index

    Scenario: A command named the same as a display phrase is refused
      # The same treatment the panic, reload and previous-mode phrases get, for the same
      # reason: dispatch handles them before the command table, so the command is dead
      # config. The error names the setting to change.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "overlay open": "ctrl + o" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "overlay.sheet.open"

  Rule: every group is opened by the phrase the sheet drew beside it

    Scenario: A generated phrase opens its group
      Given the configuration:
        """
        {
          "modes": {
            "::alphabet": {
              "commands": { "type a": "a", "type b": "b", "press e": "e", "press f": "f" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::alphabet"],
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When I say "open alphabet"
      Then the sheet is open at the group "::alphabet"

    Scenario: Either name of a merged table opens it
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "focus left": "meta + left", "focus right": "meta + right",
                "transit left": "meta + alt + left", "transit right": "meta + alt + right"
              }
            }
          }
        }
        """
      And the app is running
      When I say "open transit"
      Then the sheet is open at the group "focus/transit"

    Scenario: The prefix is configurable, and one word
      # The generated phrase is "<prefix> <group name>", said dozens of times an hour, so a
      # two-word prefix makes every one of a mode's twenty phrases longer.
      Given the configuration:
        """
        {
          "overlay": { "sheet": { "navigate": "please open" } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "not one word"

    Scenario: A null prefix generates none at all
      # Every added word is a new confusion opportunity in a small model. This is the lever
      # to pull if accuracy suffers -- and it costs the sheet only its navigation, not its
      # existence: hud(group:...) still works from a configured command.
      Given the configuration:
        """
        {
          "overlay": { "sheet": { "navigate": null } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet cannot be opened by voice in "root mode"

    @accessibility-critical
    Scenario: A generated phrase that a command already uses is refused
      # Both land in the same closed grammar, whichever dispatch reaches first wins, and
      # the loser is a command the author wrote and can no longer say. Silent, and worth
      # refusing to load over.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s", "buffer close": "ctrl + w",
                "open buffer": "ctrl + o"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "open buffer"

    Scenario: A spoken name is the way out of that collision
      Given the configuration:
        """
        {
          "overlay": { "groups": { "buffer": { "say": "buffers" } } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s", "buffer close": "ctrl + w",
                "open buffer": "ctrl + o"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then "root mode" is a runnable mode
      And the group "buffer" is opened by saying "open buffers"

    Scenario: A spoken name also rescues one nobody could pronounce
      # Without it the group is shown and says it cannot be opened, which is better than
      # hiding commands that exist -- but worse than being able to reach them.
      Given the configuration:
        """
        {
          "overlay": { "groups": { "{numeric}": { "say": "count" } } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "{numeric} up": "up", "{numeric} down": "down" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "{numeric}" is opened by saying "open count"

    Scenario: A hidden group leaves the sheet and the grammar
      Given the configuration:
        """
        {
          "overlay": { "groups": { "::alphabet": { "hide": true } } },
          "modes": {
            "::alphabet": {
              "commands": { "type a": "a", "type b": "b", "press e": "e", "press f": "f" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::alphabet"],
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" does not list a group "::alphabet"
      And "open alphabet" is not recognized in "root mode"

    Scenario: Hiding one name of a merged table only costs that way in
      # A merged table answers to several names, and hiding one is a smaller thing than
      # hiding the table: that name stops being a way in, and the commands stay.
      Given the configuration:
        """
        {
          "overlay": { "groups": { "dispatch": { "hide": true } } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "vector alpha": "f13", "vector bravo": "f14", "vector charlie": "f15",
                "dispatch alpha": "meta + f13", "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "ctrl + f13", "link bravo": "ctrl + f14"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "vector/dispatch/link" holds 8 commands
      And the group "vector/dispatch/link" is opened by saying "open vector"
      And "open dispatch" is not recognized in "root mode"

    Scenario: Hiding one group does not regroup the others
      # Grouping first, hiding second. Hide "dispatch" before the families are merged and
      # the table comes back named "vector/link" -- hiding one thing would have silently
      # renamed another.
      Given the configuration:
        """
        {
          "overlay": { "groups": { "dispatch": { "hide": true } } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "vector alpha": "f13", "vector bravo": "f14", "vector charlie": "f15",
                "dispatch alpha": "meta + f13", "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "ctrl + f13", "link bravo": "ctrl + f14"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" lists the group "vector/dispatch/link"
      And the sheet for "root mode" does not list a group "vector/link"

  Rule: any command can drive the display, through hud(...)

    Scenario: A command opens a group
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s", "buffer close": "ctrl + w",
                "buffer overview": "hud(group:buffer)"
              }
            }
          }
        }
        """
      And the app is running
      When I say "buffer overview"
      Then the sheet is open at the group "buffer"

    Scenario: And composes with keystrokes
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "zoom in": "ctrl + = + hud(close)" }
            }
          }
        }
        """
      And the app is running
      And the sheet is open at the index
      When I say "zoom in"
      Then the key sequence "ctrl↓ =↓ =↑ ctrl↑" is emitted
      And the sheet is closed

    @tinkerer
    Scenario: A mistyped target is a load error listing the alternatives
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer overview": "hud(gruop:buffer)" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "buffer overview"

    @tinkerer
    Scenario: A group nobody has is a warning, not a failure
      # Group names are derived from the commands, so deleting one command can merge a
      # two-command family into "misc". Making that a hard failure would turn an unrelated
      # edit into a session that will not start; the display falls back to its index, which
      # is visible rather than silent.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s", "buffer close": "ctrl + w",
                "buffer overview": "hud(group:bufer)"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then "root mode" is a runnable mode
      And a warning is logged mentioning "bufer"

  Rule: the display is asked before the command table and after the escape hatch

    @accessibility-critical
    Scenario: The panic phrase still outranks it
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      And the key "shift" is held
      When I say "release everything"
      Then the key sequence "shift↑" is emitted

    Scenario: A command that would shadow a group phrase cannot exist
      # Which is what makes putting the display ahead of the command table safe: there is
      # nothing for it to shadow, because the loader refused that configuration.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s", "buffer close": "ctrl + w",
                "open buffer": "ctrl + o"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "open buffer"

  Rule: the chip's startup state is configuration and its visibility is not

    Scenario: It starts hidden when the config says so
      Given the configuration:
        """
        {
          "overlay": { "chip": { "enabled": false } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      Then the chip is hidden

    Scenario: A reload that did not touch it keeps a toggle
      # Which surfaces are showing is state the user drives, like the sheet's open group.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      And I say "chip toggle"
      When I say "reload config"
      Then the chip is hidden

    Scenario: Changing the setting and reloading takes effect
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When config.json is replaced with:
        """
        {
          "overlay": { "chip": { "enabled": false } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And I say "reload config"
      Then the chip is hidden
