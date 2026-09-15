@design-decision
Feature: Showing what the session is doing, in a process of its own

  The model knows what a display can show (see command-groups.feature). This file covers how that
  material reaches a display and how it is presented.

  Two surfaces. The **chip** is ambient and on by default: which mode is listening, what
  is still held down, whether anything is being heard, the last thing that was said and
  what became of it, and the reason when something is broken. The **sheet** is summoned
  and off by default: the group index, or one group, with the phrase that opens each thing
  drawn next to it, so that nothing about navigating it has to be remembered.

  Both are drawn by a separate process, reading newline-delimited JSON off a unix socket. The
  separation is required rather than cosmetic: the core needs /dev/uinput and a Wayland client
  needs the session's WAYLAND_DISPLAY, and one process can hold only one of those. It also means
  a GTK crash cannot reach a recognizer thread, and that the protocol is a seam behind which
  another toolkit can be placed.

  Every scenario here has a test: tests/overlay/test_protocol.py for the frames,
  tests/overlay/test_service.py for the socket, tests/overlay/test_hud_render.py for what
  the surfaces say, tests/overlay/test_hud_style.py for how they look,
  tests/overlay/test_hud_compositor.py for where the compositor lets them sit,
  tests/overlay/test_hud_motion.py for how they share the space, and
  tests/lifecycle/test_display.py for the wiring between them.

  Rule: the core publishes the whole picture, and reads nothing back

    Scenario: A display that connects is handed everything at once
      # Whole pictures rather than deltas, so a display that missed one, or started late,
      # is correct after the next pair rather than after a replay.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en", "icon": [0, 255, 0],
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When a display connects
      Then it is sent the configuration and then the state
      And the group "buffer" arrives with the phrase "open buffer"
      And the state says the active mode is "root mode"

    @accessibility-critical
    Scenario: A display cannot ask the core to type
      # The core holds /dev/uinput. Enforced in the kernel rather than in a docstring: the
      # accepted socket is shut down for reading, so a display that tries to write fails.
      Given the app is running
      And a display connects
      When the display sends anything at all
      Then the write fails
      And the core is still publishing

    Scenario: Nobody else on the machine can read what was said
      # A state frame carries the last utterance, which makes this stream as sensitive as
      # a keylogger's output.
      Given the app is running
      Then the socket is readable only by its owner

    @accessibility-critical @concurrency
    Scenario: A display that has stopped reading cannot delay a command
      # One of these callers is the mode manager with the modes lock held, and another is
      # a recognizer thread one statement after the executor lock was released. No method
      # that publishes therefore touches a socket: a client thread does the writing, and a
      # client that cannot be written to is dropped.
      Given the app is running
      And a display connects
      And the display stops reading its socket
      When a modifier is held and released twenty times
      Then every one of those returns immediately
      And the display is dropped rather than written to for ever

    Scenario: A display that is behind is caught up, not replayed
      Given the app is running
      And a display connects
      And the display stops reading its socket
      When the key "shift", then "ctrl", then "alt" is held
      Then the display is waiting on the latest picture only
      And that picture says "alt" is held

    Scenario: A second core is refused rather than stealing the socket
      # Two of them would both be holding /dev/uinput. A socket left behind by a crash
      # refuses connections and is replaced.
      Given the app is running
      When a second app tries to publish on the same socket
      Then it reports that another instance is already publishing
      And the first app keeps publishing

  Rule: the chip says what the session is doing

    Scenario: A held modifier appears on the chip
      Given the app is running
      When the key "shift" is held
      Then the chip shows that shift is held

    Scenario: A matched utterance appears with the pattern it matched
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s" }
            }
          }
        }
        """
      And the app is running
      When I say "buffer save"
      Then the chip's last-utterance line reads "\"buffer save\" ✓"

    Scenario: A placeholder command says which pattern caught it
      # Which command a placeholder matched is the part that is not obvious. When the
      # pattern is what was said, naming it twice would be noise.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "{numeric} down": "down" }
            }
          }
        }
        """
      And the app is running
      When I say "three down"
      Then the chip's last-utterance line reads "\"three down\" → {numeric} down ✓"

    Scenario: A near miss rides on the utterance line
      # The answer to "why did nothing happen" belongs next to the thing that did not
      # happen, and a line already on screen costs no height.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "zoom in": "ctrl + =" }
            }
          }
        }
        """
      And the app is running
      When I say "buffer safe"
      Then the chip's last-utterance line reads "\"buffer safe\" → buffer save?"

    Scenario: Filler the recognizer invented is not drawn as something that was said
      # A banned string is a hallucination nobody uttered. Reporting it as an utterance is
      # worse than silence, and it would make the chip flicker with unspoken words. It stays
      # in the DEBUG log, where the configuration author can find it.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "banned_strings": ["the"],
              "commands": { "buffer save": "ctrl + s" }
            }
          }
        }
        """
      And the app is running
      And I say "buffer save"
      When I say "the"
      Then the chip's last-utterance line still reads "\"buffer save\" ✓"

    Scenario: A partial is what proves the microphone is alive
      # A growing partial is something no dead capture thread can produce, which is why
      # there is no separate level meter.
      Given the app is running
      When the recognizer emits the partial "buffer sa"
      Then the chip shows it is hearing "buffer sa"
      And the last-utterance line is unchanged

    @transformer
    Scenario: A dictation mode can only say that it is hearing something
      # There is no partial result until the whole chunk has been transcribed, so the empty
      # partial is all this mode has, and it reports the one thing that matters.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "transformer", "model_name": "tiny",
              "commands": { "{rest}": "type({1})" }
            }
          }
        }
        """
      And the app is running
      When a voice is detected but nothing has been transcribed yet
      Then the chip shows it is hearing, with no words
      And the chip says "no voice navigation in this mode"

    Scenario: A fault says why, and not only that there is one
      # The tray has twenty pixels and can only say that something is wrong. The chip has
      # a line, so it gets the sentence.
      Given the app is running
      When the recognizer for "root mode" dies with "model file vanished"
      Then the chip shows "mode 'root mode' (model file vanished)"

  Rule: the chip says where the other two mechanisms are, but only when it matters

    Three names on screen that usually agree would be three names nobody reads. The row acts as
    the notification, and is present only when the three do not agree.

    Scenario: While all three layers agree there is no second line
      Then the chip shows "command mode"
      And the chip shows no layer line

    Scenario: A layer somewhere else is named with its mode
      Given the pedal layer is in "scrolling"
      Then the chip's layer line reads "pedal: scrolling"

    Scenario: A layer whose subsystem is off is never named
      # A name for a mechanism that cannot fire is one more thing to learn that means
      # nothing.
      Given "enable_pedals" is absent
      And the gesture layer is in "mouse mode"
      Then the chip's layer line reads "gesture: mouse mode"

  Rule: the chip says which shape is holding something down

    A held mouse button is the least visible state this display reports. `hold(left_mouse)` from
    a fist and a fist that did nothing are indistinguishable from the outside: something is
    dragging, and the only useful statement is which hand to relax. The glyph row can report that
    a button is down, but not what is holding it.

    Held in the sense a pedal is held: a shape stays active until it relaxes past its release
    value, so a pinch whose click has already occurred remains on the line for as long as the
    pinch does.

    @accessibility-critical
    Scenario: A gesture holding a button names itself and what it holds
      Given the gesture "left_fist" is bound to press "hold(left_mouse)"
      When the shape goes active
      Then the chip's gesture line reads "gesture: left_fist → left_mouse"
      And the glyph row shows the left mouse button as its own symbol

    Scenario: Letting go clears the line
      Given the gesture "left_fist" is active
      When the shape relaxes past its release value
      Then the chip shows no gesture line

    Scenario: With nothing held there is no line at all
      # A row that is always present carries no information by being present
      Then the chip shows no gesture line

    Scenario: The response is read off the gesture layer's mode
      # The wire carries the names alone: the bindings are in the catalogue already, and
      # sending them ten times a second would be sending the configuration every frame
      Given the gesture layer is in "::hands"
      And "::hands" binds "right_pinch" to "right_mouse"
      When "right_pinch" goes active
      Then the chip's gesture line reads "gesture: right_pinch → right_mouse"

    Scenario: A held shape with nothing bound is still shown
      # It is being held, and a shape held with no binding behind it is worth showing rather
      # than hiding, because an absent binding is the answer to the question
      Given the gesture "blink" is bound in no mode
      When "blink" goes active
      Then the chip's gesture line reads "gesture: blink"

  Rule: the sheet draws what the model found, with the phrases beside it

    Scenario: The index lists every group with its size and its phrase
      Given the configuration:
        """
        {
          "modes": {
            "::alphabet": {
              "commands": { "type a": "a", "type b": "b", "press e": "e", "press f": "f",
                            "key i": "i", "key j": "j" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::alphabet"],
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When the sheet is opened at the index
      Then the sheet lists "alphabet" with 6 commands and the phrase "open alphabet"
      And the sheet's hint reads "say \"open\" + a name"

    Scenario: An aligned table draws its gaps
      # The empty cells are significant: they state that "link delta" is a command this
      # configuration does not have, which no list of the commands it has can state.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": {
                "vector alpha": "f13", "vector bravo": "f14", "vector charlie": "f15",
                "dispatch alpha": "meta + f13", "dispatch bravo": "meta + f14",
                "dispatch charlie": "meta + f15",
                "link alpha": "ctrl + f13", "link bravo": "ctrl + f14"
              }
            }
          }
        }
        """
      And the app is running
      When the sheet is opened at the group "vector"
      Then the columns read "alpha, bravo, charlie"
      And the cell "link" / "charlie" is drawn as missing, never blank

    Scenario: A ragged table's cells are the words to say
      # "press hotel" types h. What the reader needs is "hotel", with "press _" beside the
      # row, because twenty-six lines that all begin with the same verb answer nothing.
      Given the configuration:
        """
        {
          "modes": {
            "::alphabet": {
              "commands": { "type a": "a", "type b": "b",
                            "press e": "e", "press hotel": "h",
                            "key i": "i", "key j": "j" }
            },
            "root mode": {
              "type": "vosk", "path": "models/small-en", "imports": ["::alphabet"]
            }
          }
        }
        """
      And the app is running
      When the sheet is opened at the group "::alphabet"
      Then the row "press" reads "e, hotel"
      And the row "press" is labelled with the phrase "\"press _\""

    Scenario: A group drawn by voice says how to get off it
      # A page reached by voice showed no way back to the index, so the discoverability
      # tool needed one of its own. Both phrases are renameable, so they travel in the
      # catalogue rather than being spelled in the display.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When the sheet is opened at the group "buffer"
      Then the footer says "overlay open" goes back to all groups
      And it says "overlay close" hides the sheet
      And a renamed pair is what gets drawn, never the default
      And the index does not offer a way back to itself

    Scenario: A group the display has never heard of falls back to the index
      # One frame around a reload, and also what a group renamed out from under an open
      # sheet looks like. Drawing nothing would look like a crash.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s", "buffer close": "ctrl + w" }
            }
          }
        }
        """
      And the app is running
      When the sheet is opened at the group "renamed"
      Then the sheet shows the index

  Rule: the sheet says where the session is, and what reaches everywhere else

    The sheet has to show where the session is and which modes are reachable from there,
    deduced from the configuration rather than written down. It is deduced already: the
    model works out every way out of a mode and tags it with the input method that takes
    it (command-groups.feature), so the modes page is that graph drawn from the row the
    session is standing on.

    One column per input method, which is the axis that grows. When gestures, head
    tracking and pedals each carry a state of their own, they are columns here rather
    than a redrawing of this.

    Scenario: The modes page draws the graph from where the session is standing
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s" },
              "gestures": { "smile": "mode(other mode)" }
            },
            "other mode": { "type": "vosk", "path": "models/small-en" },
            "far mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      And the app is running
      When I say "overlay modes"
      Then the row for "root mode" is marked as where I am
      And that row draws no transition to itself
      And "other mode" reads "other mode" under voice and smile under gesture
      And "far mode" reads as unreachable under gesture, never blank

    Scenario: An input method the configuration never uses gets no column
      # A column of dashes carries no information, and a voice-only configuration should not
      # have to read one.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s" }
            },
            "other mode": { "type": "vosk", "path": "models/small-en" }
          }
        }
        """
      And the app is running
      When I say "overlay modes"
      Then the page has a voice column and no gesture column

    Scenario: A transition nobody can say is drawn as what it means
      # A dictation mode that types one utterance and switches back has a real edge with
      # no phrase behind it. Drawing "{rest}" would be an instruction nobody can follow.
      Given the configuration:
        """
        {
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "commands": { "buffer save": "ctrl + s" }
            },
            "dictate mode": {
              "type": "transformer", "model_name": "tiny",
              "commands": { "{rest}": "type({1}) + mode(previous mode)" }
            }
          }
        }
        """
      And the app is running
      And the mode is "dictate mode"
      When I say "overlay modes"
      Then "previous mode" is a row of its own
      And it reads "anything you say", not "{rest}"

    Scenario: A page and a group can never disagree about what is showing
      # A group name is what distinguishes the index from a group, and the page is asked about
      # the one case it cannot distinguish. A frame claiming both would be inconsistent.
      Given the app is running
      And the sheet is opened at the group "buffer"
      When I say "overlay modes"
      Then the state says the page is the modes page
      And it names no open group

    Scenario: The phrase that opens it is one the config names
      # Like every other phrase the display answers to. It lives in the "overlay" family
      # rather than under the navigate prefix, so that "open <name>" means a group of
      # commands and nothing else.
      Given the app is running
      Then a command called "overlay modes" is refused at load, naming the setting
      And the other two pages draw the phrase that opens this one

  @pedals
  Rule: the sheet says what each pedal does right now

    Three pedals with two edges apiece is a keyboard nobody can look at, and unlike a
    command a pedal has no phrase to remember it by. The page is a row per pedal the
    *board* has, so an unbound pedal is a visible gap: which pedals are still free is
    exactly what a list of the bound ones cannot state.

    Scenario: A row per pedal, and a column per edge
      Given the configuration:
        """
        {
          "enable_pedals": true,
          "pedal": { "buttons": { "left": 4, "middle": 5, "right": 6 } },
          "modes": {
            "root mode": {
              "type": "vosk", "path": "models/small-en",
              "pedals": {
                "left": { "press": "hold(left_mouse)", "release": "release(left_mouse)" },
                "middle": "enter"
              }
            }
          }
        }
        """
      And the app is running
      When I say "overlay pedals"
      Then the rows read "left, middle, right"
      And the columns read "press, release"
      And the row "left" reads "hold(left_mouse), release(left_mouse)"

    Scenario: A pedal bound on one edge only draws the other as missing
      # A click and a drag look identical until both edges are visible.
      When I say "overlay pedals"
      Then the cell "middle" / "release" is drawn as missing, never blank

    Scenario: A pedal nothing binds is still a row
      When I say "overlay pedals"
      Then the cell "right" / "press" is drawn as missing, never blank

    Scenario: It shows the layout the pedal layer is on
      Given the pedal layer is in "scrolling"
      When I say "overlay pedals"
      Then the sheet's title names "scrolling"

    Scenario: The footer says what moves the pedals
      # Gathered from all three mechanisms, because the route back to a pedal layer is as
      # likely to be a phrase or a gesture as another pedal. That is what independent mechanisms
      # means, and it is the part that cannot be held in memory.
      Given the command "feet scroll" is mapped to "mode(pedal:scrolling)"
      When I say "overlay pedals"
      Then the footer offers "feet scroll" as the way to "scrolling"

    Scenario: With no board the page says the setting that turns it on
      # An empty page reads as a bug, and this one is a switch that is off.
      Given "enable_pedals" is absent
      When I say "overlay pedals"
      Then the sheet's hint reads "no pedal device configured (enable_pedals)"

    Scenario: With no board no other page offers it
      # A footer would otherwise promise a phrase whose whole answer is "switched off".
      Given "enable_pedals" is absent
      When I say "overlay open"
      Then the footer does not offer the pedals page

    Scenario: Opening it forgets the group, the way the modes page does
      Given the sheet is open at the group "buffer"
      When I say "overlay pedals"
      Then the sheet is open at the pedals page
      And no group is open

  Rule: The gestures page is the only place a gesture is visible

    A gesture reaches a display by two routes. The modes page draws the gestures that *switch
    modes*, because a route out of a mode cannot be recovered by looking at the screen. A gesture
    bound to a key or a mouse button is not a route out of anywhere, so the modes page gives it no
    row, and a fist holding a button is precisely the state that has to be reported back.

    Its layout deliberately matches the pedals page, because the two non-voice mechanisms answer
    the same question and therefore answer it the same way.

    Scenario: It has a row per gesture the configuration defines
      # Per definition rather than per binding, because which shapes are still free is exactly
      # what a list of the bound ones cannot state
      When I say "overlay gestures"
      Then the page has a row for every gesture in "ht_custom_gestures"

    Scenario: Both edges get a column
      When I say "overlay gestures"
      Then the columns are "press" and "release"

    Scenario: A gesture bound on both edges shows both
      Given the gesture "fist" is bound to press "hold(left_mouse)" and release "release(left_mouse)"
      When I say "overlay gestures"
      Then the cell "fist" / "press" reads "hold(left_mouse)"
      And the cell "fist" / "release" reads "release(left_mouse)"

    Scenario: A bare response leaves the release a drawn gap
      # A tap and a drag look identical until both edges are visible
      Given the gesture "pinch" is bound to "left_mouse"
      When I say "overlay gestures"
      Then the cell "pinch" / "release" is drawn as missing, never blank

    Scenario: A gesture this mode binds nothing to is still a row
      Given the gesture "blink" is defined and bound in no mode
      When I say "overlay gestures"
      Then the cell "blink" / "press" is drawn as missing, never blank

    Scenario: It shows the set the gesture layer is on
      # Not the voice layer's, because the two need not agree, and drawing the recognizer's
      # gestures while a different set is live is the defect this mirrors from the pedals page
      Given the gesture layer is in "::quiet"
      When I say "overlay gestures"
      Then the sheet's title names "quiet"
      And the rows show what "::quiet" binds

    Scenario: The footer says what moves the gesture layer
      Given the command "hands off" is mapped to "mode(gesture:::quiet)"
      When I say "overlay gestures"
      Then the footer offers "hands off" as the way to "quiet"

    Scenario: With nothing defined the page says the setting
      # An empty page reads as a bug, and this one is a table nobody has filled in.
      Given "ht_custom_gestures" is absent and head tracking is off
      When I say "overlay gestures"
      Then the sheet's hint reads "no gestures defined (ht_custom_gestures)"

    Scenario: With nothing defined no other page offers it
      Given "enable_head_tracking" is absent
      When I say "overlay open"
      Then the footer does not offer the gestures page

    Scenario: The two edge pages offer each other
      # Pedals and gestures are the two mechanisms other than voice, so from either page
      # the other is one phrase away
      When I say "overlay gestures"
      Then the footer offers the pedals page
      When I say "overlay pedals"
      Then the footer offers the gestures page

    Scenario: Opening it forgets the group, the way the other pages do
      Given the sheet is open at the group "buffer"
      When I say "overlay gestures"
      Then the sheet is open at the gestures page
      And no group is open

  Rule: The gestures page says why a gesture is not firing

    A gesture that never fires and a failed camera are indistinguishable from the outside, since
    both are silent. The difference is one value against one threshold, and until that is on
    screen the only way to find it is a terminal, which is unusable while a shape is being held
    up to the webcam with both hands.

    So the page grows a third column while the readings are on: how close each gesture is,
    and the one condition keeping it shut.

    @accessibility-critical
    Scenario: The blocking condition is named with its value and its threshold
      Given the gesture "right_fist" needs "rightHandFist" above 0.8
      And "rightHandFist" is reading 0.62
      When I say "overlay gestures"
      Then the cell "right_fist" / "now" reads "rightHandFist 0.62 >0.80"
      And a bar beside it shows how close that is

    Scenario: Only the least satisfied condition is named
      # With two conditions unmet, correcting either one changes nothing, so naming both would
      # present two actions where only one is useful. The column is recomputed every frame, so
      # the next condition appears as soon as one is corrected.
      Given "right_fist" needs "rightHandFist" above 0.8 and "rightHandThumbCurl" above 0.4
      And "rightHandFist" is reading 0.62 and "rightHandThumbCurl" 0.10
      When I say "overlay gestures"
      Then the cell names "rightHandThumbCurl" alone

    @accessibility-critical
    Scenario: A feature no frame reported reads as absent, not as zero
      # On the hand path this is the difference between an open hand and no hand at all,
      # and "no hand in shot" is the most common reason a hand gesture is quiet
      Given no hand is in shot
      When I say "overlay gestures"
      Then the cell "right_fist" / "now" reads "rightHandFist -- >0.80"
      And the cell is drawn dim rather than as a blocked one

    Scenario: A held gesture says so instead of a number
      # The remaining question for a held gesture is not how close it is
      Given the gesture "left_pinch" is active
      When I say "overlay gestures"
      Then the cell "left_pinch" / "now" reads "held"

    Scenario: A gesture waiting out its dwell is not drawn as blocked
      # The one state that appears inactive while in fact working
      Given every condition of "thumbs_up" holds
      And its "hold" of 250 ms has not elapsed
      When I say "overlay gestures"
      Then the cell "thumbs_up" / "now" says it is holding for 0.25s

    @tinkerer
    Scenario: The raw ratios are drawn under the table
      # A score has already been normalised onto 0-1 by the very constants being calibrated,
      # so reading one back states nothing about where its ends belong
      Given a hand is in shot
      When I say "overlay gestures"
      Then the extension, pinch, spread and palm-area ratios are shown under the table
      And the block names "ht_hand_calibration", which is what they are measured for

    Scenario: With hands on and none in shot the block says so
      # An empty block reads as a broken model rather than as an empty frame
      Given no hand is in shot
      When I say "overlay gestures"
      Then the block reads "no hand in shot"

    Scenario: Switching them off takes the column with it
      When I say "overlay meters"
      Then the page has no "now" column
      And the ratios are gone with it

  Rule: The readings cost nothing when nobody is looking at them

    Ten frames a second of live values costs one socket write and one repaint each. All of that
    is wasted unless the page that draws them is on screen, and the core is the only side that
    knows whether it is.

    Scenario: They are published only while that page is open
      Given the sheet is open at its index
      When a gesture score changes
      Then no state frame is published for it

    Scenario: Closing the page clears them rather than leaving them standing
      # A stale meter presents a value that appears live and is not, which is worse than none
      Given the sheet is open at the gestures page
      When I say "overlay close"
      Then the next state frame carries no readings

    Scenario: They are thinned to ten a second
      # As fast as a settling value can be read, whereas the camera thread runs at sixty
      Given the gestures page is open
      When the camera delivers sixty frames in a second
      Then at most ten frames of readings are published

    Scenario: The switch survives a reload, and a reload does not undo it
      # Editing the setting and saying the reload phrase MUST take effect, whereas a reload
      # that did not touch the setting MUST NOT undo a toggle just applied
      Given "overlay.meters.enabled" is true and I have said "overlay meters"
      When I say the reload phrase
      Then the readings are still off

  Rule: the sheet makes space for the chip rather than covering it

    Both surfaces dock to the same edge by default, and between two surfaces on the overlay
    layer the compositor chooses the order, so an open sheet drew over the chip and the surface
    that disappeared was the one reporting that the microphone is alive. The chip therefore moves
    inward by the sheet's width and returns to its corner afterwards.

    A layer surface has no position, only an anchor and a margin, so all of this is the client
    setting that margin once a frame. The arithmetic is in tests/overlay/test_hud_motion.py. The
    frames themselves need a compositor, which the environment the suite runs in does not provide
    (gap 7).

    Scenario: An open sheet moves the chip aside instead of over it
      Given the chip sits in the top-right corner and the sheet docks to the right
      When the sheet is opened
      Then the chip moves in by the sheet's own width, plus the gap between them
      And what is measured is the sheet on screen, not the width it was asked for

    Scenario: The chip eases across rather than jumping
      When the sheet is opened
      Then the chip leaves and arrives at rest, over a fraction of a second
      And hiding the sheet is that same movement backwards

    Scenario: A sheet hidden mid-movement sends the chip back from where it is
      # Any other path is a jump, and a chip that jumps has to be located again.
      Given the sheet is opening and the chip is halfway clear of it
      When the sheet is hidden
      Then the chip returns from halfway, not from the corner it was heading for

    Scenario: A chip on the far side of the screen never moves
      # Nothing obstructs it, and a chip that moved for no visible reason would be worse than
      # one that stayed in place.
      Given the chip sits in the top-left corner and the sheet docks to the right
      When the sheet is opened
      Then neither surface moves

    Scenario: A desktop with animations turned off still gets the chip beside the sheet
      # The chip's position is not a preference. Only the transition is, and
      # gtk-enable-animations is how a desktop declines one, whether for an accessibility
      # reason or because the session is drawn over a network.
      Given a desktop with gtk-enable-animations off
      When the sheet is opened
      Then the chip is already clear of it on the next frame, with no movement drawn

  Rule: a display that cannot be put where it belongs says so, loudly

    Appearing above the focused window, and on every virtual desktop, is what makes this a
    display rather than a decoration. Both properties come from one Wayland protocol,
    zwlr_layer_shell_v1, and on some compositors from nothing else, because a Wayland client
    cannot request either over the wire. There is therefore no form of this that degrades
    quietly. Every statement below is asserted in tests/overlay/test_hud_compositor.py.

    @accessibility-critical
    Scenario: A missing layer shell is an error, and it names the package
      # This is an observed failure, and the first implementation answered it with one warning
      # in a log nobody was reading.
      Given a Wayland session on KDE
      And gtk4-layer-shell cannot be loaded
      When the display starts
      Then it reports at ERROR that the overlay is an ordinary window
      And it says that it stays on one virtual desktop, is covered, and swallows clicks
      And it names the package to install on each distribution
      And that name is everything the first attempt needs, binding included

    Scenario: A library without its Python binding is not called a missing protocol
      # The state a distribution leaves behind when its introspection build option is off by
      # default: the shared library is present and the typelib is not. Without the typelib
      # nothing can query the compositor, so reporting an absent layer shell would be a guess
      # presented as a finding, and the remedy it offers, LD_PRELOAD, addresses a different
      # problem entirely.
      Given a Wayland session on KDE
      And the shared library loaded and there is no Gtk4LayerShell typelib
      When the display starts
      Then it says the binding is what is missing, and not the protocol
      And it names the USE flag and the gir package that supply it

    Scenario: A layer shell loaded too late is diagnosed as the load order
      # gtk4-layer-shell defines libwayland-client's own symbols and forwards them, so it MUST
      # reach the process before libwayland-client does. Loaded after GTK, every call into it
      # succeeds and changes nothing, which is why the window is queried about whether it became
      # a layer surface rather than assumed to have become one.
      Given a Wayland session on KDE
      And gtk4-layer-shell loaded and the compositor offers the protocol
      When the window did not become a layer surface
      Then the display says the load order is the cause
      And it names the LD_PRELOAD that proves it

    Scenario: KWin is not blamed for a protocol it implements
      # Advising a change of compositor is advice that cannot be acted on, from a message that
      # cannot be verified.
      Given a Wayland session on KDE
      When the library reports no layer shell
      Then the display blames the load order and not the compositor

    Scenario: An X11 session is told the only thing that can be done about it
      Given a session with no Wayland display
      When the display starts
      Then it says that X11 has no layer shell
      And it does not send the user to install anything

    Scenario: On KDE, every failure offers the way back by hand
      # Two of the three properties are settable through compositor window rules.
      # Click-through is not, so the fallback is named as a stopgap and never as the remedy.
      Given a Wayland session on KDE
      When the display cannot reach the overlay layer
      Then it names the window rule to import
      And it names the app id that rule has to match

    Scenario: Getting the overlay layer is said once, and quietly
      Given a Wayland session on KDE
      When the surfaces reach the overlay layer
      Then the display reports the protocol version, every desktop, and click-through

  Rule: the surfaces are confined to one video output, when the configuration names one

    On two monitors the compositor's own choice is arbitrary, and a chip placed on the screen
    nobody is facing is not read. The output is named in the configuration rather than on the
    display's command line, because the restriction has to survive a reload and a cable being
    disconnected, and the core sends it with the catalogue. Pinning a surface to an output is a
    layer-shell property, so an ordinary window is placed by the compositor regardless of this
    setting. The selection is asserted in tests/overlay/test_hud_compositor.py.

    Scenario: The named output is the only one the surfaces appear on
      Given the configuration:
        """
        {
          "overlay": { "output": { "name": "DP-1", "only": true } },
          "modes": { "root mode": { "type": "vosk", "path": "models/small-en" } }
        }
        """
      When the display is told which output was asked for
      Then the chip, the sheet and the calibration surface are all pinned to "DP-1"

    Scenario: An output this session has not got draws nothing at all
      # Naming one output is a statement about where the user is looking, so the others are not
      # a fallback; they are the screens the surfaces were deliberately removed from.
      Given "DP-1" is asked for and this session has "DP-2" and "HDMI-A-1"
      Then no surface is drawn
      And the display names at WARNING the outputs it does have, and the flag that lists them

    Scenario: Unless the restriction is a preference rather than a rule
      Given "DP-1" is asked for with "only" off, and this session has "DP-2" alone
      Then the compositor places the surfaces, as though no output had been named

    Scenario: An output that arrives is taken without a restart
      # Connecting or disconnecting a dock is exactly when a display must not be the component
      # that disappears.
      Given "DP-1" is asked for and is not connected
      When "DP-1" is connected
      Then the surfaces move to it

    Scenario: An ordinary window is not blanked by a restriction it cannot obey
      # Decision 1, degraded never fatal: a configuration written for the Wayland session
      # must not leave the X11 one with no display at all.
      Given "DP-1" is asked for and this session has no layer shell
      Then the surfaces are drawn wherever the compositor puts them
      And the display says at WARNING, once, that the restriction cannot be applied

    Scenario: With no output named the compositor chooses, as it did before
      Given no output is named
      Then no surface is pinned to anything

  Rule: the surfaces are translucent, and the window itself paints nothing

    Scenario: The theme's own window background never shows through
      # A GTK theme paints window.background an opaque colour. Where the theme takes
      # precedence, the result is a solid rectangle with the display drawn inside it. The window
      # is therefore transparent at every setting, and the rule stating so is attached to an id
      # that the theme's class cannot outrank.
      When the stylesheet is built at any opacity
      Then the window is transparent
      And the panel behind the text is painted at exactly the opacity that was asked for

    Scenario: Turning the panel off leaves nothing behind
      # Not an invisible fill, because an empty rounded outline over the screen would be worse
      # than either end of the range.
      When the opacity is 0
      Then no rectangle is drawn at all
      And the text still carries the shadow that keeps it legible

    Scenario: An impossible opacity is clamped rather than refused
      # A display that refused to start over an out-of-range --opacity would be a display that
      # cannot be seen, which is the one failure this subsystem exists to prevent.
      When the opacity is 2.5
      Then the panel is painted solid and the display still starts

  Rule: a display is optional, and losing one costs nothing else

    Scenario: A socket that will not bind is a fault and nothing more
      Given the configuration directory cannot hold a socket
      When the app is started
      Then "display socket" is reported as failed
      And every mode is still listening

    Scenario: The reload phrase brings the socket back
      # The same hands-free way back that a camera unplugged and plugged in again gets.
      Given the app is running
      And the display socket has been closed
      When I say "reload config"
      Then the app is publishing again

    Scenario: A reload republishes the configuration before the state that names it
      # Otherwise a sheet would draw one configuration's groups under another's revision.
      Given the app is running
      And a display connects
      When I say "reload config"
      Then the display is sent a new configuration
      And the state that follows names that configuration

    Scenario: Shutting down hangs up rather than saying goodbye
      # Closing the socket is how a display detects that the core has stopped. Decision 13
      # states that shutdown types nothing, switches no mode and opens no device, and closing a
      # socket is none of those.
      Given the app is running
      And a display connects
      When the app shuts down
      Then the display sees the end of the stream
      And the socket is gone
