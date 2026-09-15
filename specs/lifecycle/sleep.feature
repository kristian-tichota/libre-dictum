@lifecycle @accessibility-critical
Feature: Putting a mechanism beyond reach

  A voice, a face and a foot are always on. Eating, holding a conversation, or reading with a hand
  against the chin are each a few minutes during which every mechanism is still watching, and the
  cost of one misread shape is a keystroke in whatever window holds focus.

  A layer is therefore either awake or **asleep**, which is a fourth state beside its mode
  pointer. The events of a sleeping mechanism are dropped before any binding is looked up, so
  there is nothing to bind wrongly, and its pointer does not move, so waking restores the
  previous mode.

  This could not be a mode. Every mode name is in every VOSK grammar by design, so a mode can
  always be left hands-free, and a spoken mode name is acted on before the command table — so a
  mode that bound nothing would still switch modes whenever anything in the room said one.
  Gestures and pedals could imitate sleep with an empty template; the voice layer could not, and
  the three mechanisms have to mean the same thing.

  **The toggle itself is the dangerous part.** A shape that puts the session to sleep is a shape
  that can be misread, and a shape that wakes it is worse, because it restores every mechanism at
  once. No sleep and no wake therefore happens on the first request.

  Background:
    Given the configuration:
      """
      {
        "enable_pedals": true,
        "enable_head_tracking": true,
        "ht_model_path": "models/face_landmarker.task",
        "ht_custom_gestures": {
          "both_open": { "bothHandsPresent": { "min": 0.5 } },
          "left_open": { "leftHandOpen":     { "min": 0.9 } },
          "fist":      { "handFist":         { "min": 0.8 } }
        },
        "modes": {
          "::sleep": {
            "commands": { "go to sleep": "sleep()", "wake up": "wake()" },
            "gestures": { "both_open": "sleep(gesture)", "left_open": "wake(gesture)" }
          },
          "root mode": {
            "type": "vosk", "path": "models/small-en", "ht_enabled": true,
            "imports": ["::sleep"],
            "commands": { "open terminal": "alt + enter", "overview": "hud(open)" },
            "gestures": { "fist": { "press": "hold(left_mouse)", "release": "release(left_mouse)" } },
            "pedals": { "left": "enter" }
          },
          "dictate mode": { "type": "transformer", "model_name": "whisper-turbo" }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: Nothing happens on the first request

    A request *arms*, and only the **same** request repeated inside `sleep_confirm_seconds` applies
    it. Two independent misfires inside five seconds rather than one, which is the difference
    between unlikely and inevitable on a mechanism that reads a face sixty times a second.

    Scenario: One ask changes nothing
      When I say "go to sleep"
      Then nothing is asleep
      And the display says "sleep? again to confirm"

    Scenario: The same ask again applies it
      When I say "go to sleep"
      And I say "go to sleep"
      Then every mechanism is asleep

    Scenario: A repeat after the window arms again instead
      Given I said "go to sleep"
      When six seconds pass
      And I say "go to sleep"
      Then nothing is asleep

    Scenario: A different request re-arms rather than confirming
      # Nothing may be confirmed that was not asked for: one ask for the gestures and one for
      # everything is not two asks for either
      Given the gesture "both_open" fired
      When I say "go to sleep"
      Then nothing is asleep

    Scenario: Either half may come from any mechanism
      # Deliberate. Two independent acts is the whole guard, and arming with a gesture and
      # confirming with a foot is one of the safer ways to spell it
      Given the gesture "both_open" fired
      When the pedal bound to "sleep(gesture)" goes down
      Then the gesture layer is asleep

    Scenario: The arming expires on its own
      Given I said "go to sleep"
      When six seconds pass
      Then the display says nothing about sleep

  Rule: sleep and wake are deterministic, never a toggle

    An accidental repeat is then a no-op rather than a reversal. Bind both to the same mechanism
    and they sit side by side: the sleep one is inert while asleep and the wake one while awake —
    which also lets the wake action be *harder* to perform than the sleep one, and it should be.

    Scenario: Sleeping what is already asleep does nothing
      Given the gesture layer is asleep
      When the gesture "both_open" fires twice
      Then the gesture layer is asleep

    Scenario: Waking what is already awake does nothing
      When I say "wake up"
      And I say "wake up"
      Then nothing is asleep
      And the display says nothing about sleep

    Scenario: Applying a request leaves nothing armed
      # Or a third ask would toggle straight back out
      When I say "go to sleep"
      And I say "go to sleep"
      And I say "wake up"
      Then every mechanism is asleep

  Rule: A sleeping mechanism keeps sleep, wake and hud, and loses everything else

    One sentence: it can manage sleep and the display, and it cannot touch the machine.

    Scenario: A sleeping gesture layer runs no gesture
      Given the gesture layer is asleep
      When the gesture "fist" fires
      Then no input is emitted

    Scenario: The other mechanisms carry on
      Given the gesture layer is asleep
      When I say "open terminal"
      Then the key sequence "alt↓ enter↓ enter↑ alt↑" is emitted

    Scenario: A sleeping pedal layer runs no pedal
      Given the pedal layer is asleep
      When the pedal "left" goes down
      Then no input is emitted

    Scenario: A sleeping voice layer runs no command
      Given the voice layer is asleep
      When I say "open terminal"
      Then no input is emitted
      And the display shows the utterance as unmatched

    Scenario: Nor switches modes on a spoken mode name
      # The reason sleep needs code at all
      Given the voice layer is asleep
      When I say "dictate mode"
      Then the active mode is "root mode"

    Scenario: Nor on the previous-mode keyword
      Given the active mode is "dictate mode"
      And the voice layer is asleep
      When I say "previous mode"
      Then the active mode is "dictate mode"

    Scenario: The wake action still works
      Given the gesture layer is asleep
      When the gesture "left_open" fires twice
      Then nothing is asleep

    Scenario: And a display phrase
      # Seeing where a session is changes nothing about it, and being unable to look is worse
      # than a sheet opening by accident
      Given the voice layer is asleep
      When I say "overview"
      Then the sheet is showing

    Scenario: A mixed chain is dropped whole
      # A response that ran half of itself would be worse than one that did nothing, and a
      # sleeping mechanism typing a word is the exact thing sleep exists to prevent
      Given mode "root mode" maps "wake and type" to "wake() + type(hello)"
      And the voice layer is asleep
      When I say "wake and type"
      Then no input is emitted
      And the voice layer is asleep

    Scenario: An alias cannot smuggle anything past it
      Given an alias rewrites "nap" as "wake() + ctrl + c"
      And the voice layer is asleep
      When I say "nap"
      Then no input is emitted

  @design-decision @accessibility-critical
  Rule: Whoever slept it wakes it

    A sleep entered with the feet may be undone only by the feet. That is the purpose of using the
    feet: the voice and the camera are not to be relied on for the next few minutes, and a sleep
    either of them could undo would not be worth entering. The rule is general rather than a pedal
    exception, so that every route is definitive in the same way.

    The mechanism that **confirms** a sleep owns it — arming may come from anywhere, and the ask
    that applied it is the one that counts.

    Scenario: A pedal sleep is not undone by voice
      Given the pedals put every mechanism to sleep
      When I say "wake up"
      And I say "wake up"
      Then every mechanism is asleep

    Scenario: Nor by a gesture
      Given the pedals put every mechanism to sleep
      When the gesture bound to "wake()" fires twice
      Then every mechanism is asleep

    Scenario: And the pedals still wake it
      Given the pedals put every mechanism to sleep
      When the pedal run bound to "wake()" is performed twice
      Then nothing is asleep

    Scenario: A spoken sleep is not undone by the pedals
      Given I said "go to sleep" twice
      When the pedal run bound to "wake()" is performed twice
      Then every mechanism is asleep

    Scenario: A refusal arms nothing
      # Or two refused asks would add up to a wake, which is the opposite of the point
      Given the pedals put every mechanism to sleep
      When I say "wake up"
      And I say "wake up"
      And I say "wake up"
      Then every mechanism is asleep

    Scenario: Half a wake is no wake
      # It would leave the session in a state that was not requested, with nothing reporting
      # which half moved
      Given a gesture put the gesture layer to sleep
      And the pedals put the pedal layer to sleep
      When the gesture bound to "wake()" fires twice
      Then both are still asleep

    Scenario: A refusal is not silent
      Given the pedals put every mechanism to sleep
      When I say "wake up"
      Then a warning is logged mentioning "refused"
      And the display says the pedals are what wakes it

    Scenario: And which mechanism to use is on screen before anybody tries the wrong one
      Given the pedals put every mechanism to sleep
      Then the chip says "wake with pedal"

  Rule: The panic and reload phrases are never taken away

    Scenario: The panic phrase still releases everything
      # Panic only ever lets go of things, so it is harmless when a television says it
      Given the voice layer is asleep
      And the key "shift" is held
      When I say "release everything"
      Then no key is held

    Scenario: Panic does not wake
      # Short and common enough to be a misfire vector, which is exactly why it may not be
      # the way back
      Given every mechanism is asleep
      When I say "release everything"
      Then every mechanism is asleep

    Scenario: A successful reload wakes everything
      # The guaranteed way back: a configuration whose wake binding was edited away would
      # otherwise be a session nothing could reach
      Given every mechanism is asleep
      When I say "reload config"
      Then nothing is asleep

    Scenario: And it answers to nobody
      # A sleep only the pedals can undo is a session an unplugged board would close for good
      Given the pedals put every mechanism to sleep
      When I say "reload config"
      Then nothing is asleep

    Scenario: A rejected reload leaves it asleep
      Given every mechanism is asleep
      When config.json is edited to something invalid
      And I say "reload config"
      Then every mechanism is asleep

  Rule: Nothing is left stranded

    Scenario: Going to sleep releases what was held
      # A fist holding a button as sleep begins would otherwise keep it down for ever: the
      # gate refuses the very release that would end it
      Given the gesture "fist" is held and holding "left_mouse" down
      When I say "go to sleep"
      And I say "go to sleep"
      Then the button "left_mouse" comes up

    Scenario: And drops the release it had captured
      Given the gesture "fist" is held and holding "left_mouse" down
      And every mechanism is asleep
      When everything is woken
      And the gesture "fist" is re-armed
      Then no input is emitted

    Scenario: A press that was dropped leaves no release behind
      # Or waking would fire the second half of something that never had a first
      Given the gesture layer is asleep
      When the gesture "fist" fires
      And the gesture layer is woken
      And the gesture "fist" is re-armed
      Then no input is emitted

  Rule: The pointer is parked only when everything is asleep

    A pointer drifting on its own is half of an accidental click, so "nothing can act" has to
    include the mouse. Sleeping one mechanism is not a reason to stop the head driving it.

    Scenario: Everything asleep stops the pointer
      Given every mechanism is asleep
      When the head turns 20 degrees
      Then the pointer does not move

    Scenario: One mechanism asleep leaves it alone
      Given the gesture layer is asleep
      When the head turns 20 degrees
      Then the pointer moves

  Rule: Sleep is visible, because a quiet session looks exactly like a broken one

    Nothing about a session that is not responding says whether it is listening, and a user who
    cannot tell will keep performing a gesture that is being dropped.

    Scenario: The chip says which mechanisms are asleep
      Given the gesture layer is asleep
      Then the chip says "asleep: gesture"

    Scenario: The mode dot dims, the way the tray disc does
      # The quietest thing a chip can say and the one that needs no reading. The two surfaces
      # dim by the same amount so they cannot disagree about what a quiet session looks like.
      Given every mechanism is asleep
      Then the chip's mode dot is the mode's colour with the light turned down

    Scenario: It is not drawn as a fault
      # A mechanism deliberately put beyond reach is not broken, and alarm red would make the
      # one state the user chose look like the one they need to fix
      Given every mechanism is asleep
      Then the sleep line is italic and dim, and the fault line is empty

    Scenario: A confirmation waiting to be repeated clears itself
      # The window closes on its own and nothing else is watching, so a prompt left on screen
      # would keep asking long after the chance had gone -- which is worse than saying nothing
      Given I said "go to sleep"
      When the confirmation window passes
      Then the display says nothing about sleep

    Scenario: Everything asleep needs no list
      Given every mechanism is asleep
      Then the chip says "ASLEEP"

    Scenario: The tray dims rather than recolouring
      # Every colour a sleep indicator could pick is one a mode may configure for itself
      Given every mechanism is asleep
      Then the tray icon is the same disc with the light turned down
      And the tooltip says "asleep"

    Scenario: A faulted mechanism that is also asleep is still faulted
      Given the recognizer for "root mode" has died
      And every mechanism is asleep
      Then the tray icon is the fault disc, dimmed

    Scenario: The mode pointers are still shown
      # Waking restores this mode, and a chip that forgot it would make sleep look like a
      # mode switch
      Given every mechanism is asleep
      Then the chip says the active mode is "root mode"

  @design-decision
  Rule: A full sleep may take the display with it, and gives it back to be woken

    Sleep is for a session somebody is stepping away from, and a chip lit in the corner of a
    film is the one thing about it nobody can ignore. So 'overlay.sleep.hide' exists, is off
    by default -- a display that vanishes looks exactly like one that crashed, which is the
    problem the chip was drawn to solve -- and when it is on it applies only once **every**
    mechanism is asleep. One mechanism resting is a session that is still working and still
    has something to say.

    Scenario: Everything asleep takes both surfaces away
      Given "overlay.sleep.hide" is on and the sheet is open
      When every mechanism goes to sleep
      Then neither surface is drawn

    Scenario: One mechanism asleep leaves the display alone
      Given "overlay.sleep.hide" is on
      When the gesture layer goes to sleep
      Then the chip is still drawn, and says which mechanism is asleep

    Scenario: The chip comes back to be confirmed
      # Waking takes two asks inside the window, and the line saying so is drawn on the chip.
      # Hiding it would make waking the one thing in the session done blind.
      Given "overlay.sleep.hide" is on and every mechanism is asleep
      When something asks to wake
      Then the chip is drawn again, with the confirmation it is waiting for
      And it goes dark again when the window closes with nothing confirmed

    Scenario: And a refusal is shown for the same reason
      Given "overlay.sleep.hide" is on and the pedals put everything to sleep
      When voice asks to wake
      Then the chip is drawn again, saying which mechanism may undo it

    Scenario: Waking gives back what was showing
      Given "overlay.sleep.hide" is on and the sheet was open at a group
      When everything sleeps and is woken again
      Then the sheet is drawn at that same group

    Scenario: Nothing is measured for a surface nobody can see
      Given "overlay.sleep.hide" is on and the gestures page is open
      When every mechanism goes to sleep
      Then no readings are published

  Rule: What is validated at load

    Scenario: A layer name that is not a mechanism is refused
      Given mode "root mode" maps "nap" to "sleep(mouth)"
      Then loading fails saying 'mouth' is not an input layer, and naming the three

    Scenario: A confirmation window of zero is refused
      # Read as "no confirmation", which is the thing the window exists to prevent, and
      # nobody sets it meaning to give that up
      Given "sleep_confirm_seconds" is 0
      Then loading fails mentioning 'sleep_confirm_seconds'

  Rule: Sleep does not survive a restart

    Scenario: A session starts awake
      Given every mechanism is asleep
      When the session is restarted
      Then nothing is asleep
