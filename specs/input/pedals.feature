@pedals
Feature: Foot pedals as commands

  A USB HID pedal board answers with a fixed-length report in which one byte per pedal is zero
  while the pedal is up and nonzero while it is down. Which byte is which pedal is
  configuration, because the offsets are the only thing that differs between two boards of the
  same shape.

  Pedals matter because they cover what neither voice nor a face can: a foot can *stay there*.
  A held pedal is a modifier that costs no words, no expression and no hands — which is what
  makes it the mechanism a drag, a push-to-dictate and a temporary layer are built on.

  Only edges reach the executor. A board reports about a hundred times a second, and a foot
  resting on a pedal repeats the same report for as long as it stays there; a press that fired
  on every report would type a hundred characters.

  Background:
    Given the configuration:
      """
      {
        "enable_pedals": true,
        "pedal": {
          "vendor_id": "0x0fd9",
          "product_id": "0x0086",
          "buttons": { "left": 4, "middle": 5, "right": 6 },
          "report_length": 8
        },
        "modes": {
          "root mode": {
            "type": "vosk", "path": "models/small-en",
            "commands": { "feet scroll": "mode(pedal:scrolling)" },
            "pedals": {
              "left": { "press": "hold(left_mouse)", "release": "release(left_mouse)" },
              "middle": "enter"
            }
          },
          "scrolling": {
            "pedals": { "left": "3(up)", "right": "3(down)" }
          }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: A pedal fires on the edge, not on the report

    Scenario: Pressing a pedal runs its response
      When the pedal report is "left=0 middle=1 right=0"
      Then the key sequence "enter↓ enter↑" is emitted

    Scenario: Holding it does not fire again
      # The reason this is a state machine and not a translation.
      Given the pedal report was "left=0 middle=1 right=0"
      When the pedal report is "left=0 middle=1 right=0"
      Then no input is emitted

    Scenario: Any nonzero value is down
      # Nothing promises a pedal reports 1: on some boards the byte is a pressure or a count,
      # and only zero is reliably "up".
      When the pedal report is "left=0 middle=200 right=0"
      Then the key sequence "enter↓ enter↑" is emitted

    Scenario: Two pedals in one report fire in configuration order
      When the pedal report is "left=1 middle=1 right=0"
      Then the key sequence "left_mouse↓ enter↓ enter↑" is emitted

  Rule: Both edges are bindable, and that is what a foot is for

    Scenario: A bare response is the press
      When the pedal report is "left=0 middle=1 right=0"
      And the pedal report is "left=0 middle=0 right=0"
      Then the key sequence "enter↓ enter↑" is emitted

    @accessibility-critical
    Scenario: Press and release make a drag
      When the pedal report is "left=1 middle=0 right=0"
      Then the key sequence "left_mouse↓" is emitted
      When the pedal report is "left=0 middle=0 right=0"
      Then the key sequence "left_mouse↑" is emitted

    Scenario: A pedal the layout does not bind does nothing
      # Silence, not a warning: binding only some pedals is the common case.
      When the pedal report is "left=0 middle=0 right=1"
      Then no input is emitted

    @design-decision
    Scenario: A pedal explicitly bound to null does nothing
      # How a single binding inherited from a template is withdrawn without dropping the
      # template itself.
      Given the configuration:
        """
        {
          "enable_pedals": true,
          "modes": {
            "::feet": { "pedals": { "middle": "enter" } },
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "imports": ["::feet"],
              "pedals": { "middle": null }
            }
          }
        }
        """
      When the pedal report is "left=0 middle=1 right=0"
      Then no input is emitted

  @design-decision @accessibility-critical
  Rule: A run of bare taps is a toggle a modifier pedal can carry

    A pedal bound to `hold(ctrl)` is the one input that can be performed safely at any moment:
    ctrl down and up with nothing typed inside it does nothing in any application. A **bare tap**
    is therefore both harmless to perform and impossible to produce by working — every real ctrl
    press has a key inside it — and a run of them is a toggle nothing can fire by accident.

    That is what lets the three modifier pedals carry the sleep toggle without giving up any of
    their ordinary function. A key naming several pedals separated by spaces is the run; the taps
    themselves keep their own effect, with nothing suppressed and nothing delayed.

    A tap is *brief* as well as bare, and the clock matters: a modifier held to scroll-zoom or to
    click several things reaches this process as no key events whatsoever, because this process
    does not read the mouse.

    Background:
      Given the pedal layout:
        """
        {
          "left":        { "press": "hold(ctrl)", "release": "release(ctrl)" },
          "left left":   "sleep()",
          "right right": "wake()"
        }
        """

    Scenario: Two bare taps fire the run
      When the pedal "left" is tapped twice
      Then "sleep()" runs once

    Scenario: And the taps still hold the modifier they are bound to
      When the pedal "left" is tapped twice
      Then the key sequence "ctrl↓ ctrl↑ ctrl↓ ctrl↑" is emitted

    Scenario: A pedal in ordinary use is not a tap
      # ctrl+c twice over is a normal minute's work, and it must not be a run
      When the pedal "left" is pressed, "c" is typed, and it is released
      And that happens again
      Then "sleep()" does not run

    Scenario: A press held longer than tap_ms is not a tap
      # ctrl held to scroll-zoom: no key events reach this process at all
      Given "pedal.tap_ms" is 500
      When the pedal "left" is held for 600 milliseconds and released
      And that happens again
      Then "sleep()" does not run

    Scenario: Taps further apart than sequence_ms are not a run
      Given "pedal.sequence_ms" is 1000
      When the pedal "left" is tapped
      And two seconds pass
      And the pedal "left" is tapped
      Then "sleep()" does not run

    Scenario: The run is forgotten once it matched
      # Or the third tap would confirm the pair the first two armed
      When the pedal "left" is tapped three times
      Then "sleep()" runs once

    Scenario: A longer run fires when it is the one bound
      Given mode "root mode" binds "middle middle middle" to "hud(open)"
      When the pedal "middle" is tapped twice
      Then "hud(open)" does not run
      When the pedal "middle" is tapped again
      Then "hud(open)" runs

    Scenario: A shorter run bound alongside it gets there first
      # Documented rather than arbitrated: "left left" has already fired and cleared the run
      # by the time "left left left" could complete, so binding both is a mistake
      Given mode "root mode" also binds "left left left" to "hud(open)"
      When the pedal "left" is tapped three times
      Then "sleep()" runs once
      And "hud(open)" does not run

    @accessibility-critical
    Scenario: A release the device owes completes nothing
      # The binding still runs, because that is what stops hold(ctrl) sticking. It is not an act.
      When the pedal "left" is tapped
      And the pedal "left" is pressed
      And the pedal device stops answering
      Then the key sequence "ctrl↓ ctrl↑ ctrl↓ ctrl↑" is emitted
      And "sleep()" does not run

    Scenario: Each pedal is timed on its own
      # Two feet, or one foot rolling across two pedals
      When the pedal "left" is pressed and held
      And the pedal "right" is tapped
      Then the tap of "right" counts

    Scenario: A key naming one pedal is that pedal
      Given a pedal is called "left left" in "pedal.buttons"
      Then binding "left left" binds that pedal, not a run

    Scenario: A name that is neither is a load error
      Given mode "root mode" binds "left bogus"
      Then loading fails naming 'bogus' and listing the pedals the device has

  @accessibility-critical
  Rule: A release runs what its press captured

    A foot stays down across a mode switch, and an unqualified `mode(...)` moves all three layers
    by design — so a spoken mode name lands under a foot that has not come up yet. Looking the
    release edge up afresh then finds a layout that does not bind this pedal, and the
    `hold(left_mouse)` it started never comes back up.

    So the press remembers the layout and the binding it saw, and the release runs that. The same
    rule applies to a gesture — see [gestures](../head-tracking/gestures.feature), where it also
    frees a held shape to move its own layer.

    Scenario: A held pedal survives its own layer moving
      When the pedal report is "left=1 middle=0 right=0"
      And the pedal layer moves to a layout that does not bind "left"
      And the pedal report is "left=0 middle=0 right=0"
      Then the key sequence "left_mouse↓ left_mouse↑" is emitted

    Scenario: The captured release wins over the one bound now
      When the pedal report is "left=1 middle=0 right=0"
      And the pedal layer moves to a layout binding "left" to "up"
      And the pedal report is "left=0 middle=0 right=0"
      Then the key sequence "left_mouse↓ left_mouse↑" is emitted
      And no other key is emitted

    Scenario: No capture means no release
      Given the pedal layer is in a layout that does not bind "left"
      When the pedal report is "left=1 middle=0 right=0"
      And the pedal layer moves to "root mode"
      And the pedal report is "left=0 middle=0 right=0"
      Then no input is emitted

  @accessibility-critical
  Rule: A pedal the device took with it is released

    A pedal bound to `hold(left_mouse)` and a board unplugged mid-press would otherwise leave
    the button down with nothing on screen to say why every click has become a drag. Nothing
    else will ever send that release, so the reader owes it.

    Scenario: An unplugged board releases what was down
      Given the pedal report was "left=1 middle=0 right=0"
      When the pedal device stops answering
      Then the key sequence "left_mouse↑" is emitted
      And a warning is logged mentioning "was still down"

    Scenario: A truncated report is not read as a release
      # A short read is not evidence that a pedal moved, and treating it as one would release a
      # button still being held down.
      Given the pedal report was "left=1 middle=0 right=0"
      When a report arrives with only five bytes
      Then no input is emitted
      And the pedal "left" is still down

  @design-decision
  Rule: A board that goes away is retried, and comes back on the reload phrase

    The same bargain the camera makes (decision 11). A pedal board must never take voice
    control down with it, because voice may be the only way its user has to say the phrase that
    brings the pedals back.

    Scenario: A board that comes back is picked up
      When the pedal device stops answering
      And it answers again
      Then the pedals are live again
      And an info line is logged mentioning "recovered"

    Scenario: Retrying is bounded and says so
      When the pedal device never answers
      Then the pedals are retried 5 times with a growing delay
      And an error is logged mentioning "say the reload phrase to try again"
      And every mode is still listening

    Scenario: The reload phrase starts them again
      Given the pedals gave up
      When I say "reload config"
      Then the pedals are live again

    Scenario: A board that will not open names both of its causes
      # A missing device and an unwritable hidraw node fail identically and have different
      # fixes, so the message carries both.
      When the pedal device cannot be opened
      Then an error is logged mentioning "plugged in"
      And an error is logged mentioning "udev"
      And "pedals" is reported as failed

  @tinkerer
  Rule: What the board has is validated at load

    Scenario: A binding for a pedal that does not exist
      Given the configuration:
        """
        {
          "enable_pedals": true,
          "pedal": { "buttons": { "left": 4, "right": 6 } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "pedals": { "middle": "enter" }
            }
          }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "middle"
      And loading fails with an error naming "left" and "right"

    Scenario: An offset the report never reaches
      # Otherwise that pedal never fires, which looks exactly like a broken board.
      Given the configuration:
        """
        {
          "enable_pedals": true,
          "pedal": { "buttons": { "left": 9 }, "report_length": 8 },
          "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
        }
        """
      When the configuration is loaded
      Then loading fails with an error naming "report_length"

    Scenario: An edge that is not an edge
      Given the mode's pedal "left" is `{ "hold": "enter" }`
      When the configuration is loaded
      Then loading fails with an error naming "no other edge"

    Scenario: Both edges are validated as responses
      Given the mode's pedal "left" is `{ "press": "enter", "release": "bogus" }`
      When the configuration is loaded
      Then loading fails with an error naming "release"

    Scenario Outline: A USB id may be written the way lsusb prints it
      # JSON has no hexadecimal literal, and every source these identifiers are read from writes
      # them in hexadecimal, so retyping one in decimal is an avoidable transcription step.
      Given "pedal.vendor_id" is <written>
      When the configuration is loaded
      Then the pedal vendor id is 4057

      Examples:
        | written  |
        | "0x0fd9" |
        | 4057     |

    Scenario: A USB id that is neither says what it wanted
      Given "pedal.vendor_id" is "elgato"
      When the configuration is loaded
      Then loading fails with an error naming "0x0fd9"

  Rule: Pedals are off until they are switched on

    Scenario: No enable_pedals means no device is opened
      Given "enable_pedals" is absent
      When the app is running
      Then no pedal device is opened
      And the pedals page says "no pedal device configured (enable_pedals)"

    Scenario: A binding is not checked when there is no board
      # Nothing can fire it, so there is no typo to report -- the same bargain gestures make
      # without head tracking.
      Given "enable_pedals" is absent
      And the mode binds a pedal called "nonsense"
      When the configuration is loaded
      Then "root mode" is a runnable mode
