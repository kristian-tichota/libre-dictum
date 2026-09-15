@design-decision
Feature: Finding a forgotten command

  A configuration stops being navigable by memory long before it stops loading. The
  accessibility user has no keyboard to fall back on while they try to remember, and the
  tinkerer has no way to see what they built. A display therefore has to answer "what can I say
  here", grouped, because a flat list of two hundred phrases answers nothing.

  Nothing is annotated to make that work. The groups are already in the configuration, in
  two places: a template or an included file names the commands it holds, and the author
  chose that name; commands written inline in a mode have no such name, but their first
  word is one. "buffer left", "buffer save" and "buffer close" form a family whether or not the
  configuration declares them as one.

  Rule: an imported command keeps the name its template was given

    Scenario: A template becomes a group
      Given the configuration:
        """
        {
          "modes": {
            "::navigation": { "commands": { "step up": "up", "step down": "down" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::navigation"],
              "commands": { "zoom in": "ctrl + =" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" lists the group "::navigation"
      And the group "::navigation" holds 2 commands

    Scenario: A template is not broken up by the first-word rule
      # The author named this group. The first-word rule exists only for commands that
      # have no name of their own, and applying it here would discard one that was given.
      Given the configuration:
        """
        {
          "modes": {
            "::navigation": {
              "commands": { "step up": "up", "step down": "down", "page up": "pageup" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::navigation"]
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" lists the group "::navigation"
      And the sheet for "root mode" does not list a group "step"

  Rule: commands declared inline are grouped by their first word

    Scenario: A shared first word becomes a family named after it
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer open": "ctrl + o"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" lists the group "buffer"
      And the group "buffer" holds 3 commands

    Scenario: A first word used once is not a family of one
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "zoom in": "ctrl + =",
                "zoom out": "ctrl + -",
                "grab all": "ctrl + a"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "zoom" holds 2 commands
      And the group "misc" holds 1 command

  Rule: a group whose phrases share two axes is shown as a table

    Scenario: Two families over the same tails become one table
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "focus left": "meta + left",
                "focus right": "meta + right",
                "transit left": "meta + alt + left",
                "transit right": "meta + alt + right"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "focus/transit" is a table with rows "focus, transit" and columns "left, right"

    Scenario: A family covering part of the axis joins, and the gaps are kept
      # The empty cells are the point. They say that "link charlie" is a command this
      # configuration does not have, which no list of the commands it does have can say.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "vector alpha": "f13",
                "vector bravo": "f14",
                "vector charlie": "f15",
                "dispatch alpha": "meta + f13",
                "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "shift + ctrl + alt + -",
                "link bravo": "shift + ctrl + alt + ="
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "vector/dispatch/link" is a table with rows "vector, dispatch, link"
      And the cell "link" / "charlie" is empty

    Scenario: Families that share a word but not an axis stay apart
      # "focus" fits inside "buffer"'s columns, so nothing stops it joining -- but the two
      # together leave a third of the grid empty, which means left and right are a word
      # they happen to share rather than an axis they both run along. A merge exists only
      # to make a table, so one that makes no table is undone.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "focus left": "meta + left",
                "focus right": "meta + right",
                "buffer left": "ctrl + left",
                "buffer right": "ctrl + right",
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer open": "ctrl + o",
                "buffer copy": "ctrl + c"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" lists the group "focus"
      And the sheet for "root mode" lists the group "buffer"

    Scenario: Rows that share no columns are still a table
      # The alphabet: the verb changes every few letters, so a flat list of twenty-six
      # makes the reader work out which verb applies on every line.
      Given the configuration:
        """
        {
          "modes": {
            "::alphabet": {
              "commands": {
                "type a": "a", "type b": "b",
                "press e": "e", "press f": "f",
                "key i": "i", "key j": "j"
              }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::alphabet"]
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "::alphabet" is a table with rows "type, press, key"

    Scenario: One first word is a list, not a table
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "buffer save": "ctrl + s",
                "buffer close": "ctrl + w",
                "buffer open": "ctrl + o"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "buffer" is a plain list

  Rule: every group carries the phrase that opens it

    Scenario: A template is opened by its name without the colons
      Given the configuration:
        """
        {
          "modes": {
            "::control keys": { "commands": { "send escape": "esc" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::control keys"]
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "::control keys" is opened by saying "open control keys"

    Scenario: Either name of a merged table opens it
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "focus left": "meta + left",
                "focus right": "meta + right",
                "transit left": "meta + alt + left",
                "transit right": "meta + alt + right"
              }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "focus/transit" is opened by saying "open focus"
      And the group "focus/transit" is opened by saying "open transit"

    @accessibility-critical
    Scenario: A group nobody can pronounce is shown, and says it cannot be opened
      # Putting "{numeric}" in a closed recognizer grammar would be a lexicon entry nobody
      # can say, and one more chance to mis-hear a real command. Hiding the group instead
      # would leave commands that exist and cannot be found by the person who wrote them.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "{numeric} up": "up", "{numeric} down": "down" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the group "{numeric}" holds 2 commands
      And the group "{numeric}" cannot be opened by voice

    @transformer @accessibility-critical
    Scenario: A dictation mode says outright that it cannot be navigated
      # A "{rest}" command types whatever it hears, so nothing said there escapes being
      # typed -- and a transformer mode has no closed grammar to add a phrase to. A display
      # that quietly ignored the user here would look identical to one that had crashed.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "transformer", "model_name": "tiny",
              "commands": { "{rest}": "type({1})" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet cannot be opened by voice in "root mode"

  Rule: the sheet says how to leave the mode, not only where to

    Scenario: A gesture that switches mode is shown
      Given the configuration:
        """
        {
          "ht_custom_gestures": { "smile": { "mouthSmileLeft": { "min": 0.5 } } },
          "enable_head_tracking": true,
          "ht_model_path": "models/face_landmarker.task",
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "gestures": { "smile": "mode(other mode)" }
            },
            "other mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" says that the gesture "smile" leads to "other mode"

    Scenario: A mode switch reachable only through an alias is still shown
      # An alias can expand into anything, so a response that says nothing about modes may
      # still switch. Searching the text before expanding it would miss every one.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "aliases": { "GO": "mode(other mode)" },
              "commands": { "go over there": "GO" }
            },
            "other mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" says that saying "go over there" leads to "other mode"

    Scenario: A switch to a placeholder is no route anywhere
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "go {any}": "mode({1})" }
            }
          }
        }
        """
      When the configuration is loaded
      Then the sheet for "root mode" shows no route out other than by mode name

  Rule: the sheet keeps its place across a mode switch

    Scenario: A group the new mode also has stays open
      # Hopping to another mode for one symbol and back must not cost two re-openings.
      Given the configuration:
        """
        {
          "modes": {
            "::navigation": { "commands": { "step up": "up" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::navigation"]
            },
            "other mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::navigation"]
            }
          }
        }
        """
      And the app is running
      And the sheet is open at the group "::navigation"
      When I say "other mode"
      Then the sheet is open at the group "::navigation"

    Scenario: A group the new mode does not have falls back to the index
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            },
            "other mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      And the app is running
      And the sheet is open at the group "buffer"
      When I say "other mode"
      Then the sheet is open at the index

    @accessibility-critical
    Scenario: The sheet never closes itself
      # An overlay that vanishes while it is being read is worse than one that has to be
      # dismissed: dismissing costs an utterance, and re-opening costs the utterance plus
      # finding the place again.
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
      And the sheet is open at the group "buffer"
      When 60 seconds of silence pass
      Then the sheet is open at the group "buffer"

  Rule: when nothing matched, the closest pattern is named

    Scenario: One word out
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "zoom in": "ctrl + =" }
            }
          }
        }
        """
      And the app is running
      When I say "buffer safe"
      Then no input is emitted
      And "buffer save" is offered as the closest pattern

    Scenario: Nothing like anything
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s" }
            }
          }
        }
        """
      And the app is running
      When I say "elephant"
      Then no input is emitted
      And no closest pattern is offered
