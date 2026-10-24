@tinkerer
Feature: Commands from other programs

  Another program runs a command by name over a unix socket. The configuration declares every
  name and what it runs; nothing else is accepted.

  Background:
    Given the configuration:
      """
      {
        "enable_pedals": true,
        "enable_control": true,
        "control": {
          "commands": {
            "break started": "mode(pedal:break pedals)",
            "break over": "mode(pedal:voice)"
          }
        },
        "modes": {
          "command mode": { "type": "vosk", "path": "models/small-en" },
          "mouse mode": { "type": "vosk", "path": "models/small-en" },
          "dictate mode": { "type": "transformer", "model_name": "whisper-turbo" },
          "break pedals": { "pedals": { "right": "space" } }
        }
      }
      """
    And the app is running

  Rule: A declared name runs its response

    Scenario: A program moves the pedal layer
      When a program sends "break started"
      Then the reply is "ok"
      And the pedal layer is in "break pedals"

    Scenario: Case and spacing do not matter
      When a program sends "Break   Started"
      Then the pedal layer is in "break pedals"

    Scenario: A sleeping mechanism does not stop it
      Given the pedals are asleep
      When a program sends "break started"
      Then the pedal layer is in "break pedals"

    Scenario: Its wake applies at once, whoever slept the mechanisms
      # No mechanism sent it, so it cannot be a misread, and a program that covers the screen
      # needs the mechanisms back
      Given the control command "break started" is mapped to "wake() + mode(pedal:break pedals)"
      And the pedals put every mechanism to sleep
      When a program sends "break started"
      Then nothing is asleep

  @design-decision
  Rule: A program can lock the mode switches

    While locked, a mechanism moves the voice layer alone and a program still moves any layer, so
    the layouts a program set stay set until it changes them (decision 32).

    Background:
      Given the control command "break locked" is mapped to "lock() + mode(pedal:break pedals)"
      And the control command "break unlocked" is mapped to "unlock() + mode(pedal:voice)"
      And a program sends "break locked"

    Scenario: A spoken mode name moves the voice layer alone
      When I say "mouse mode"
      Then the voice layer is in "mouse mode"
      And the pedal layer is in "break pedals"

    Scenario: A gesture's switch moves the voice layer alone
      When the gesture "smile" is mapped to "mode(mouse mode)" and fires
      Then the voice layer is in "mouse mode"
      And the pedal layer is in "break pedals"

    Scenario: A mechanism's move of a layer the program holds is refused
      When a gesture runs "mode(pedal:voice)"
      Then the pedal layer is in "break pedals"

    Scenario: A program still moves the layers
      When a program sends "break over"
      Then the pedal layer is in "command mode"

    Scenario: Unlocking lets a mechanism move every layer again
      When a program sends "break unlocked"
      And I say "mouse mode"
      Then the pedal layer is in "mouse mode"

    @accessibility-critical
    Scenario: Going back leads to the voice mode the program set
      # A one-step memory bounces between the two modes entered last, and neither of them is
      # the way out.
      When the gesture "smile" is mapped to "mode(mouse mode)" and fires
      And I say "dictate mode"
      And the voice layer runs "mode(previous mode)"
      Then the voice layer is in "command mode"

    @accessibility-critical
    Scenario: Asking for the voice layer's own mode again goes back
      # The asking layer stayed put, so its binding still names that mode: a toggle has to
      # toggle under the lock as well.
      When the gesture "smile" is mapped to "mode(mouse mode)" and fires
      And the gesture "smile" fires again
      Then the voice layer is in "command mode"
      And the pedal layer is in "break pedals"

    Scenario: Naming the voice layer's own mode qualified stays
      When I say "mouse mode"
      And a gesture runs "mode(voice:mouse mode)"
      Then the voice layer is in "mouse mode"

    Scenario: A program's voice switch is the new way back
      Given the control command "break symbols" is mapped to "mode(voice:symbol mode)"
      When a program sends "break symbols"
      And I say "mouse mode"
      And I say "previous mode"
      Then the voice layer is in "symbol mode"

    @accessibility-critical
    Scenario: A reload lifts the lock
      # A program that dies while locked would otherwise leave the lock for ever.
      When I say "reload config"
      And I say "mouse mode"
      Then the pedal layer is in "mouse mode"

  Rule: A program's commands apply while it holds the lock

    Background:
      Given the template "::break" maps "wall home" to "tell(home)"
      And the control command "break commands" is mapped to "lock(::break) + mode(pedal:break pedals)"
      And a program sends "break commands"

    Scenario: They work in every command mode
      When I say "mouse mode"
      And I say "wall home"
      Then every display is told "home"

    Scenario: They win over the mode's own
      Given "command mode" maps "wall home" to "ctrl + home"
      When I say "wall home"
      Then every display is told "home"
      And no key is emitted

    Scenario: A dictation mode types them
      When I say "dictate mode"
      And I say "wall home"
      Then no display is told anything

    Scenario: They end with the lock
      Given the control command "break unlocked" is mapped to "unlock() + mode(pedal:voice)"
      When a program sends "break unlocked"
      And I say "wall home"
      Then no display is told anything

    Scenario: Every command recognizer can hear them
      Then the grammar of "command mode" holds "wall home"

    Scenario: A lock naming no mode is refused
      Given the control command "go" is mapped to "lock(nonsense)"
      When the configuration is loaded
      Then loading fails with an error naming "not a defined mode"

  Rule: Anything else is refused

    Scenario: An undeclared name
      When a program sends "type hello"
      Then the reply starts with "error:"
      And no input is emitted

    Scenario: Control is off unless enabled
      Given "enable_control" is absent
      Then no control socket exists

    Scenario: A response that could never run
      Given the control command "go" is mapped to "mode(pedal:nonsense)"
      When the configuration is loaded
      Then loading fails with an error naming "Control command 'go'"

  Rule: The socket belongs to the session

    Scenario: Only this user can open it
      Then the control socket's mode is 0600

    Scenario: A reload switches it on and off
      Given "enable_control" is false
      When the configuration is reloaded
      Then no control socket exists

    Scenario: Shutdown refuses what arrives late
      Given the app is shutting down
      When a program sends "break started"
      Then the reply starts with "error:"
