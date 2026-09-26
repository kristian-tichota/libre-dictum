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
