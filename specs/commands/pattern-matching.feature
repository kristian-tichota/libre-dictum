@tinkerer
Feature: Matching an utterance against a command pattern

  A command pattern is literal spoken text, optionally containing placeholders. It is matched
  against the whole normalized utterance — never a substring — so "press enter" cannot be
  triggered by "I would press enter if I could".

  Normalization is applied to the utterance and to the pattern alike, so a config author can
  write patterns the way they would say them.

  Background:
    Given the configuration:
      """
      {
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en",
            "commands": {
              "open terminal": "alt + enter",
              "press {any}": "{1}",
              "go to line {numeric}": "ctrl + g"
            }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: Matching is whole-utterance and anchored

    Scenario: An exact utterance matches
      When I say "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario Outline: Partial and surrounding text does not match
      When I say "<utterance>"
      Then no input is emitted

      Examples:
        | utterance                  |
        | open                       |
        | open terminal now          |
        | please open terminal       |
        | open the terminal          |

  Rule: Utterances and patterns are normalized identically

    Scenario Outline: Normalization rules
      When I say "<utterance>"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

      Examples: Case is ignored
        | utterance      |
        | open terminal  |
        | Open Terminal  |
        | OPEN TERMINAL  |

      Examples: Sentence punctuation is stripped
        | utterance       |
        | open terminal.  |
        | open terminal?  |
        | open terminal!  |
        | open terminal;  |
        | open terminal:  |

      Examples: Surrounding and repeated whitespace collapses
        | utterance          |
        |   open terminal    |
        | open    terminal   |

    Scenario: A pattern written with punctuation still matches plain speech
      Given mode "root mode" has the command "Open Terminal!" mapped to "alt + enter"
      When I say "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: Apostrophes and hyphens are not stripped
      # They can be meaningful inside a recognized word
      Given mode "root mode" has the command "what's up" mapped to "f1"
      When I say "what's up"
      Then the key sequence "f1↓ f1↑" is emitted

  Rule: Number words are replaced with digits before matching

    A recognizer returns "five", but a config author wants to write {numeric} and receive "5".
    Replacement happens on the utterance before pattern matching, for the words zero through ten.

    Scenario Outline: Number words become digits
      When I say "go to line <spoken>"
      Then the capture group 1 is "<digits>"

      Examples:
        | spoken | digits |
        | zero   | 0      |
        | one    | 1      |
        | five   | 5      |
        | nine   | 9      |
        | ten    | 10     |
        | 7      | 7      |

    Scenario: Number words are replaced only as whole words
      Given mode "root mode" has the command "press {any}" mapped to "{1}"
      When I say "press tone"
      Then the capture group 1 is "tone"

    Scenario: Multiple number words in one utterance are each replaced
      Given mode "root mode" has the command "go to {numeric} {numeric}" mapped to "{1} + {2}"
      When I say "go to four two"
      Then the capture group 1 is "4"
      And the capture group 2 is "2"

  Rule: Literal text in a pattern is never treated as a regex

    A pattern is spoken text. A config author who writes a regex metacharacter means it
    literally, and only placeholders have special meaning.

    Scenario Outline: Metacharacters in patterns are literal
      Given mode "root mode" has the command "<pattern>" mapped to "f1"
      When I say "<utterance>"
      Then the key sequence "f1↓ f1↑" is emitted

      Examples:
        | pattern      | utterance    |
        | c plus plus  | c plus plus  |
        | dot star     | dot star     |

    Scenario: A metacharacter pattern does not match arbitrary text
      Given mode "root mode" has the command "a.c" mapped to "f1"
      When I say "abc"
      Then no input is emitted

  Rule: Patterns are compiled once, at configuration load

    Scenario: Matching cost does not grow with utterance count
      # closes S6 — every utterance re-escapes and re-compiles a regex for every command in the
      # active mode today, putting a linear cost on the latency-sensitive path
      Given mode "root mode" has 500 commands
      When the configuration is loaded
      Then all 500 patterns are compiled
      When I say "open terminal"
      Then no pattern is compiled

    Scenario: Reloading recompiles patterns
      Given mode "root mode" has 3 commands
      When config.json is edited to add a command to mode "root mode"
      And I say "reload config"
      Then 4 patterns are compiled
