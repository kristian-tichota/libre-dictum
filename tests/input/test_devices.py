import math

import pytest

from libre_dictum.input.devices import ABSOLUTE_RANGE, UinputBackend
from libre_dictum.pointer import PointerFilter
from libre_dictum.settings import PointerSettings

evdev_ecodes = pytest.importorskip("evdev.ecodes", reason="evdev is not installed")

DT = 1 / 60


class StubUInput:
    """Records what would reach /dev/uinput, opening nothing."""

    def __init__(self):
        self.writes = []
        self.syns = 0
        self.closed = False

    def write(self, kind, code, value):
        self.writes.append((kind, code, value))

    def syn(self):
        self.syns += 1

    def close(self):
        self.closed = True

    @property
    def relative(self):
        """The (dx, dy) pairs written, as whole pixels."""
        moves = [v for kind, _code, v in self.writes if kind == evdev_ecodes.EV_REL]
        return list(zip(moves[::2], moves[1::2], strict=True))

    @property
    def absolute(self):
        """The (x, y) pairs written, as device axis values."""
        moves = [v for kind, _code, v in self.writes if kind == evdev_ecodes.EV_ABS]
        return list(zip(moves[::2], moves[1::2], strict=True))


@pytest.fixture
def mouse():
    return StubUInput()


@pytest.fixture
def tablet():
    return StubUInput()


@pytest.fixture
def backend(mouse, tablet):
    return UinputBackend(StubUInput(), mouse, tablet)


class TestWholePixels:
    def test_the_device_only_ever_sees_integers(self, backend, mouse):
        for _ in range(10):
            backend.move_relative(1.5, -2.25)

        assert mouse.relative
        assert all(isinstance(v, int) for pair in mouse.relative for v in pair)

    def test_a_movement_smaller_than_a_pixel_writes_nothing_yet(self, backend, mouse):
        backend.move_relative(0.4, 0.4)

        assert mouse.relative == []
        assert mouse.syns == 0

    def test_but_it_is_owed_and_paid_on_a_later_call(self, backend, mouse):
        for _ in range(3):
            backend.move_relative(0.4, 0.4)

        assert mouse.relative == [(1, 1)]

    def test_the_remainder_does_not_accumulate_beyond_a_pixel(self, backend, mouse):
        for _ in range(100):
            backend.move_relative(0.5, 0.0)

        assert sum(dx for dx, _ in mouse.relative) == 50

    @pytest.mark.parametrize("sign", [1, -1])
    def test_it_carries_in_both_directions(self, backend, mouse, sign):
        for _ in range(4):
            backend.move_relative(sign * 0.5, 0.0)

        assert sum(dx for dx, _ in mouse.relative) == sign * 2

    def test_a_reversal_does_not_leave_a_stale_fraction_behind(self, backend, mouse):
        backend.move_relative(0.6, 0.0)
        backend.move_relative(-0.6, 0.0)
        backend.move_relative(0.6, 0.0)

        assert sum(dx for dx, _ in mouse.relative) == 0


class TestNothingIsLost:
    def test_an_eased_sweep_that_used_to_travel_nothing_now_travels(self, backend, mouse):
        """A 4 degree sweep sits entirely under a pixel per frame."""
        settings = PointerSettings(
            enabled=True,
            dead_angle_h=2.2,
            dead_angle_v=2.2,
            speed_power=1.5,
            full_speed_angle=18.0,
            max_speed_px_per_sec=800.0,
        )
        pointer = PointerFilter(settings)
        wanted = 0.0
        for frame in range(120):
            movement = pointer.filter(4.0 * math.sin(math.pi * frame / 119), 0.0, DT)
            if movement is not None:
                wanted += abs(movement[0])
                backend.move_relative(*movement)

        travelled = sum(abs(dx) for dx, _ in mouse.relative)
        assert travelled > 0
        assert travelled == pytest.approx(wanted, abs=1.0)

    def test_no_event_is_emitted_for_a_zero_movement(self, backend, mouse):
        backend.move_relative(0.0, 0.0)

        assert mouse.writes == []
        assert mouse.syns == 0


class TestKeys:
    def test_pressing_and_releasing_a_key_writes_both_edges(self, mouse, tablet):
        keyboard = StubUInput()
        backend = UinputBackend(keyboard, mouse, tablet)

        backend.press("ctrl")
        backend.release("ctrl")

        assert [value for _kind, _code, value in keyboard.writes] == [1, 0]

    def test_a_mouse_button_goes_to_the_mouse_device(self, backend, mouse):
        backend.press("left_mouse")

        assert [value for _kind, _code, value in mouse.writes] == [1]

    def test_closing_closes_every_device(self, mouse, tablet):
        keyboard = StubUInput()
        backend = UinputBackend(keyboard, mouse, tablet)

        backend.close()

        assert keyboard.closed
        assert mouse.closed
        assert tablet.closed


class TestAbsolutePointing:
    """The other control law's way out."""

    def test_a_normalised_position_becomes_a_device_axis_value(self, backend, tablet):
        backend.move_absolute(0.0, 1.0)
        backend.move_absolute(0.5, 0.5)
        backend.move_absolute(1.0, 0.0)

        assert tablet.absolute == [(0, ABSOLUTE_RANGE), (32768, 32768), (ABSOLUTE_RANGE, 0)]

    def test_it_goes_to_the_tablet_and_not_to_the_mouse(self, backend, mouse, tablet):
        backend.move_absolute(0.25, 0.75)

        assert tablet.absolute and not mouse.writes

    def test_a_position_off_the_screen_lands_on_the_edge(self, backend, tablet):
        backend.move_absolute(-3.0, 42.0)

        assert tablet.absolute == [(0, ABSOLUTE_RANGE)]

    def test_every_position_is_synced_so_the_pointer_actually_moves(self, backend, tablet):
        backend.move_absolute(0.4, 0.6)

        assert tablet.syns == 1

    def test_the_same_position_twice_is_still_written(self, backend, tablet):
        backend.move_absolute(0.4, 0.6)
        backend.move_absolute(0.4, 0.6)

        assert len(tablet.absolute) == 2
