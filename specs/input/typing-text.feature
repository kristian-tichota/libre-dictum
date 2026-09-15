Feature: Typing text without a clipboard

  Dictated text has to reach the screen. The old way was to put it on the clipboard and send a
  paste chord, and there is no paste chord that works everywhere: ctrl+v pastes in a GUI
  application but is quoted-insert in a terminal and scroll-up-command in Emacs, while
  ctrl+shift+v pastes in a terminal and is unbound in Emacs. A response can only name one of
  them, so it misfires in whatever the author did not have open when they wrote it.

  type() presses the keys that spell the text instead. It has no target application, so it works
  in every one of them, in a TTY and over SSH, and it leaves the clipboard and the primary
  selection alone.

  What it costs is the layout: uinput emits key codes, and which glyph appears is decided by the
  compositor's keymap. The table is a US layout and nothing can detect that it is wrong.

  Background:
    Given the app is running with a vosk mode "root mode"
    And the active mode is "root mode"
    And no key is held

  Rule: The text that was asked for is the text that is typed

    Scenario: A word is typed one key at a time
      Given the command "say hi" mapped to "type(hi)"
      When I say "say hi"
      Then the key sequence "h↓ h↑ i↓ i↑" is emitted

    Scenario: A capital holds shift over its own letter
      Given the command "say hi" mapped to "type(Hi)"
      When I say "say hi"
      Then the key sequence "shift↓ h↓ h↑ shift↑ i↓ i↑" is emitted

    Scenario: A run of capitals presses shift once
      Given the command "shout" mapped to "type(ABC)"
      When I say "shout"
      Then shift is pressed once and released once
      And the typed text is "ABC"

    Scenario: Nothing reaches the clipboard
      Given the command "say hi" mapped to "type(hi)"
      When I say "say hi"
      Then the clipboard is untouched
      And the primary selection is untouched

  Rule: Recognized text is never read as syntax

    A plus separates tokens in a response, and a dictation model writes "9 + 10". Substituting
    that into a response unescaped would split it mid-sentence, and because a response runs whole
    or not at all, the utterance would be discarded with only a log line to show for it.

    Scenario: A plus in dictated text is typed, not obeyed
      Given a dictation mode "dictate mode" with the command "{rest}" mapped to "type({1})"
      And the active mode is "dictate mode"
      When the recognizer reports "9 + 10 = 19"
      Then the typed text is "9 + 10 = 19"

    Scenario: A plus written in the config is still a separator
      Given the command "chord" mapped to "ctrl + c"
      When I say "chord"
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑" is emitted

    Scenario: A plus written in the config can be escaped as text
      Given the command "add" mapped to "type(9 \+ 10)"
      When I say "add"
      Then the typed text is "9 + 10"

  Rule: Characters a keyboard does not have are handled where the author can be told

    Scenario: Typographic punctuation is folded to ASCII
      Given a dictation mode "dictate mode" with the command "{rest}" mapped to "type({1})"
      And the active mode is "dictate mode"
      When the recognizer reports "He said “hi” — really…"
      Then the typed text is "He said \"hi\" - really..."

    Scenario: An accent is dropped rather than the word
      Given a dictation mode "dictate mode" with the command "{rest}" mapped to "type({1})"
      And the active mode is "dictate mode"
      When the recognizer reports "café"
      Then the typed text is "cafe"

    Scenario: What has no key at all is left out of dictated text, loudly
      Given a dictation mode "dictate mode" with the command "{rest}" mapped to "type({1})"
      And the active mode is "dictate mode"
      When the recognizer reports "the 好 word"
      Then the typed text is "the  word"
      And a warning names the character "好"

    @tinkerer
    Scenario: A literal type() the keyboard cannot produce is rejected at load time
      Given the command "do it" mapped to "type(hi 好)"
      Then the configuration is rejected at load time
      And the error names the command "do it"
      And the error mentions "好"

    @tinkerer
    Scenario: A dictated type() cannot be checked at load time
      Given the command "{rest}" mapped to "type({1})"
      Then the configuration is accepted

  Rule: Typing gives back the shift it found

    A modifier stuck down makes every later keystroke wrong, and a user who cannot reach a
    keyboard has no way to clear it. type() changes the shift state per character, so it has to
    leave it exactly as it was -- both when shift was held on purpose and when it was not.

    @accessibility-critical
    Scenario: A held shift is put back after typing lowercase
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "say ab" mapped to "type(ab)"
      When I say "hold shift"
      And I say "say ab"
      Then the key sequence "shift↓ shift↑ a↓ a↑ b↓ b↑ shift↓" is emitted
      And the key "shift" is held

    @accessibility-critical
    Scenario: A held shift is not pressed twice for a capital
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "shout" mapped to "type(AB)"
      When I say "hold shift"
      And I say "shout"
      Then the key sequence "shift↓ a↓ a↑ b↓ b↑" is emitted
      And the key "shift" is held

    @accessibility-critical
    Scenario: Typing leaves no shift behind when none was held
      Given the command "say hi" mapped to "type(Hi!)"
      When I say "say hi"
      Then no key is held

    Scenario: A pending modifier is consumed as any keystroke consumes it
      Given the command "control hi" mapped to "ctrl + type(hi)"
      When I say "control hi"
      Then the key "ctrl" is released at the end
      And no key is held

  Rule: Typing is paced separately from chords

    Every key event goes out under the executor lock, which is the lock the panic phrase needs.
    A chord is a handful of events and 10ms apiece is imperceptible; a dictated sentence is
    hundreds, and 10ms apiece would hold that lock for seconds.

    Scenario: A character costs type_delay, not input_delay
      Given mode "root mode" has "input_delay" of 0.5 and "type_delay" of 0.001
      And the command "say abc" mapped to "type(abc)"
      When I say "say abc"
      Then the delay between characters is 0.001

    Scenario: type_delay defaults well below input_delay
      Then the default "type_delay" is 0.002
      And the default "input_delay" is 0.01
