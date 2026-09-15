Feature: Three independent input layers

  Voice, gestures and the pedals are independent mechanisms, and each keeps its own pointer
  into the single `modes` table. The voice pointer decides which recognizer is listening and
  which command table applies; the gesture pointer decides which mode's `gestures` apply; the
  pedal pointer decides which mode's `pedals` apply.

  Independence is the feature, and cross-modification is its purpose: a gesture can move the voice
  layer, a pedal can move the voice layer, and a voice command can move the pedal layer. That
  yields a much larger alphabet than any one mechanism has on its own, because three pedals become
  a different three in every layout and a layout can outlast a mode switch.

  A `mode(...)` says which layer it means by qualifying the target. Unqualified moves all
  three, which is what every configuration written before layers existed already meant.

  Background:
    Given the configuration:
      """
      {
        "enable_pedals": true,
        "enable_head_tracking": true,
        "ht_model_path": "models/face_landmarker.task",
        "starting_mode": "command mode",
        "modes": {
          "command mode": {
            "type": "vosk", "path": "models/small-en",
            "commands": {
              "feet scroll": "mode(pedal:scrolling)",
              "dictate now": "mode(voice:dictate mode)"
            },
            "gestures": { "smile": "mode(mouse mode)" },
            "pedals": { "left": "left_mouse", "right": "mode(voice:mouse mode)" }
          },
          "mouse mode": {
            "type": "vosk", "path": "models/small-en", "ht_enabled": true,
            "gestures": { "smile": "mode(command mode)" }
          },
          "dictate mode": {
            "type": "transformer", "model_name": "whisper-turbo",
            "commands": { "{rest}": "type({1})" }
          },
          "scrolling": {
            "pedals": { "left": "3(up)", "right": "3(down)" }
          }
        }
      }
      """
    And the app is running

  Rule: All three layers start together

    Scenario: Nothing said about layers starts them all in the starting mode
      Then the voice layer is in "command mode"
      And the gesture layer is in "command mode"
      And the pedal layer is in "command mode"

    Scenario: A layer can be pinned somewhere else at startup
      Given "starting_pedal_mode" is "scrolling"
      When the app is running
      Then the voice layer is in "command mode"
      And the pedal layer is in "scrolling"

    @design-decision
    Scenario: An unpinned layer follows the mode that started
      # `starting_mode` is one thing and where voice landed is another: a model that will not
      # load costs its mode, and the layers have to go where the session went.
      Given "starting_mode" is "mouse mode"
      And the recognizer for "mouse mode" will not start
      When the app is running
      Then the voice layer is in "command mode"
      And the gesture layer is in "command mode"

    Scenario: A layer whose subsystem is off does not exist
      Given "enable_pedals" is absent
      Then the pedal layer is not reported
      And the chip shows no layer line

  @design-decision
  Rule: An unqualified switch moves all three

    This is what makes every configuration written before layers existed behave exactly as it
    did: `"smile": "mode(mouse mode)"` still takes the whole session to mouse mode, and still
    toggles back from there.

    Scenario: A bare mode name takes every layer with it
      When I say "mouse mode"
      Then the voice layer is in "mouse mode"
      And the gesture layer is in "mouse mode"
      And the pedal layer is in "mouse mode"

    Scenario: A gesture that switches takes every layer with it
      When the gesture "smile" fires
      Then the voice layer is in "mouse mode"
      And the gesture layer is in "mouse mode"

    Scenario: And so the gesture still toggles
      When the gesture "smile" fires
      And the gesture "smile" fires
      Then the voice layer is in "command mode"

    Scenario: A mode with nothing for a layer still takes it
      # One rule and no exceptions: a mode with no `pedals` means the pedals do nothing there,
      # exactly as a mode with no `gestures` always has.
      Given the pedal layer is in "scrolling"
      When I say "mouse mode"
      Then the pedal layer is in "mouse mode"
      And the pedal "left" does nothing

    @accessibility-critical
    Scenario: A hand or a foot that is still down keeps its way back
      # A bare mode name can arrive under a held shape, and looking the release up in the new
      # mode would strand it. The press captures its own release for this.
      Given the gesture "fist" is held and holding "left_mouse" down
      When I say "mouse mode"
      And the gesture "fist" is re-armed
      Then the button "left_mouse" comes up

    @accessibility-critical
    Scenario: A target whose recognizer is deaf moves nothing at all
      # Half a switch is worse than none. The voice layer is the only layer that can refuse,
      # and it refuses on behalf of all three: a recognizer that cannot hear cannot hear the
      # phrase that leaves it either.
      Given the recognizer for "mouse mode" dies with "its thread died"
      When I say "mouse mode"
      Then the voice layer is in "command mode"
      And the pedal layer is in "command mode"
      And a warning is logged mentioning "unavailable"

  Rule: A qualified switch moves one layer

    Scenario: Voice can move the pedals
      When I say "feet scroll"
      Then the pedal layer is in "scrolling"
      And the voice layer is in "command mode"
      And the pedal "left" emits the key sequence "up↓ up↑ up↓ up↑ up↓ up↑"

    Scenario: A pedal can move the voice layer
      When the pedal "right" is pressed
      Then the voice layer is in "mouse mode"
      And the pedal layer is in "command mode"

    Scenario: A gesture can move the voice layer alone
      Given the gesture "smile" is mapped to "mode(voice:mouse mode)"
      When the gesture "smile" fires
      Then the voice layer is in "mouse mode"
      And the gesture layer is in "command mode"

    Scenario: A pedal layout outlasts a voice switch
      # The whole reason for qualifying: a scrolling layout that survives hopping between
      # modes for one symbol and back.
      When I say "feet scroll"
      And I say "dictate now"
      Then the voice layer is in "dictate mode"
      And the pedal layer is in "scrolling"

    @design-decision
    Scenario: A layer may sit on a mode that has no recognizer
      # A pedal layout needs no model, and requiring one would mean loading a multi-gigabyte
      # file to change what a foot does. "scrolling" has no `type`, so it never listens.
      When I say "feet scroll"
      Then the pedal layer is in "scrolling"
      And "scrolling" is not a runnable mode

    Scenario: The voice layer may not
      Given the command "go" is mapped to "mode(voice:scrolling)"
      When the configuration is loaded
      Then loading fails with an error naming "not a runnable mode"

    Scenario: Neither may an unqualified target
      Given the command "go" is mapped to "mode(scrolling)"
      When the configuration is loaded
      Then loading fails with an error naming "not a runnable mode"

    Scenario: A layer target that names no mode at all
      Given the command "go" is mapped to "mode(pedal:nonsense)"
      When the configuration is loaded
      Then loading fails with an error naming "not a defined mode"

  @design-decision
  Rule: The enter and exit commands belong to the voice layer

    There is one session to set up, not three. A mode that all three layers enter at once would
    otherwise run its `enter_command` three times.

    Scenario: Moving one layer runs neither
      Given "mouse mode" has the enter command "f1"
      When the gesture "smile" is mapped to "mode(gesture:mouse mode)" and fires
      Then no input is emitted

    Scenario: An unqualified switch runs them once
      Given "mouse mode" has the enter command "f1"
      When I say "mouse mode"
      Then the key sequence "f1↓ f1↑" is emitted

  Rule: Every layer remembers where it came from

    Scenario: Going back on the pedals leaves the voice layer alone
      When I say "feet scroll"
      And the pedal "left" is mapped to "mode(pedal:previous mode)" and is pressed
      Then the pedal layer is in "command mode"
      And the voice layer is in "command mode"
      And no previous voice mode is remembered

    Scenario: An unqualified go-back takes each layer to its own previous
      When I say "feet scroll"
      And I say "mouse mode"
      And I say "previous mode"
      Then the voice layer is in "command mode"
      And the pedal layer is in "scrolling"

  Rule: A reload keeps every mechanism live

    Scenario: A layer whose mode was deleted falls back to the voice layer
      # A pointer at a mode that no longer exists is a mechanism gone quietly inert -- for the
      # pedals, a foot that does nothing with nothing on screen saying why.
      Given the pedal layer is in "scrolling"
      When config.json is replaced without "scrolling"
      Then the pedal layer is in "command mode"
      And a warning is logged mentioning "is gone after the reload"

    Scenario: A layer whose mode survived stays put
      Given the pedal layer is in "scrolling"
      When I say "reload config"
      Then the pedal layer is in "scrolling"

  Rule: Where every layer is, is visible

    Scenario: While they agree the chip says nothing extra
      # A row that is always there says nothing by being there.
      Then the chip shows "command mode"
      And the chip shows no layer line

    Scenario: A layer elsewhere gets a line naming it
      When I say "feet scroll"
      Then the chip shows "command mode"
      And the chip's layer line reads "pedal: scrolling"

    Scenario: Both layers elsewhere read in column order
      When I say "feet scroll"
      And the gesture layer moves to "mouse mode"
      Then the chip's layer line reads "gesture: mouse mode · pedal: scrolling"

    Scenario: The tray paints an arc for a layer that has moved
      When I say "feet scroll"
      Then the tray icon still shows the "command mode" colour
      And the tray icon shows an arc in the "scrolling" colour
      And the tray tooltip says "voice: command mode, gesture: command mode, pedal: scrolling"

    Scenario: The modes page reads each column off that mechanism's layer
      # Reading the pedal column from the voice mode would draw pedals that are not reachable.
      Given the pedal layer is in "scrolling"
      When I say "overlay modes"
      Then the modes page has no "pedal" column

    Scenario: A switch that moves only a layer is not a row on the modes page
      # "feet scroll" moves the pedals, not the session. Drawing it as a transition between
      # modes would promise a mode switch that never happens.
      When I say "overlay modes"
      Then the modes page does not offer "feet scroll" as a way to "scrolling"
      And the modes page does not list a row "scrolling"
