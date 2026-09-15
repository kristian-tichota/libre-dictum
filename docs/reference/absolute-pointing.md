# Absolute pointing

Two control laws, selectable per mode through `ht_pointer_law`. Under the **rate** law head rotation
sets pointer velocity; it needs no screen geometry and operates before anything has been calibrated.
Under the **absolute** law head orientation sets pointer position, so gaze need not leave the target.

Both are kept. The rate law requires the head to be held off neutral for the whole journey while
the target is being looked at, so head and gaze disagree throughout, worst at the screen edges. The
absolute law removes that and asks for less neck movement, because a screen corner subtends less yaw
than the angle at which the rate law reaches full speed. What the absolute law cannot do is hold
still: head pose at rest wanders by a fraction of a degree, which is several pixels of shimmer.
Breathing and postural sway cause it rather than sensor noise, so filtering trades it for lag
instead of removing it. That amplitude is acceptable for a button and unacceptable for a text caret,
so **the absolute law travels and a slow rate mode finishes**
([configuration.md](configuration.md#two-control-laws)).

## Calibration

`--calibrate-screen` draws a grid of dots, 3 to 6 a side. The **nose** is aimed at each dot and held
still, and the sample is taken once the pose stops moving, so nothing that moves the head at the
moment of measurement takes part. Four properties of that procedure are load-bearing. The first dot
gets a 2.5 s lead-in, because stillness measured against a head parked on the previous dot settles
in one dwell. The sample is the dwell's average, not its last frame. The pose is smoothed the way a
running session smooths it, or raw jitter crosses the settle tolerance unaided. The gaps between
dots read frames rather than sleeping, or the queue hands over stale ones.

**The residual is not the figure to optimise.** It is RMS error against the very dots the eight
coefficients were fitted to, so it over-states the mapping, and it moves in the *opposite* direction
from the expected error as dots are added. The expected error is `residual · sqrt(p / (M − p))`,
where `M = 2·samples` and `p = 8`. The report therefore leads with the expected error, and the
advice threshold of 90 px is set on it. Both figures are computed through the **unclamped**
projection, because through the clamp a fit that throws a dot past a screen corner reports the
corner instead and appears better than it is.

Accuracy is set by **how much head angle the session covers**, not by the camera and not by its
height: a ray meeting a flat screen is a projective map the fit absorbs exactly. The report names
the narrower axis, measured off the mean pose of each row and column rather than off the extremes,
which are two samples carrying the very scatter being characterised.

## Mapping

A homography in `(tan yaw, tan pitch / cos yaw)`, the ray's coordinates on a plane one unit ahead,
`fx/fz` and `−fy/fz`. Two corrections are required, and neither is visible in a running session:

- **Bilinear interpolation is not sufficient**, having the same eight degrees of freedom and the
  wrong shape: a constant angular offset composes projectively rather than affinely.
- **The `cos yaw` term is geometry, not a correction factor.** `extract_yaw_pitch` measures pitch
  from the horizontal *plane*, so `tan pitch` is `−fy/hypot(fx, fz)`. Fitted against the spherical
  pair instead, the error grows toward the edges and grows further as the camera is aimed downward.

The eight coefficients come from the DLT and MUST then be polished by Levenberg-Marquardt, which
minimises the geometric error. The DLT weights each equation by its own denominator and so gives
least weight to the dots where that denominator is smallest, which on a downward-aimed camera is a
whole row. Two rank checks are required, because a pose-spread check alone accepts a session in
which the head moved plenty but the settled targets are collinear.

**Sign conventions.** Mediapipe's frame has X toward the *image's* right, which an unmirrored webcam
makes the user's left. So `+yaw` is a head turned toward the **left of the screen** and `+pitch` is
**down**, both the opposite of the intuitive reading. A fitted mapping absorbs either convention, so
neither a session nor a test written from the same misreading can detect a swap. `from_geometry` is
the one place that MUST state them explicitly.

## Limitations

**Leaning** is not compensated; the mapping is fitted for a head in one place. The translation is
recorded so the correction can be fitted later, but that needs a session that leans deliberately.

**More than one screen** is not handled. The mapping is one flat plane, so the pointer clamps to the
monitor it was measured against and a rate mode covers the rest. Which monitor that is MUST be
written down in [`ht_screen_bounds`](configuration.md#two-monitors), because
`--calibrate-screen` draws on one output while the tablet device spans the whole desktop; with a
single monitor those are the same rectangle. The protocol is one-way, so nothing here can measure
it.

## Pointer output

A **second uinput device** carrying absolute axes on 0..65535, shaped like QEMU's `usb-tablet`.
Relative motion is not usable, because it passes through libinput's pointer acceleration, which
would amplify a fast head sweep into an overshoot. Buttons stay on the relative mouse.

Pair the absolute law with a slow rate mode and place **`recenter()`** in the binding that enters
that mode. Travelling by pointing leaves the head well past `ht_full_speed_angle`, so without the
recentre the pointer accelerates away at the moment fine control was requested. `recenter()` is
inert under the absolute law.
