@head-tracking
Feature: Moving the pointer with head rotation

  A webcam tracks the user's head and moves the pointer. The raw signal is yaw and pitch in
  degrees, and five stages convert that into pointer motion:

    1. smoothing   adaptive low-pass on the pose, so tracker jitter does not become velocity
    2. neutral     subtract where the head is considered to be resting
    3. dead zone   an ellipse around neutral inside which nothing moves at all
    4. curve       the angle past that boundary, as a fraction of the way to full speed,
                   raised to a power, scaled to pixels per second
    5. interval    multiplied by the time since the last pose, to get this frame's step

  The curve works on the rotation **vector**. Speed comes from the total angle and the direction
  is carried through exactly, so the pointer travels where the head moved. Applying a curve to
  yaw and pitch separately bent the direction of travel by up to 7.6 degrees, and bent it worse
  the steeper the curve, so the pointer drifted away from the intended target precisely when
  fine control mattered most.

  Speed is expressed per second and never per frame. Per-frame gain meant the same head movement
  travelled half as far whenever the frame rate halved, which made pointer sensitivity depend on
  whatever else the machine was doing.

  Background:
    Given the configuration:
      """
      {
        "enable_head_tracking": true,
        "ht_model_path": "models/face_landmarker.task",
        "ht_dead_angle_h": 2.0,
        "ht_dead_angle_v": 2.0,
        "ht_speed_power": 1.0,
        "ht_full_speed_angle": 12.0,
        "ht_max_speed_px_per_sec": 1000,
        "ht_offset_x": 0.0,
        "ht_offset_y": 0.0,
        "modes": {
          "root mode": { "type": "vosk", "path": "models/small-en", "ht_enabled": true },
          "reading":   { "type": "vosk", "path": "models/small-en", "ht_enabled": false }
        }
      }
      """
    And the app is running
    And the active mode is "root mode"

  Rule: The dead zone holds the pointer perfectly still

    Without this the pointer drifts constantly from the small involuntary movements of holding
    a head still, which makes reading impossible.

    Scenario Outline: Movement within the dead zone produces nothing
      When my head yaws <degrees> degrees
      Then the pointer does not move

      Examples:
        | degrees |
        | 0       |
        | 0.5     |
        | 1.9     |
        | -1.9    |
        | 2.0     |

    Scenario: A pose inside the dead zone on both axes emits nothing at all
      When my head yaws 1 degree and pitches 1 degree
      Then no pointer event is emitted
      # Not a zero-motion event — nothing is written to the device

    Scenario: Speed rises from zero at the boundary
      When my head yaws 2.001 degrees
      Then the pointer moves horizontally slower than 1 pixel per second
      # No jump at the threshold: the pointer starts from a standstill

    Scenario Outline: The two dead angles are the radii of an ellipse, not a rectangle
      # A rectangle would suppress a diagonal that neither axis allows on its own, and the
      # boundary would jump as the head's direction turned rather than sweeping round it.
      Given mode "root mode" has effective setting "ht_dead_angle_v" of 9.0
      And mode "root mode" has effective setting "ht_dead_angle_h" of 3.0
      When my head yaws <yaw> degrees and pitches <pitch> degrees
      Then the pointer <outcome>

      Examples:
        | yaw | pitch | outcome     |
        | 2.9 | 0     | does not move |
        | 0   | 8.9   | does not move |
        | 2.9 | 8.9   | moves         |

  Rule: Speed is pixels per second

    Scenario Outline: The curve between the dead zone and full speed
      When my head yaws <degrees> degrees
      Then the pointer moves horizontally at <speed> pixels per second

      Examples:
        | degrees | speed |
        | 3       | 100   |
        | 5       | 300   |
        | 7       | 500   |
        | 12      | 1000  |

    Scenario: Full speed is reached at the configured angle and clamped past it
      When my head yaws 12 degrees
      Then the pointer moves horizontally at 1000 pixels per second
      When my head yaws 90 degrees
      Then the pointer moves horizontally at 1000 pixels per second

    Scenario Outline: The same angle is the same speed at any frame rate
      # This is the reason the unit is per second. The previous per-frame gain made a busy
      # machine produce a slow pointer, which presents as unreliable tracking.
      Given the camera is delivering <fps> frames per second
      When my head yaws 7 degrees
      Then the pointer moves horizontally at 500 pixels per second

      Examples:
        | fps |
        | 15  |
        | 30  |
        | 60  |
        | 120 |

    Scenario: The first pose after a gap moves nothing
      # A rate needs two samples. Acting on the first would require inventing an interval, and
      # after a stall the correct interval is long enough to move the pointer a large distance.
      Given no face has been detected yet
      When my head yaws 20 degrees
      Then no pointer event is emitted

    Scenario: Speeds below one pixel per frame are available
      # The device takes whole pixels, so a fraction discarded every frame rather than carried
      # to the next leaves 60 px/s as the slowest speed available: an eased four-degree head
      # sweep then travels zero pixels.
      Given mode "root mode" has effective setting "ht_full_speed_angle" of 40.0
      When my head yaws 3 degrees for 1 second
      Then the pointer moves horizontally by more than 0 pixels
      And the pointer moves horizontally by fewer than 60 pixels

  Rule: The curve shapes speed, never direction

    Scenario Outline: The power bends the middle of the range and leaves the ends alone
      Given mode "root mode" has effective setting "ht_speed_power" of <power>
      When my head yaws 7 degrees
      Then the pointer moves horizontally at <speed> pixels per second

      Examples:
        | power | speed |
        | 1.0   | 500   |
        | 2.0   | 250   |

    Scenario Outline: The pointer travels in exactly the direction the head moved
      When my head yaws <yaw> degrees and pitches <pitch> degrees
      Then the pointer moves at <angle> degrees from horizontal

      Examples:
        | yaw | pitch | angle |
        | 10  | 10    | 45.0  |
        | 10  | 5     | 26.6  |
        | 12  | 4     | 18.4  |

    Scenario: Direction survives a steep curve
      # The per-axis curve failed exactly here: at power 1.5 a 19.7 degree movement came out
      # at 12.1, and worse the steeper the curve went.
      Given mode "root mode" has effective setting "ht_speed_power" of 3.0
      When my head yaws 10 degrees and pitches 5 degrees
      Then the pointer moves at 26.6 degrees from horizontal

    Scenario: The speed limit is round, so a diagonal is no faster than an axis
      # Clamping each axis separately let the vector reach 1.41 times the limit, and a fast
      # diagonal turn faster than a fast horizontal one presents as the pointer veering.
      When my head yaws 90 degrees
      Then the pointer moves at 1000 pixels per second
      When my head yaws 90 degrees and pitches 90 degrees
      Then the pointer moves at 1000 pixels per second

  Rule: Jitter is smoothed without lagging a deliberate turn

    Mediapipe's per-frame estimate is noisy. The dead zone hides that near neutral and nothing
    hid it further out, so the pointer shivered while travelling. A plain low-pass would trade
    the shiver for lag one-for-one; this one widens its own cutoff with the speed of the pose.

    Scenario: A still head does not shiver
      When my head holds still 6 degrees off neutral with 0.4 degrees of tracker noise
      Then the pointer speed varies by less than 5 percent

    Scenario: A deliberate turn is not held back
      When my head turns 20 degrees over half a second
      Then the smoothed pose reaches at least 18 degrees

    Scenario: Smoothing is discarded across a gap
      # Smoothing across a lost face would drag the pointer toward a pose the head has long
      # since left.
      Given no face was detected for 2 seconds
      When my head yaws 5 degrees
      Then no pointer event is emitted
      And the next pose is taken as it is

    Scenario: Both axes are smoothed by the same amount
      # Smoothing one axis harder than the other would bend the direction that the radial
      # curve exists to preserve.
      When my head yaws 10 degrees and pitches 10 degrees
      Then the pointer moves at 45.0 degrees from horizontal

  Rule: Neutral is where the head rests, and the user can move it

    A fixed constant cannot stay right: posture drifts over minutes, and once neutral is off by
    two degrees, "centred" quietly stops meaning centred and the head has to be held against it.

    Scenario: The offset sets the starting neutral
      Given mode "root mode" has effective setting "ht_offset_x" of 10.0
      When my head yaws 10 degrees
      Then the pointer does not move

    Scenario: Movement is measured from neutral
      Given mode "root mode" has effective setting "ht_offset_x" of 10.0
      When my head yaws 15 degrees
      Then the pointer moves horizontally at 300 pixels per second

    Scenario: recenter() adopts the current pose
      When my head yaws 8 degrees
      And I say "recenter"
      Then the pointer does not move
      When my head yaws 11 degrees
      Then the pointer moves horizontally at 100 pixels per second

    Scenario: recenter() with head tracking stopped says so and changes nothing
      # Rule 2: a subsystem that is not there does not end the session.
      Given the camera has failed
      When I say "recenter"
      Then nothing is typed
      And the session is still running

    Scenario: recenter() before any face has been seen changes nothing
      Given no face has been detected yet
      When I say "recenter"
      Then the neutral position is unchanged
      # There is no pose to adopt, and guessing one would move neutral where the head has
      # never been

    Scenario: Auto-recentring is off unless asked for
      Given the configuration does not set "ht_auto_recenter_seconds"
      When my head holds still 1 degree off neutral for 30 seconds
      Then the neutral position is unchanged

    Scenario: Auto-recentring absorbs drift inside the dead zone
      Given "ht_auto_recenter_seconds" is 5.0
      When my head holds still 1.5 degrees off neutral for 5 seconds
      Then the neutral position has moved about 63 percent of the way there

    Scenario: Auto-recentring never creeps toward a held turn
      # Gated on the pose being inside the dead zone, so it can only absorb unintended drift.
      # Otherwise a sustained turn would gradually stop working.
      Given "ht_auto_recenter_seconds" is 5.0
      When my head holds 8 degrees off neutral for 5 seconds
      Then the neutral position is unchanged

  Rule: Direction is decided in exactly one place

    Scenario Outline: Inversion flips the axis
      # closes a maintainability trap: handle_mouse_relative negates X unconditionally while
      # the caller separately negates for ht_invert_x, so the effective direction is the
      # product of two independent negations in two modules
      Given mode "root mode" has effective setting "ht_invert_x" of <invert>
      When my head yaws 5 degrees to the right
      Then the pointer moves <direction> at 300 pixels per second

      Examples:
        | invert | direction |
        | false  | right     |
        | true   | left      |

    Scenario Outline: Vertical inversion
      Given mode "root mode" has effective setting "ht_invert_y" of <invert>
      When my head pitches 5 degrees up
      Then the pointer moves <direction> at 300 pixels per second

      Examples:
        | invert | direction |
        | false  | up        |
        | true   | down      |

  Rule: Pointer control follows the active mode

    Scenario: A mode with head tracking disabled does not move the pointer
      Given the active mode is "reading"
      When my head yaws 20 degrees
      Then the pointer does not move

    Scenario: Switching back re-enables pointer control
      Given the active mode is "reading"
      When I say "root mode"
      And my head yaws 5 degrees
      Then the pointer moves horizontally at 300 pixels per second

    Scenario: The camera keeps running while pointer control is off
      # Gestures still need the feed — see gestures.feature
      Given the active mode is "reading"
      Then the camera is still capturing

    @design-decision
    Scenario: Per-mode sensitivity makes a precision mode possible
      Given a mode "precision" with ht_enabled true and ht_full_speed_angle 30.0
      And the active mode is "precision"
      When my head yaws 5 degrees
      Then the pointer moves horizontally at about 107 pixels per second
      # The same 5 degrees is 300 px/s in root mode

  Rule: No face means no movement

    Scenario: The pointer holds still when the user looks away
      When no face is detected in the frame
      Then the pointer does not move

    Scenario: A dropped frame is skipped, not treated as zero rotation
      When the camera returns a failed frame read
      Then no pointer event is emitted
      And the camera loop continues

  Rule: The configuration is rejected rather than silently reinterpreted

    The units changed by a factor of the frame rate. A configuration that continues to load while
    meaning something sixty times different is the least acceptable outcome, so both keys are
    refused.

    Scenario: The retired per-frame keys are refused with their converted value
      Given the configuration sets "ht_max_speed" to 10
      Then loading fails saying "ht_max_speed_px_per_sec"
      And the message says that 10 corresponds to 600

    Scenario: The retired multiplier is refused
      Given the configuration sets "ht_speed_mult" to 0.25
      Then loading fails naming "ht_max_speed_px_per_sec" and "ht_full_speed_angle"

    Scenario Outline: A misspelled head-tracking key is refused, not ignored
      # ht_blink_thresh, ht_open_thresh and ht_gesture_enabled all sat in a working
      # configuration for months, read by nothing, appearing to tune something.
      Given the configuration sets "<key>" to 0.7
      Then loading fails listing the head-tracking settings that exist

      Examples:
        | key                 |
        | ht_blink_thresh     |
        | ht_open_thresh      |
        | ht_gesture_enabled  |
        | ht_speed_multiplier |

    Scenario: Full speed inside the dead zone is refused
      Given the configuration sets "ht_full_speed_angle" to 2.0
      And the configuration sets "ht_dead_angle_h" to 2.2
      Then loading fails saying full speed has to be reached outside the dead zone
      # Otherwise the pointer has only two speeds: nothing, and everything

  Rule: The head can point at the screen instead of steering toward it

    Rate control keeps the pointer moving only while the head is held off neutral, so head and
    eyes disagree for the whole journey — worst at the edges, where the largest angle is held
    for longest. The other law puts the pointer where the head points, and the eyes stay centred.

    Neither law is superior. An absolute pointer shimmers by several pixels, because a head at
    rest wanders by a fraction of a degree, and the cause is breathing rather than sensor noise, so
    no amount of filtering removes it. Rate control requires no knowledge of the screen and is the
    only law that works before any calibration has been performed. Each mode therefore selects one,
    and the effective pairing is the absolute law to travel with a slow rate mode for the last few
    pixels.

    Scenario: The pointer goes where the head is pointing
      Given a mode using the absolute law and a calibrated screen
      When the head points at a place on the screen
      Then the pointer is put there
      And it is a position rather than a movement, so nothing accumulates

    Scenario: Absolute pointing does not go out as relative motion
      Given a mode using the absolute law
      Then the position goes to a device with absolute axes
      And not to the mouse, whose motion the compositor would accelerate — which would
        amplify a fast head sweep into an overshoot and break the mapping

    Scenario: The absolute law reads the pose, not the offset from neutral
      Given neutral has been moved by a recentre
      When the head holds the same real pose as before
      Then the pointer is put in the same place as before
      And a calibration is measured against a screen, which does not drift

    Scenario: Auto-recentring cannot be combined with the absolute law
      Given a mode using the absolute law
      And auto-recentring is switched on
      Then the configuration is refused at load
      And the reason is that the two would fight and the mapping would quietly stop being true

    Scenario: Asking for the absolute law with nothing to point at is refused
      Given a mode using the absolute law and no screen calibration
      Then the configuration is refused, naming the mode
      And that is better than parking the pointer, which looks exactly like a dead camera

    Scenario: A held shape swaps the law for the last few pixels
      Given a mode using the absolute law and a shape bound to press and release
      When the shape is held
      Then the *voice* layer alone moves to the slow rate mode, because that is the layer the
        pointer law is read off
      And the gesture layer stays where it is, so the release is still found — an unqualified
        switch would take it along, and nothing would be able to bring the pointer back
      And the head's current pose is taken as neutral on the way in, or the rate law starts
        however far off neutral the travelling left it, which is past full speed

    Scenario: A mode using the rate law is unaffected by any of this
      Given a mode that names no control law
      Then it uses rate control, as every configuration written before this did
      And it needs no calibration

  Rule: Which way is left is measured, not reasoned

    Mediapipe reports +yaw for a head turned toward the *left* of the screen and +pitch for one
    tipped *down*. Both are the opposite of the intuitive reading, and a fitted mapping absorbs
    either convention into its own coefficients, so a session cannot report which convention is
    in force, and neither can a test written from the same misreading.

    Scenario: The tape measure and the rate law agree about which way is left
      Given a head turned toward the left of the screen
      Then the rate law moves the pointer left
      And the mapping a tape measure gives puts it left
      And the fitted mapping, which absorbs the convention either way round, cannot say

    Scenario: The mapping that is not fitted is the one that has to state the convention
      Given a mapping built from width, height and distance rather than from dots
      Then it carries the signs the hardware reports
      And getting either wrong sends the pointer to the opposite corner

  Rule: Where the head points is measured, not assumed

    A tape measure cannot establish where the camera is mounted, where the head sits, or
    what the pose estimate's own bias is. A fitted grid of dots establishes all three.

    Scenario: The head is asked to point at nine known places
      Given a calibration session
      Then a dot appears on the screen, one at a time
      And it is drawn over everything, because a dot behind a window cannot be pointed at

    Scenario: Holding still records the dot, because there is no keyboard
      Given the head is pointing at a dot
      When the pose stops changing for long enough
      Then that dot is recorded and the next one appears
      And how far through the dwell it is, is shown, so it is clear it is working

    Scenario: A dot is not answered by the pose left over from the dot before it
      Given a dot has just been recorded, so the head is perfectly still on it
      When the next dot appears
      Then holding that same pose records nothing, however long it is held
      And the dwell begins only once the head has moved off where it started
      And without that, the budget for finding a new dot is one dwell, which a session clears by a
        negligible margin and which then presents as a careful session containing noise

    Scenario: The first dot is given time to be found
      Given the first dot, which has no previous dot to be confused with
      Then it is shown for a lead-in before anything the head does can record it
      And it is not gated on movement, because a head already pointing at the middle of the
        screen is giving the right answer rather than a stale one
      And without the lead-in it records a head that is still reading the instructions

    Scenario: The dot's answer is the whole dwell, not the frame it ended on
      Given a head that has held still on a dot
      Then what is recorded is the average pose over the hold
      And one frame would carry the whole per-frame noise where two dozen carry a fifth of it

    Scenario: The session measures the same signal it calibrates
      Given the pose the tracker smooths before any pointer sees it
      Then the calibration smooths it the same way, with the same configured constants
      And unsmoothed, jitter alone crosses the settle tolerance and restarts the dwell
      And the symptom is the progress ring repeatedly resetting to empty

    Scenario: The ring is left closed on the dot it recorded
      Given a dot whose dwell has just completed
      Then the closed ring stays up until the next dot appears
      And publishing an empty one straight after would race it to the display, which draws
        only the last frame offered — so the one moment worth confirming would show nothing

    Scenario: A dot nobody can settle on is skipped rather than stalling the session
      Given a dot the head cannot hold still on
      Then it is given up on after a while
      And the mapping is fitted from the ones that did settle

    Scenario: The mapping absorbs a camera that is not square to the head
      Given a camera mounted above the screen and aimed down at the face
      Then the fitted mapping still puts the pointer where the head points
      And that is exact rather than approximate, because a ray meeting a flat screen is a
        projective map and the fit is one

    Scenario: Dots that cannot decide a mapping are refused
      Given calibration points that lie in a line, or a head that never moved
      Then fitting raises rather than returning a mapping that works along one axis
      And a pointer that works horizontally but not vertically is the hardest failure to
        diagnose while using it

    Scenario: The fit is the best one available and not just the linear solve
      Given the dots have been solved by direct linear transform
      Then that answer is polished against the error the residual is measured in
      And the linear solve minimises an algebraic error weighted by its own denominator, so
        it cares least about the dots where that denominator is smallest — which under a
        camera aimed down is the bottom row, the hardest row to point at

    Scenario: How far out the fit is, is reported in pixels
      Given a fitted mapping
      Then the residual is printed as pixels rather than as a fraction
      And so is what the mapping is expected to be worth away from the dots, which is the
        one to judge a session by

    Scenario: The residual is not the number a session is judged by
      Given a residual, which is the error on the very dots the coefficients were chosen for
      Then part of it is noise the fit has already absorbed and will never make again
      And the number reported beside it corrects for the degrees of freedom left over
      And a fit from the fewest dots that can decide a mapping reports no confidence at all,
        because it passes through every point by construction

    Scenario: More dots is a better mapping and a worse-looking residual
      Given a session asked for more dots than the default
      Then the same eight coefficients are fitted from more samples, so they chase the noise
        less and the mapping is worth more
      And the residual rises, because more noise was sampled
      And optimising the residual would therefore be optimising the wrong direction

    Scenario: A grid nobody should be asked for is refused before the camera opens
      Given a grid size below what leaves the fit anything spare, or above a minute and a
        half of holding a head still
      Then it is refused when the argument is read
      And not after a configuration, a camera and a display socket have been opened

    Scenario: Consecutive dots are never a single cell apart
      Given a denser grid
      Then the dots are asked for every other cell and then the ones in between
      And a raster order would put consecutive dots one cell apart, which on a real screen
        is barely over the movement the dwell waits for

    Scenario: The session says how much head angle it used
      Given a fitted mapping
      Then the degrees the head covered along each axis are reported, and the narrower named
      And that is measured from the mean pose of each row and each column, not from the
        extremes, which are two samples carrying the very scatter being described
      And it is the lever that matters: the camera's height is absorbed exactly by the fit,
        but a head that covers the screen with its eyes has fewer degrees to spread the
        same pixels over

    Scenario: A dot the fit throws off the screen is reported as far off as it went
      Given a fit that puts a dot past the edge of the screen
      Then the residual counts the whole distance, not the distance to the edge
      And the pointer still clamps to the edge, because out there that is the honest answer
      And measuring the residual through that clamp flatters the fit into looking better

    Scenario: The screen the dots were drawn on is not always the whole desktop
      Given a second monitor
      Then the dots go on one output, because that is what a layer surface anchored to four
        edges is
      And the pointer's device is spread across every output by the compositor
      And so the mapping is told which rectangle of the desktop the calibrated screen is,
        or it puts the pointer on the wrong screen while every fraction in the chain stays
        a perfectly good fraction of something

    Scenario: The absolute pointer cannot leave the screen it was measured against
      Given a mapping and a second monitor
      Then the clamp happens on the calibrated screen, before the desktop rectangle applies
      And that is the honest edge, because the mapping is one flat plane and the other
        monitor is not on it
      And a rate-law mode is how the pointer gets across

    Scenario: An axis inverted is the monitor mirrored, not the desktop
      Given a calibrated screen that is one half of the desktop
      When an axis is inverted
      Then the pointer mirrors within that screen
      And mirroring the desktop instead would land it on whichever monitor sits opposite

    Scenario: Calibrating claims no session and types nothing
      Given the calibration tool is running
      Then it opens no uinput device and switches no mode
      And the configuration file it is about to change may stay open in an editor
