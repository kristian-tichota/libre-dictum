@head-tracking @design-decision
Feature: Finding the camera without being told which one

  A `/dev/videoN` number is not a property of a camera. The kernel hands the numbers out in
  probe order, so they move when a hub enumerates differently or a camera is plugged in before
  boot instead of after — and one UVC webcam takes two of them: frames on one node, per-frame
  metadata on the other, the same card name on both.

  So the index a config file can state is a guess that expires, and the way it expires is the
  worst kind: the metadata node opens, reports success, and never delivers a frame. Nothing is
  logged because nothing failed. The pointer does not move, and on every boot the user
  guesses again.

  The camera is therefore named the way the pedal board already is — by what it is, not by where
  it landed:

    nothing    the camera is found
    a name     matched against the card name or the bus id, case-insensitively
    a path     `/dev/video0`, or a `/dev/v4l/by-id/…` symlink the kernel builds from the
               device's own vendor, product and serial
    an index   still accepted, and still a pin

  Every node is asked what it is (`VIDIOC_QUERYCAP`) before any of them is opened, because that
  ioctl is the only thing that distinguishes a camera's picture from its metadata: sysfs reports
  no capability, and both nodes carry the same name.

  Background:
    Given the configuration:
      """
      {
        "enable_head_tracking": true,
        "ht_model_path": "models/face_landmarker.task",
        "modes": {
          "root mode": { "type": "vosk", "path": "models/small-en", "ht_enabled": true }
        }
      }
      """

  Rule: A camera nobody named is found

    @accessibility-critical
    Scenario: One webcam, two video nodes
      Given video0 reports frames and video1 reports metadata, both named "i-tec SOLOMON 300"
      When head tracking starts
      Then video0 is opened
      And an info line names the camera it opened

    Scenario: The numbers moved since the last boot
      Given video2 reports frames and video3 reports metadata, both named "i-tec SOLOMON 300"
      When head tracking starts
      Then video2 is opened
      And nothing has to be edited

    Scenario: A node that opens but delivers no frame is passed over
      # A node can answer for itself and still refuse to stream: something else holds it, or
      # no capture format was agreed. Only a frame proves a camera.
      Given video0 and video1 both report frames
      And video0 delivers no frame
      When head tracking starts
      Then video1 is opened

    Scenario: A driver too old to say what a node is gets tried anyway
      Given video0 reports no capabilities at all
      When head tracking starts
      Then video0 is opened

    Scenario: There is no camera
      Given there are no video devices
      When head tracking starts
      Then an error is logged saying there are no video devices at all
      And speech recognition is still working

    Scenario: Every node is a metadata node
      Given video0 reports metadata and nothing else does
      When head tracking starts
      Then an error is logged saying no video device hands out frames
      And the error names video0 and what it is

  Rule: A name outranks the order the kernel probed in

    @tinkerer
    Scenario: A name matches part of the card name
      # The kernel truncates a card name to 31 characters, so requiring the whole of it would
      # mean transcribing a string that has already been cut in half.
      Given the configuration has "camera" set to "SOLOMON"
      And video0 is named "Integrated Camera" and video2 is named "i-tec SOLOMON 300: i-tec SOLOMO"
      When head tracking starts
      Then video2 is opened

    @tinkerer
    Scenario: A bus id names a port rather than a camera
      Given two cameras report the same card name on different bus ids
      And the configuration names one of those bus ids
      When head tracking starts
      Then the camera on that bus is opened

    @tinkerer
    Scenario: A name nothing answers to
      Given the configuration has "camera" set to "Integrated"
      And the only camera is named "i-tec SOLOMON 300: i-tec SOLOMO"
      When head tracking starts
      Then an error is logged saying no video device is called that
      And the error lists every video device there is

    @tinkerer
    Scenario: A by-id symlink means whichever node it points at today
      Given the configuration has "camera" set to a "/dev/v4l/by-id/…" path
      And that symlink points at video2
      When head tracking starts
      Then video2 is opened

    @tinkerer
    Scenario: A device path that is not there
      Given the configuration names a "/dev/v4l/by-id/…" path that does not exist
      When head tracking starts
      Then an error is logged saying there is no such device node

  Rule: An index is still a pin, and says so when it misses

    Scenario: A stale index landed on the same camera's metadata
      # The exact failure this feature exists for, now loud instead of silent.
      Given the configuration has "camera" set to 1
      And video0 reports frames and video1 reports metadata
      When head tracking starts
      Then an error is logged saying video1 is that camera's metadata, not its picture
      And the error says to remove "camera" to have one found

    Scenario: An index that is not there lists what is
      Given the configuration has "camera" set to 4
      And the only video device is video0
      When head tracking starts
      Then an error is logged naming video0

  Rule: The key it replaced still works, and says it is going

    @tinkerer
    Scenario: camera_index is honoured
      Given the configuration has "camera_index" set to 0
      When the configuration is loaded
      Then it loads
      And a warning says "camera_index" is deprecated and why

    @tinkerer
    Scenario: Naming the camera twice is a contradiction, not a precedence puzzle
      Given the configuration has both "camera" and "camera_index" set
      When the configuration is loaded
      Then the configuration is rejected naming both keys

    @tinkerer
    Scenario: An index that is not a number is rejected at load
      Given the configuration has "camera_index" set to "first"
      When the configuration is loaded
      Then the configuration is rejected saying it must be a whole number

  Rule: The config file can be written without guessing

    @tinkerer
    Scenario: Asking which camera would be used
      Given video0 reports frames and video1 reports metadata, both named "i-tec SOLOMON 300"
      When "libre-dictum --cameras" is run
      Then both video devices are listed with what each one is
      And it names video0 as the one that would be opened
      And it prints a "camera" value that can be pasted into the config file
      And it prints the "/dev/v4l/by-id/…" path as well, when the kernel made one
      And no camera was opened, no session was claimed and no config file was needed

    @tinkerer
    Scenario: Asking when there is no camera
      Given there are no video devices
      When "libre-dictum --cameras" is run
      Then it says there are no video devices at all
      And it exits with a failure

  Rule: None of this can end the session

    @accessibility-critical
    Scenario: Every failure above costs the pointer and nothing else
      Given head tracking cannot find a camera for any of the reasons above
      When head tracking starts
      Then speech recognition is still working
      And the tray icon shows the fault colour
      And saying the reload phrase looks for the camera again
