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

    While locked, every switch a mechanism asks for is refused and a program's still runs, so
    the layouts a program set stay set until it changes them (decision 32).

    Background:
      Given the control command "break locked" is mapped to "lock() + mode(pedal:break pedals)"
      And the control command "break unlocked" is mapped to "unlock() + mode(pedal:voice)"
      And a program sends "break locked"

    Scenario: A spoken mode name switches nothing
      When I say "mouse mode"
      Then the voice layer is in "command mode"
      And the pedal layer is in "break pedals"

    Scenario: A gesture's switch is refused
      When the gesture "smile" is mapped to "mode(mouse mode)" and fires
      Then the voice layer is in "command mode"

    Scenario: A program still moves the layers
      When a program sends "break over"
      Then the pedal layer is in "command mode"

    Scenario: Unlocking lets every mechanism switch again
      When a program sends "break unlocked"
      And I say "mouse mode"
      Then the voice layer is in "mouse mode"

    @accessibility-critical
    Scenario: A reload lifts the lock
      # A program that dies while locked would otherwise leave the lock for ever.
      When I say "reload config"
      And I say "mouse mode"
      Then the voice layer is in "mouse mode"

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
