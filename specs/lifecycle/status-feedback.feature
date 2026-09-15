Feature: Knowing what the app is doing

  Two audiences need to know the app's state, and they need different things.

  A user driving the app hands-free needs to know, at a glance and without a terminal, which
  mode is active, whether a modifier is still down, and whether anything is broken. Getting
  this wrong is not cosmetic: speaking commands into a mode that is not listening, or dictating
  into a command mode, wastes real effort and is confusing to diagnose -- and a modifier nobody
  can see makes every command after it do something else.

  A config author debugging a command needs a detailed trace of how an utterance was
  interpreted — what was heard, how it normalized, which pattern matched, what the response
  expanded to, which tokens were emitted.

  Rule: The tray shows the active mode

    Scenario: Each mode gets its configured colour
      Given mode "root mode" has icon [0, 255, 0]
      And mode "dictate" has icon [255, 0, 0]
      When the active mode is "root mode"
      Then the tray icon shows the colour [0, 255, 0]
      When I say "dictate"
      Then the tray icon shows the colour [255, 0, 0]

    Scenario: A mode with no icon leaves the indicator unchanged
      Given mode "quiet" has no icon
      And the active mode is "root mode"
      When I say "quiet"
      Then the tray icon still shows the "root mode" colour

    Scenario: An invalid icon is rejected at load time
      Given mode "root mode" has icon [0, 999]
      When the configuration is loaded
      Then loading fails with an error naming mode "root mode"
      And the error explains that an icon is three values from 0 to 255

    Scenario: The tray reflects the mode after a reload
      When config.json is edited to set the icon of mode "root mode" to [0, 0, 255]
      And I say "reload config"
      Then the tray icon shows the colour [0, 0, 255]

  @accessibility-critical
  Rule: The tray shows faults

    Scenario: A fault colour is distinguishable from every mode colour
      Given any configuration
      Then the fault colour differs from every configured mode colour

    Scenario: A fault overrides the mode colour
      Given the active mode is "root mode"
      When head tracking fails
      Then the tray icon shows the fault colour

    Scenario: The mode remains discoverable while faulted
      Given a subsystem has failed
      Then the tray tooltip names the active mode
      And the tray tooltip names the failed subsystem

  @accessibility-critical
  Rule: The tray shows what is still held down

    A kernel keyboard has nothing on screen. Nothing says that "toggle shift" left shift down,
    so the next command silently does something else -- and the panic phrase only rescues
    someone who already knows they need it.

    Scenario: A toggled modifier appears on the icon
      Given the active mode is "root mode"
      And no key is held
      When I say "toggle shift"
      Then the tray icon shows that shift is held
      And the tray tooltip says "shift held"

    Scenario: A modifier waiting for its chord is shown as well
      # A bare "ctrl" stays down until the next keystroke completes the chord, which may be a
      # whole utterance away -- so it is shown, but not as the same thing as a held key.
      Given the command "hold control" mapped to "ctrl"
      When I say "hold control"
      Then the tray icon shows that ctrl is pending
      And the tray tooltip says "ctrl pending"

    Scenario: A completed chord shows nothing
      Given the command "copy that" mapped to "ctrl + c"
      When I say "copy that"
      Then the tray icon shows nothing held

    Scenario: Releasing a modifier clears it from the icon
      Given the key "shift" is held
      When I say "toggle shift"
      Then the tray icon shows nothing held

    @design-decision
    Scenario: The panic phrase clears the icon as well as the keys
      # Seeing the modifiers go is how a user knows the escape hatch worked.
      Given the key "shift" is held
      And the key "ctrl" is held
      When I say "release everything"
      Then the tray icon shows nothing held

    Scenario: One stuck modifier is drawn once
      # Tapping a modifier twice records it twice; drawn twice it reads as a different
      # problem than the one there is.
      When I say "hold control"
      And I say "hold control"
      Then the tray icon shows one ctrl glyph

    Scenario: Held keys are drawn over a fault
      # A stuck shift matters in every mode, faulted or not.
      Given a subsystem has failed
      And the key "shift" is held
      Then the tray icon shows the fault colour
      And the tray icon shows that shift is held

    Scenario: A mode with no icon has nothing to draw on
      # The documented consequence of "a mode with no icon leaves the indicator unchanged".
      Given the active mode is "quiet" which has no icon
      When I say "toggle shift"
      Then the tray icon is left unchanged
      And the tray tooltip says "shift held"

    @concurrency
    Scenario: A display that has wedged does not delay the panic phrase
      Given the display is not responding
      And the key "shift" is held
      When I say "release everything"
      Then the key sequence "shift↑" is emitted

  Rule: Logging is levelled and configurable

    Scenario Outline: What is logged at which level
      # closes M3 — everything is an unconditional print() today, including every VOSK partial
      Given the log level is "<level>"
      Then <event> is <visibility>

      Examples:
        | level | event                              | visibility |
        | info  | the active mode at startup         | shown      |
        | info  | a failed subsystem                 | shown      |
        | info  | an executed command                | shown      |
        | info  | an unmatched utterance             | hidden     |
        | info  | a VOSK partial result              | hidden     |
        | debug | an unmatched utterance             | shown      |
        | debug | a VOSK partial result              | shown      |
        | debug | a response's expansion steps       | shown      |

    Scenario: The log level is settable from the command line
      # closes M3
      When the app is started with "--log-level debug"
      Then debug messages are shown

    Scenario: A repeated warning is not silently suppressed
      # closes M3 — warnings.warn deduplicates by default, so a config author repeating a bad
      # command sees the warning once and then silence, which reads as the command working
      Given a command that fails validation at execution
      When I say the command 3 times
      Then the warning is logged 3 times

  @tinkerer
  Rule: An utterance's interpretation can be traced

    Scenario: Debug logging shows how an utterance was handled
      Given the log level is "debug"
      And the command "number {numeric}" mapped to "save({1})"
      And the command "page down" mapped to "{1=1}(pagedown)"
      When I say "number five"
      And I say "page down"
      Then the debug log shows the recognized text "number five"
      And the debug log shows the normalized text "number 5"
      And the debug log shows the matched pattern "number {numeric}"
      And the debug log shows the expanded response "save(5)"
      And the debug log shows the saved variable "5"
      And the debug log shows the expanded response "5(pagedown)"
      And the debug log shows the emitted tokens "pagedown" repeated 5 times

    Scenario: A near miss is explainable
      # "why did nothing happen" is the most common config authoring question
      Given the log level is "debug"
      And the command "press {any}" mapped to "{1}"
      When I say "press two words"
      Then the debug log states that no pattern matched
      And the debug log names the closest pattern "press {any}"
