@tinkerer
Feature: Saved variables as spoken arguments

  Some actions want an argument that is awkward to say in one breath. Saying "page down five
  times" needs a pattern for every count; saying "five" then "page down" needs only two.

  save() stores a value from one utterance. The next command that uses a placeholder consumes
  it. In practice this is a count prefix, the way a number typed before a motion is in vi:

      "number five"  ->  save(5)              stores 5
      "page down"    ->  {1=1}(pagedown)      repeats pagedown 5 times

  The default in {1=1} is what makes "page down" on its own still mean once. This is why the
  mechanism is positional rather than named: the command being modified does not have to know
  whether an argument was supplied.

  Background:
    Given the configuration:
      """
      {
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en",
            "commands": {
              "number {numeric}": "save({1})",
              "page down": "{1=1}(pagedown)",
              "page up": "{1=1}(pageup)",
              "press {any}": "{2=1}({1})",
              "copy": "ctrl + c"
            }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"
    And no saved variable exists

  Rule: A saved value modifies the next command

    Scenario: A count prefix repeats the following command
      When I say "number 5"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

    Scenario: Without a prefix the default applies
      When I say "page down"
      Then the key "pagedown" is pressed 1 time

    Scenario: The same prefix works for any command that accepts one
      When I say "number 3"
      And I say "page up"
      Then the key "pageup" is pressed 3 times

    Scenario: A spoken number word is stored as a digit
      When I say "number five"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

  Rule: A saved value is consumed once

    Scenario: The prefix does not carry to a second command
      When I say "number 5"
      And I say "page down"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times
      And then the key "pagedown" is pressed 1 time

    Scenario: A command that emits input consumes the value even if it ignores it
      When I say "number 5"
      And I say "copy"
      And I say "page down"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted
      And the key "pagedown" is pressed 1 time

    Scenario: An unmatched utterance between the two does not consume the value
      # A single misheard word must not silently drop the prefix
      When I say "number 5"
      And I say "what is the weather"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

    Scenario: A banned string does not consume the value
      Given mode "root mode" bans the string "thank you"
      When I say "number 5"
      And I say "thank you"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

  Rule: A command can take both a spoken argument and a saved one

    This is what the two-stage substitution in commands/placeholders.feature is for. Capture
    groups fill {1} upward; whatever is left over renumbers down for the saved value.

    Scenario: A captured key and a saved count together
      When I say "number 4"
      And I say "press tab"
      Then the key "tab" is pressed 4 times

    Scenario: The same command with no saved count
      When I say "press tab"
      Then the key "tab" is pressed 1 time

  @design-decision
  Rule: Restating a prefix replaces it

    Confirmed: a save() into a slot that is already filled overwrites it. In a spoken
    interface a misheard count is corrected by saying the number again, and that reading has
    to win — the alternative, accumulating positionally, ignores the correction and scrolls
    the wrong amount.

    The accepted cost is that one command can no longer collect two saved values from two
    utterances. Only one value is ever pending.

    Scenario: Correcting a misspoken count
      When I say "number 5"
      And I say "number 3"
      And I say "page down"
      Then the key "pagedown" is pressed 3 times

    Scenario: Only one value is ever pending
      Given the command "grid" mapped to "{1=1}(right) + {2=1}(down)"
      When I say "number 2"
      And I say "number 3"
      And I say "grid"
      Then the key "right" is pressed 3 times
      And the key "down" is pressed 1 time

  @design-decision
  Rule: Non-input verbs do not consume a saved value

    Confirmed, and deliberate rather than incidental: mode(), script(), python() and exec()
    leave a saved value alone, while any keystroke consumes it. "Number five", a mode switch,
    then "page down" is a reasonable thing to say, and the prefix should survive it.

    Scenario: A mode switch preserves a saved value
      Given mode "dictate" has the command "page down" mapped to "{1=1}(pagedown)"
      When I say "number 5"
      And I say "dictate"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

    Scenario: A script call preserves a saved value
      Given the command "notify" mapped to "script(notify)"
      When I say "number 5"
      And I say "notify"
      And I say "page down"
      Then the key "pagedown" is pressed 5 times

  Rule: A saved value never outlives the configuration or the session

    @accessibility-critical
    Scenario: The panic phrase clears saved values
      When I say "number 5"
      And I say "release everything"
      And I say "page down"
      Then the key "pagedown" is pressed 1 time

    Scenario: A reload clears saved values
      When I say "number 5"
      And I say "reload config"
      And I say "page down"
      Then the key "pagedown" is pressed 1 time

  Rule: A saved value is text, not code

    Scenario: A saved value is not re-interpreted as DSL
      Given mode "root mode" has the command "save a chord" mapped to "save(ctrl + c)"
      And the command "use it" mapped to "{1=x}"
      When I say "save a chord"
      And I say "use it"
      Then no input is emitted
      And a warning is logged mentioning "ctrl + c"

    @tinkerer
    Scenario: A saved value that cannot be a repeat count is reported
      # A save() feeding a repeat group must be numeric; anything else should say so rather
      # than fail token validation with a confusing message
      Given mode "root mode" has the command "save a word" mapped to "save(hello)"
      When I say "save a word"
      And I say "page down"
      Then no input is emitted
      And a warning is logged mentioning "hello"
      And the warning explains that a repeat count must be numeric
