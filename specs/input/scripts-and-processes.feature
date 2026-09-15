@tinkerer
Feature: Running scripts and processes from a command

  Three verbs reach outside the keyboard: script() calls a Python function the user wrote,
  exec() launches a program, and python() evaluates an inline expression. Together they are what
  makes the app a scriptable voice layer rather than a fixed macro set.

  The user's own code is trusted by design. What matters here is that it is easy to write, that
  failures are reported, and that a broken script cannot take the app down.

  Background:
    Given the app is running with a vosk mode "root mode"
    And the active mode is "root mode"
    And the directory ~/.config/libre-dictum/scripts exists

  Rule: script() calls a function in the user's scripts directory

    Scenario: A script is imported from the config directory
      Given the script file ~/.config/libre-dictum/scripts/copy.py defines a function "script"
      And the command "duplicate" mapped to "script(copy)"
      When I say "duplicate"
      Then the function "script" in "copy" is called with no arguments

    Scenario: Arguments are passed separated by double semicolons
      Given the script file ~/.config/libre-dictum/scripts/notify.py defines a function "script"
      And the command "warn me" mapped to "script(notify;;hello;;world)"
      When I say "warn me"
      Then the function "script" in "notify" is called with "hello", "world"

    Scenario: A captured group becomes a script argument
      Given the command "type {rest}" mapped to "script(copy;;{1})"
      When I say "type hello world"
      Then the function "script" in "copy" is called with "hello world"

    Scenario: An argument containing a plus sign survives escaping
      Given the command "plus" mapped to "script(copy;;\+)"
      When I say "plus"
      Then the function "script" in "copy" is called with "+"

    Scenario: A missing script is reported at load time
      # closes C3-class validation: today the ImportError surfaces on the recognizer thread the
      # first time the command is spoken
      Given no script file named "missing" exists
      And the command "broken" mapped to "script(missing)"
      Then the configuration is rejected at load time
      And the error mentions "missing"

    Scenario: A script file without a script function is reported at load time
      Given the script file ~/.config/libre-dictum/scripts/empty.py defines no function "script"
      And the command "broken" mapped to "script(empty)"
      Then the configuration is rejected at load time
      And the error explains that a script module must define "script"

    @accessibility-critical
    Scenario: A script that raises does not kill the recognizer
      # closes C7-class robustness: an exception from user code propagates out of the callback
      # and terminates the recognizer thread today
      Given the script "explode" raises an exception when called
      And the command "boom" mapped to "script(explode)"
      When I say "boom"
      Then an error is logged naming the script "explode"
      And the error includes the script's traceback
      And the recognizer thread keeps running
      When I say "boom"
      Then an error is logged naming the script "explode"

    @accessibility-critical
    Scenario: A script that blocks does not freeze recognition
      Given the script "slow" takes 10 seconds to return
      And the command "wait" mapped to "script(slow)"
      When I say "wait"
      Then recognition continues to accept utterances

  Rule: exec() launches a program

    Scenario: A program is launched
      Given the command "open browser" mapped to "exec(firefox)"
      When I say "open browser"
      Then the process "firefox" is launched

    Scenario: Arguments are passed to the program
      Given the command "open site" mapped to "exec(firefox https://example.com)"
      When I say "open site"
      Then the process "firefox" is launched with argument "https://example.com"

    @tinkerer
    Scenario: A quoted argument containing spaces stays one argument
      # closes H4 — captured.split(" ") today, so this becomes three separate arguments
      Given the command "open file" mapped to "exec(xdg-open \"My Documents/notes.txt\")"
      When I say "open file"
      Then the process "xdg-open" is launched with argument "My Documents/notes.txt"

    @accessibility-critical
    Scenario: A program that does not exist is reported, not fatal
      Given the command "open nothing" mapped to "exec(does-not-exist)"
      When I say "open nothing"
      Then an error is logged mentioning "does-not-exist"
      And the recognizer thread keeps running

    @accessibility-critical
    Scenario: A long-running program does not block recognition
      # A launched program must not be waited on
      Given the command "open editor" mapped to "exec(gvim)"
      When I say "open editor"
      Then recognition continues to accept utterances immediately

    Scenario: A program's exit status is logged, not acted on
      Given the command "fail" mapped to "exec(false)"
      When I say "fail"
      Then the non-zero exit status is logged at debug level
      And no input is emitted

  Rule: python() evaluates inline code

    Scenario: Inline code runs
      Given the command "test command" mapped to "python(print('hello'))"
      When I say "test command"
      Then "hello" appears in the output

    @accessibility-critical
    Scenario: Inline code that raises does not kill the recognizer
      Given the command "boom" mapped to "python(1/0)"
      When I say "boom"
      Then an error is logged naming the command "boom"
      And the recognizer thread keeps running

    @design-decision
    Scenario: Inline code runs in an explicit namespace
      # closes H4 — exec() runs with the input handler's module globals today, so user config
      # can reach and mutate internal state by accident rather than by intent
      Given the command "peek" mapped to "python(print(keys_held))"
      When I say "peek"
      Then an error is logged mentioning "keys_held"
      And the recognizer thread keeps running

    @tinkerer
    Scenario: Inline code cannot span the plus separator unescaped
      Given the command "add" mapped to "python(print(1 \+ 2))"
      When I say "add"
      Then "3" appears in the output

  Rule: Verbs compose with keystrokes in one response

    Scenario: A script runs before the keystrokes that follow it
      Given the command "paste text" mapped to "script(copy;;hello) + ctrl + shift + v"
      When I say "paste text"
      Then the function "script" in "copy" is called with "hello"
      And then the key sequence "ctrl↓ shift↓ v↓ v↑ ctrl↑ shift↑" is emitted

    @accessibility-critical
    Scenario: A failing script does not stop the keystrokes after it
      Given the script "explode" raises an exception when called
      And the command "try it" mapped to "script(explode) + esc"
      When I say "try it"
      Then an error is logged naming the script "explode"
      And the key sequence "esc↓ esc↑" is emitted
