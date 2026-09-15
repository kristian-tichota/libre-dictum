@accessibility-critical
Feature: Keys held across commands

  Normally a chord releases everything it pressed. Sometimes a key needs to stay down past the
  end of the command: holding shift to select while navigating by voice, or holding ctrl while
  clicking several times.

  hold(), release() and toggle() manage that state. Because held state outlives the command that
  created it, it is the most dangerous state the app has: a modifier stuck down makes every
  subsequent keystroke wrong, and a user who cannot reach a keyboard has no way to clear it. So
  every scenario here is accessibility-critical, and a panic phrase always exists.

  Background:
    Given the app is running with a vosk mode "root mode"
    And the active mode is "root mode"
    And no key is held

  Rule: A held key stays down until released

    Scenario: hold presses a key and keeps it down
      Given the command "hold shift" mapped to "hold(shift)"
      When I say "hold shift"
      Then the key sequence "shift↓" is emitted
      And the key "shift" is held

    Scenario: A later command runs with the held key down
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "go right" mapped to "right"
      When I say "hold shift"
      And I say "go right"
      Then the key sequence "shift↓ right↓ right↑" is emitted
      And the key "shift" is held

    Scenario: release lets the key go
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "drop shift" mapped to "release(shift)"
      When I say "hold shift"
      And I say "go right"
      And I say "drop shift"
      Then the key sequence "shift↓ right↓ right↑ shift↑" is emitted
      And no key is held

    Scenario: Holding an already-held key does not press it twice
      Given the command "hold shift" mapped to "hold(shift)"
      When I say "hold shift"
      And I say "hold shift"
      Then the key sequence "shift↓" is emitted
      And the key "shift" is held

    Scenario: A non-modifier key can be held
      Given the command "hold space" mapped to "hold(space)"
      When I say "hold space"
      Then the key sequence "space↓" is emitted
      And the key "space" is held

    Scenario: A held modifier is not auto-released by a following chord
      # Auto-release applies to modifiers pressed as part of a chord, not to explicitly held ones
      Given the command "hold ctrl" mapped to "hold(ctrl)"
      And the command "copy" mapped to "ctrl + c"
      When I say "hold ctrl"
      And I say "copy"
      Then the key "ctrl" is held

  Rule: toggle flips whatever the current state is

    Scenario: Toggling an unheld key holds it
      Given the command "toggle shift" mapped to "toggle(shift)"
      When I say "toggle shift"
      Then the key sequence "shift↓" is emitted
      And the key "shift" is held

    Scenario: Toggling a held key releases it
      Given the command "toggle shift" mapped to "toggle(shift)"
      When I say "toggle shift"
      And I say "toggle shift"
      Then the key sequence "shift↓ shift↑" is emitted
      And no key is held

    Scenario: toggle and hold refer to the same state
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "toggle shift" mapped to "toggle(shift)"
      When I say "hold shift"
      And I say "toggle shift"
      Then the key sequence "shift↓ shift↑" is emitted
      And no key is held

  Rule: Releasing a key that is not held is harmless

    Scenario: A stray release is a no-op
      # closes C3 — keys_held.remove() raises ValueError today, killing the recognizer thread
      Given the command "drop shift" mapped to "release(shift)"
      When I say "drop shift"
      Then no input is emitted
      And a warning is logged mentioning "shift"
      And the recognizer thread keeps running

    Scenario: A double release is a no-op the second time
      # closes C3
      Given the command "hold shift" mapped to "hold(shift)"
      And the command "drop shift" mapped to "release(shift)"
      When I say "hold shift"
      And I say "drop shift"
      And I say "drop shift"
      Then the key sequence "shift↓ shift↑" is emitted
      And the recognizer thread keeps running

  @design-decision
  Rule: A panic phrase always releases everything

    This is the guaranteed hands-free escape hatch. It is reserved, it is in every mode's
    vocabulary, it outranks every other dispatch check, and it needs no configuration to exist.

    Scenario: The panic phrase releases every held key
      # closes C2 and S5 — no such phrase exists today
      Given the keys "ctrl" and "shift" are held
      When I say "release"
      Then the key sequence "ctrl↑ shift↑" is emitted
      And no key is held

    Scenario: The panic phrase also clears a chord's pending modifiers
      Given a chord left "ctrl" pressed
      When I say "release"
      Then the key sequence "ctrl↑" is emitted
      And no modifier is pressed

    Scenario: The panic phrase works with nothing held
      When I say "release"
      Then no input is emitted
      And the app reports that nothing was held

    Scenario: The panic phrase works from any mode
      Given the active mode is "dictate"
      And the key "ctrl" is held
      When I say "release"
      Then the key sequence "ctrl↑" is emitted

    Scenario: The panic phrase is renameable
      Given the configuration sets "panic_command" to "let go of everything"
      And the key "ctrl" is held
      When I say "let go of everything"
      Then the key sequence "ctrl↑" is emitted

    Scenario: The panic phrase also clears saved variables
      Given a saved variable "5"
      And the key "ctrl" is held
      When I say "release"
      Then no key is held
      And no saved variable remains

  Rule: Held state is shared across modes

    Held keys belong to the virtual keyboard, not to a mode. A key held in one mode is still
    held after switching, so a user can hold shift in a command mode and keep it held while
    dictating.

    Scenario: A held key survives a mode switch
      Given the command "hold shift" mapped to "hold(shift)"
      When I say "hold shift"
      And I say "dictate"
      Then the key "shift" is held

    Scenario: A key held in one mode can be released from another
      Given the command "hold shift" mapped to "hold(shift)"
      And mode "dictate" has the command "drop shift" mapped to "release(shift)"
      When I say "hold shift"
      And I say "dictate"
      And I say "drop shift"
      Then the key sequence "shift↓ shift↑" is emitted
      And no key is held

  @concurrency
  Rule: Concurrent input is serialised

    Three kinds of thread can emit input: each mode's recognizer, and the head-tracking gesture
    thread. They share one virtual keyboard and one set of held keys.

    Scenario: A gesture firing mid-command does not interleave with it
      # closes C2 — keys_held, modifiers_held and the UInput handles are unsynchronised today,
      # so a gesture landing between a chord's press and its release can drain modifiers_held
      # and leave a modifier stuck down
      Given the command "copy" mapped to "ctrl + c"
      And the gesture "blink" mapped to "esc"
      When I say "copy"
      And the gesture "blink" fires while the command is executing
      Then the key sequence "ctrl↓ c↓ c↑ ctrl↑ esc↓ esc↑" is emitted
      And no key is held

    Scenario: Two recognizers firing at once do not interleave
      # closes C2
      Given the command "copy" mapped to "ctrl + c"
      And mode "dictate" would emit "ctrl + v"
      When both responses are executed at the same moment
      Then the emitted events form two complete chords
      And no modifier remains pressed

    Scenario: Held state changes are atomic
      # closes C2
      Given the command "hold shift" mapped to "hold(shift)"
      When "hold shift" and the panic phrase are processed at the same moment
      Then the held key set is either exactly {shift} or empty
      And no key is left physically down while absent from the held set
