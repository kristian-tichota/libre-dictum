@tinkerer
Feature: The response DSL

  A command's response is a small language describing what to do. In its simplest form it is a
  chord: tokens joined by "+", pressed together. It can also repeat a group, expand an alias,
  hold a key across commands, run a script, or change mode.

  Processing a response happens in a fixed order:

    1. aliases are applied         — so an alias may expand into any of the syntax below
    2. saved variables are filled  — see variables.feature
    3. repeat groups are expanded  — 3(ctrl + c) becomes three chords
    4. the result is split on "+"  — an escaped \+ is a literal plus
    5. every token is validated    — one bad token rejects the whole response
    6. tokens execute in order

  Keystroke expectations below use ↓ for a press and ↑ for a release.

  Background:
    Given the app is running with a vosk mode "root mode"
    And the active mode is "root mode"
    And no key is held

  Rule: A chord presses modifiers, then the key, then releases everything

    Scenario: A single key
      Given the command "go" mapped to "enter"
      When I say "go"
      Then the key sequence "enter↓ enter↑" is emitted

    Scenario: A modifier and a key
      Given the command "copy" mapped to "ctrl + c"
      When I say "copy"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: Two modifiers and a key
      Given the command "paste plain" mapped to "ctrl + shift + v"
      When I say "paste plain"
      Then the key sequence "ctrl↓ shift↓ v↓ v↑ ctrl↑ shift↑" is emitted

    Scenario: Two plain keys are pressed in sequence, not together
      Given the command "two letters" mapped to "a + b"
      When I say "two letters"
      Then the key sequence "a↓ a↑ b↓ b↑" is emitted

    Scenario: Modifiers release after the key that used them
      Given the command "select then type" mapped to "ctrl + a + b"
      When I say "select then type"
      Then the key sequence "ctrl↓ a↓ a↑ ctrl↑ b↓ b↑" is emitted

    Scenario: Token case is ignored
      Given the command "copy" mapped to "CTRL + C"
      When I say "copy"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: Whitespace around tokens is ignored
      Given the command "copy" mapped to "ctrl   +c"
      When I say "copy"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: Mouse buttons are tokens too
      Given the command "click" mapped to "left_mouse"
      When I say "click"
      Then the mouse sequence "left_mouse↓ left_mouse↑" is emitted

    Scenario: A modifier applies to a mouse button
      Given the command "shift click" mapped to "shift + left_mouse"
      When I say "shift click"
      Then the key sequence "shift↓" is emitted
      And the mouse sequence "left_mouse↓ left_mouse↑" is emitted
      And the key sequence "shift↑" is emitted

  Rule: A delay separates emitted events

    Applications and toolkits drop events that arrive too fast. input_delay is how long to wait
    after each press and release.

    Scenario: The configured delay is applied between events
      Given mode "root mode" has effective setting "input_delay" of 0.05
      And the command "copy" mapped to "ctrl + c"
      When I say "copy"
      Then consecutive emitted events are at least 0.05 seconds apart

  Rule: A repeat group repeats its contents

    Scenario: Repeating a single key
      Given the command "down five" mapped to "5(down)"
      When I say "down five"
      Then the key "down" is pressed 5 times

    Scenario: Repeating a chord
      Given the command "undo thrice" mapped to "3(ctrl + z)"
      When I say "undo thrice"
      Then the key sequence "ctrl↓ z↓ z↑ ctrl↑ ctrl↓ z↓ z↑ ctrl↑ ctrl↓ z↓ z↑ ctrl↑" is emitted

    Scenario: Repeat groups nest, innermost first
      Given the command "nested" mapped to "2(a + 2(b))"
      When I say "nested"
      Then the key sequence "a↓ a↑ b↓ b↑ b↓ b↑ a↓ a↑ b↓ b↑ b↓ b↑" is emitted

    Scenario: A repeat group may sit inside a chord
      Given the command "many tabs" mapped to "ctrl + 3(tab)"
      When I say "many tabs"
      Then the key sequence "ctrl↓ tab↓ tab↑ ctrl↑ tab↓ tab↑ tab↓ tab↑" is emitted

    @tinkerer
    Scenario: A zero repeat count is rejected at load time
      # closes a latent hazard: 0(ctrl) expands to the empty string today, which then fails
      # token validation with a confusing message about an empty character
      Given the command "nothing" mapped to "0(ctrl)"
      Then the configuration is rejected at load time
      And the error explains that a repeat count must be at least 1

  Rule: Aliases are applied before anything else

    An alias is a regex-to-replacement pair. Because aliases run first, one may expand into
    repeat groups, verbs, or further syntax.

    Scenario: An alias expands into a chord
      Given mode "root mode" has the alias "^copy$" replaced by "ctrl + c"
      And the command "duplicate" mapped to "copy"
      When I say "duplicate"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: An alias may capture and reuse text
      Given mode "root mode" has the alias "^write\((.*)\)$" replaced by "script(copy;;\1) + ctrl + shift + v"
      And the command "type {rest}" mapped to "write({1})"
      When I say "type hello world"
      Then the script "copy" is called with "hello world"
      And the key sequence "ctrl↓ shift↓ v↓ v↑ ctrl↑ shift↑" is emitted

    Scenario: Aliases are applied in declaration order
      Given mode "root mode" has aliases in this order:
        | pattern | replacement |
        | ^one$   | two         |
        | ^two$   | a           |
      And the command "go" mapped to "one"
      When I say "go"
      Then the key sequence "a↓ a↑" is emitted
      # "one" becomes "two", then the second alias rewrites it to "a"

    Scenario: An alias from another mode does not apply
      Given mode "root mode" has no aliases
      And another mode has the alias "^copy$" replaced by "ctrl + c"
      And the command "duplicate" mapped to "copy"
      When I say "duplicate"
      Then no input is emitted
      And a warning is logged mentioning "copy"

  Rule: Splitting is on unescaped plus signs only

    Scenario: An escaped plus is a literal key
      Given mode "root mode" has the alias "^write\((.*)\)$" replaced by "script(copy;;\1) + ctrl + shift + v"
      And the command "plus sign" mapped to "write(\+)"
      When I say "plus sign"
      Then the script "copy" is called with "+"

    Scenario: An escaped plus inside a chord
      Given the command "shift equals" mapped to "shift + \+"
      When I say "shift equals"
      Then the key sequence "shift↓ =↓ =↑ shift↑" is emitted

  Rule: One invalid token rejects the whole response

    Nothing is emitted for a response that contains a token the app cannot execute. A partially
    executed chord could leave a modifier stuck down.

    @accessibility-critical
    Scenario: A response with an unknown token emits nothing
      Given the command "broken" mapped to "ctrl + nonsense"
      When I say "broken"
      Then no input is emitted
      And no key is held

    @accessibility-critical
    Scenario: An unknown token cannot reach execution
      # closes C3 — an unknown key raises from the execution path today, killing the recognizer
      # thread and leaving the mode permanently deaf with no error the user can see
      Given a response containing an unknown token somehow reaches execution
      Then the token is skipped with an error logged
      And the recognizer thread keeps running

    @tinkerer
    Scenario: An invalid response is reported at load time, not on first use
      # closes C3
      Given the command "broken" mapped to "ctrl + nonsense"
      Then the configuration is rejected at load time
      And the error names the command "broken"
      And the error mentions "nonsense"
