@tinkerer
Feature: Placeholders and capture groups

  A pattern may contain placeholders that capture part of the utterance. Captured text is
  substituted into the response by position: the first placeholder becomes {1}, the second {2},
  and so on.

  Three placeholders exist, differing in what they will match:

    {numeric}  a run of digits
    {any}      a single word
    {rest}     everything remaining, including spaces

  Background:
    Given the app is running with a transformer mode "dictate"
    And the active mode is "dictate"

  Rule: Each placeholder matches a defined shape of text

    Scenario Outline: What each placeholder captures
      Given the command "<pattern>" mapped to "write({1})"
      When I say "<utterance>"
      Then the capture group 1 is "<captured>"

      Examples:
        | pattern         | utterance            | captured      |
        | press {any}     | press enter          | enter         |
        | line {numeric}  | line 42              | 42            |
        | search {rest}   | search hello world   | hello world   |

    Scenario Outline: What each placeholder refuses
      Given the command "<pattern>" mapped to "write({1})"
      When I say "<utterance>"
      Then no input is emitted

      Examples:
        | pattern         | utterance          |
        | press {any}     | press two words    |
        | line {numeric}  | line forty two     |
        | line {numeric}  | line abc           |

    Scenario: {any} captures a single word only
      Given the command "press {any} now" mapped to "write({1})"
      When I say "press enter now"
      Then the capture group 1 is "enter"

    Scenario: {rest} is greedy to the end of the utterance
      Given the command "search {rest}" mapped to "write({1})"
      When I say "search press enter now"
      Then the capture group 1 is "press enter now"

    Scenario: {rest} requires at least one character
      Given the command "search {rest}" mapped to "write({1})"
      When I say "search"
      Then no input is emitted

  Rule: Capture groups are numbered left to right from 1

    Scenario: Two placeholders in one pattern
      Given the command "{numeric} times press {any}" mapped to "{1}({2})"
      When I say "3 times press pagedown"
      Then the capture group 1 is "3"
      And the capture group 2 is "pagedown"
      And the key sequence "pagedown↓ pagedown↑ pagedown↓ pagedown↑ pagedown↓ pagedown↑" is emitted

    Scenario: A capture group may be used more than once
      Given the command "double {any}" mapped to "{1} + {1}"
      When I say "double a"
      Then the key sequence "a↓ a↑ a↓ a↑" is emitted

    Scenario: A capture group may be unused
      Given the command "ignore {any}" mapped to "esc"
      When I say "ignore whatever"
      Then the key sequence "esc↓ esc↑" is emitted

    Scenario: {0} is not a valid placeholder
      # closes C4 — {0} silently resolves to the last capture group today
      Given the command "press {any}" mapped to "{0}"
      Then the configuration is rejected at load time

  Rule: Placeholders may declare a default

    The form {n=value} supplies a value when nothing filled slot n. This is what makes a command
    usable both with and without a preceding saved variable.

    Scenario: A default is used when no value is available
      Given the command "page down" mapped to "{1=1}(pagedown)"
      And no saved variable exists
      When I say "page down"
      Then the key sequence "pagedown↓ pagedown↑" is emitted

    Scenario: An available value beats the default
      Given the command "page down" mapped to "{1=1}(pagedown)"
      And a saved variable "3"
      When I say "page down"
      Then the key sequence "pagedown↓ pagedown↑ pagedown↓ pagedown↑ pagedown↓ pagedown↑" is emitted

    Scenario: An empty default is allowed
      Given the command "maybe shift" mapped to "{1=}a"
      And no saved variable exists
      When I say "maybe shift"
      Then the key sequence "a↓ a↑" is emitted

  Rule: Substitution happens in two stages

    Capture groups are substituted first. Any placeholder left over refers to a saved variable,
    and is renumbered so that {n+1} becomes {n} for the second stage. This is what lets one
    command take both a spoken argument and a saved count.

    See input/variables.feature for the saved-variable half of this.

    Scenario: Capture groups fill the low slots and saved variables the rest
      Given the command "press {any}" mapped to "{2=1}({1})"
      And a saved variable "3"
      When I say "press pagedown"
      Then the capture group 1 is "pagedown"
      And the response after capture substitution is "{1=1}(pagedown)"
      And the response after variable substitution is "3(pagedown)"
      And the key sequence "pagedown↓ pagedown↑ pagedown↓ pagedown↑ pagedown↓ pagedown↑" is emitted

    Scenario: A leftover placeholder falls back to its default when no variable is saved
      Given the command "press {any}" mapped to "{2=1}({1})"
      And no saved variable exists
      When I say "press pagedown"
      Then the key sequence "pagedown↓ pagedown↑" is emitted

    Scenario: Captured text is inserted literally, not re-interpreted
      # A captured word is never parsed for placeholders or DSL verbs
      Given the command "type {rest}" mapped to "write({1})"
      When I say "type ctrl plus c"
      Then the text "ctrl plus c" is typed
      And no modifier key is pressed
