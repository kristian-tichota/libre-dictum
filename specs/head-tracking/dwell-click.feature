@head-tracking
Feature: Clicking by holding the head still

  A mode under the absolute law can click where the head stops. A stop clicks only when it ends a
  deliberate move, because the head also pauses while it reads and while it rests.

  Background:
    Given mode "mouse mode" points the head absolutely with "ht_dwell_seconds" of 0.8
    And the active mode is "mouse mode"

  Rule: Only an arrival clicks, and only once

    Scenario: A stop after a deliberate move clicks
      When my head turns 10 degrees and holds still for 0.8 seconds
      Then the left mouse button is clicked once

    Scenario: A stop that nothing led up to does not click
      When my head holds still for 3 seconds
      Then nothing is clicked

    Scenario: Holding still after the click does not click again
      When my head turns 10 degrees and holds still for 3 seconds
      Then the left mouse button is clicked once

    Scenario: Leaving the stop before the dwell ends cancels it
      When my head turns 10 degrees, holds still for 0.6 seconds and turns 2 degrees
      Then the ring is taken away and nothing is clicked

    Scenario: A correction just after the move still clicks
      # The arrival is any travel in the second before the stop, so a stop that follows a short
      # correction keeps it.
      When my head turns 10 degrees, holds still for 0.3 seconds and turns 1.5 degrees
      And it holds still for 0.8 seconds
      Then the left mouse button is clicked once

  Rule: The click lands where the ring is drawn

    Scenario: The ring fills over the second half of the dwell
      When my head turns 10 degrees and holds still for 0.3 seconds
      Then no ring is drawn
      When it holds still for another 0.3 seconds
      Then a ring around the pointer is half full

    Scenario: The pointer is held at the stop's mean while the ring fills
      When my head turns 10 degrees and holds still with 0.3 degrees of wander
      Then the pointer stays at the mean pose once the ring is drawn
      And the click lands there

  Rule: Other input takes precedence

    Scenario Outline: A stop does not click when other input acted on it
      Given <input>
      When my head turns 10 degrees and holds still for 0.8 seconds
      Then nothing is clicked

      Examples:
        | input                                        |
        | a fist holds the left mouse button           |
        | a pinch clicked during the stop              |
        | the gesture layer is asleep                  |

    Scenario: A dwell under the rate law is refused at load
      # Only the absolute law knows where the pointer is.
      Given mode "precision mode" sets "ht_dwell_seconds" under the rate law
      When the configuration is loaded
      Then loading fails naming "precision mode"
