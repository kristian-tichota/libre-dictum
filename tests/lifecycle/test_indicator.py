import pytest

pytest.importorskip("PIL", reason="system-tray extra not installed")

from libre_dictum.layers import Layer, LayerState, SleepView  # noqa: E402
from libre_dictum.status import MODIFIER_ORDER, Grip, HeldInput, HeldKey  # noqa: E402
from libre_dictum.systray import (  # noqa: E402
    ASLEEP_BRIGHTNESS,
    FAULT_COLOUR,
    GLYPHS,
    LAYER_ARCS,
    MAX_GLYPHS,
    _boxes,
    _ink,
    base_image,
    dimmed,
    fault_image,
    mode_image,
    with_held_keys,
    with_layer_arcs,
)

GREEN = (0, 200, 0)

TRAY_SIZE = 22


def _luminance(colour):
    return 0.299 * colour[0] + 0.587 * colour[1] + 0.114 * colour[2]


class TestFaultImage:
    def test_the_fault_is_not_just_a_colour(self):
        assert fault_image().tobytes() != mode_image(FAULT_COLOUR).tobytes()

    @pytest.mark.parametrize("rgb", [(0, 255, 0), (255, 0, 0), (0, 0, 255), FAULT_COLOUR])
    def test_it_differs_from_every_mode_colour(self, rgb):
        assert fault_image().tobytes() != mode_image(rgb).tobytes()

    def test_both_images_are_the_same_size(self):
        assert fault_image().size == mode_image((1, 2, 3)).size


class TestHeldKeyGlyphs:
    """A held modifier is visible on the icon."""

    def held(self, **kwargs):
        return HeldInput(**kwargs).pressed()

    def test_nothing_held_leaves_the_icon_alone(self):
        base = mode_image(GREEN)

        assert with_held_keys(base, ()) is base

    def test_a_held_modifier_changes_the_icon(self):
        base = mode_image(GREEN)

        assert with_held_keys(base, self.held(held=("shift",))).tobytes() != base.tobytes()

    def test_the_base_image_is_not_touched(self):
        base = mode_image(GREEN)
        before = base.tobytes()

        with_held_keys(base, self.held(held=("shift",)))

        assert base.tobytes() == before

    def test_held_and_pending_look_different(self):
        base = mode_image(GREEN)

        assert (
            with_held_keys(base, (HeldKey("shift", Grip.HELD),)).tobytes()
            != with_held_keys(base, (HeldKey("shift", Grip.PENDING),)).tobytes()
        )

    @pytest.mark.parametrize("name", MODIFIER_ORDER)
    def test_every_modifier_is_drawn_differently(self, name):
        base = mode_image(GREEN)
        drawn = {
            modifier: with_held_keys(base, (HeldKey(modifier, Grip.HELD),)).tobytes()
            for modifier in MODIFIER_ORDER
        }

        assert name in GLYPHS
        assert len(set(drawn.values())) == len(MODIFIER_ORDER)

    def test_a_key_that_is_not_a_modifier_is_drawn_too(self):
        base = mode_image(GREEN)

        assert (
            with_held_keys(base, self.held(held=("a",))).tobytes()
            != with_held_keys(base, ()).tobytes()
        )

    def test_more_keys_than_fit_still_draw(self):
        base = mode_image(GREEN)
        crowded = self.held(held=("shift", "a"), pending=("ctrl", "alt"))

        assert len(crowded) > MAX_GLYPHS
        assert with_held_keys(base, crowded).size == base.size

    @pytest.mark.parametrize("count", [1, 2, MAX_GLYPHS])
    def test_glyphs_stay_inside_the_circle(self, count):
        size = 256
        centre, radius = size / 2, size / 2
        for x0, y0, x1, y1 in _boxes(count, size):
            for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                assert (x - centre) ** 2 + (y - centre) ** 2 < radius**2

    @pytest.mark.parametrize(
        "colour", [(255, 255, 255), (0, 0, 0), (250, 250, 60), (0, 200, 0), FAULT_COLOUR]
    )
    def test_the_glyph_contrasts_with_the_mode_colour(self, colour):
        ink, halo = _ink(mode_image(colour), 255)

        assert abs(_luminance(ink) - _luminance(colour)) > 90
        assert abs(_luminance(ink) - _luminance(halo)) > 90


BLUE = (0, 0, 255)


def _colours(image):
    return {colour for _, colour in image.convert("RGBA").getcolors(maxcolors=1 << 20)}


class TestLayerArcs:
    """A layer that has moved has to be visible without hovering over the tray."""

    def test_layers_that_agree_leave_the_icon_alone(self):
        base = mode_image(GREEN)

        assert with_layer_arcs(base, []) is base

    def test_a_diverged_layer_paints_its_own_mode_colour(self):
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, BLUE)])

        assert (*BLUE, 255) in _colours(arced)

    def test_the_arc_survives_being_scaled_to_the_tray(self):
        plain = mode_image(GREEN).resize((TRAY_SIZE, TRAY_SIZE))
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, BLUE)]).resize(
            (TRAY_SIZE, TRAY_SIZE)
        )
        was = list(plain.convert("RGB").tobytes())
        now = list(arced.convert("RGB").tobytes())
        bluer = [1 for i in range(2, len(was), 3) if now[i] > was[i] + 40]

        assert len(bluer) >= 30, "the arc is not visible at tray size"

    def test_the_arc_stays_off_the_middle_where_the_glyphs_go(self):
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.GESTURE, BLUE), (Layer.PEDAL, BLUE)])
        third = arced.width // 3
        middle = arced.convert("RGBA").crop((third, third, 2 * third, 2 * third))

        assert _colours(middle) == {(*GREEN, 255)}

    def test_the_mode_colour_stays_the_thing_you_see_first(self):
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, BLUE)])
        counts = {colour: n for n, colour in arced.convert("RGBA").getcolors(1 << 20)}

        assert counts[(*GREEN, 255)] > 4 * counts[(*BLUE, 255)]

    def test_the_base_image_is_not_touched(self):
        base = mode_image(GREEN)
        before = base.tobytes()
        with_layer_arcs(base, [(Layer.PEDAL, BLUE)])

        assert base.tobytes() == before

    def test_each_layer_has_its_own_place_on_the_rim(self):
        gesture = with_layer_arcs(mode_image(GREEN), [(Layer.GESTURE, BLUE)])
        pedal = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, BLUE)])

        assert gesture.tobytes() != pedal.tobytes()
        assert set(LAYER_ARCS) == {Layer.GESTURE, Layer.PEDAL}
        (g_start, g_end), (p_start, p_end) = LAYER_ARCS[Layer.GESTURE], LAYER_ARCS[Layer.PEDAL]
        assert g_end < p_start or p_end < g_start

    def test_a_mode_with_no_icon_still_gets_a_visible_arc(self):
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, None)])

        assert arced.tobytes() != mode_image(GREEN).tobytes()

    def test_both_arcs_can_be_drawn_at_once(self):
        arced = with_layer_arcs(
            mode_image(GREEN), [(Layer.GESTURE, (255, 0, 0)), (Layer.PEDAL, BLUE)]
        )
        colours = _colours(arced)

        assert (255, 0, 0, 255) in colours
        assert (*BLUE, 255) in colours

    def test_held_glyphs_still_fit_over_an_arced_icon(self):
        arced = with_layer_arcs(mode_image(GREEN), [(Layer.PEDAL, BLUE)])
        keys = HeldInput(held=("shift",)).pressed()

        assert with_held_keys(arced, keys).tobytes() != arced.tobytes()


class TestTheAsleepImage:
    """The same icon, gone quiet."""

    def test_it_keeps_the_shape(self):
        base = mode_image(GREEN, TRAY_SIZE)

        quiet = dimmed(base)

        assert quiet.size == base.size
        assert quiet.split()[3].tobytes() == base.split()[3].tobytes()

    def test_it_turns_the_light_down(self):
        base = mode_image(GREEN, TRAY_SIZE)

        quiet = dimmed(base)

        centre = (TRAY_SIZE // 2, TRAY_SIZE // 2)
        assert quiet.getpixel(centre)[:3] == tuple(
            int(channel * ASLEEP_BRIGHTNESS) for channel in GREEN
        )

    def test_it_stays_visible(self):
        quiet = dimmed(mode_image(GREEN, TRAY_SIZE))

        assert any(channel > 0 for channel in quiet.getpixel((TRAY_SIZE // 2,) * 2)[:3])


class TestWhichDiscTheIconSitsOn:
    def test_asleep_dims_the_mode_colour(self):
        base = base_image(mode_image(GREEN, TRAY_SIZE), fault=False, asleep=True)

        assert base is not None
        assert base.getpixel((TRAY_SIZE // 2,) * 2)[:3] != GREEN

    def test_awake_leaves_it_alone(self):
        base = base_image(mode_image(GREEN, TRAY_SIZE), fault=False, asleep=False)

        assert base is not None
        assert base.getpixel((TRAY_SIZE // 2,) * 2)[:3] == GREEN

    def test_a_fault_outranks_the_mode_colour(self):
        base = base_image(mode_image(GREEN, TRAY_SIZE), fault=True, asleep=False)

        assert base is not None
        assert base.getpixel((base.width // 4, base.height // 2))[:3] == FAULT_COLOUR

    def test_a_faulted_mechanism_that_is_asleep_is_still_faulted(self):
        lit = base_image(None, fault=True, asleep=False)
        quiet = base_image(None, fault=True, asleep=True)

        assert lit is not None and quiet is not None
        spot = (lit.width // 4, lit.height // 2)
        assert 0 < quiet.getpixel(spot)[0] < lit.getpixel(spot)[0]

    def test_a_mode_with_no_icon_has_no_disc_to_dim(self):
        assert base_image(None, fault=False, asleep=True) is None


class TestTheTooltip:
    """A glyph is a reminder."""

    def test_it_names_every_running_layer(self):
        state = LayerState(voice="command mode", pedal="scrolling")

        assert state.summary() == "voice: command mode, pedal: scrolling"

    def test_a_layer_that_is_not_running_is_left_out(self):
        assert LayerState(voice="command mode").summary() == "voice: command mode"

    def test_it_says_what_is_asleep(self):
        assert SleepView(asleep=frozenset({Layer.GESTURE})).summary() == "asleep: gesture"

    def test_everything_asleep_needs_no_list(self):
        assert SleepView(asleep=frozenset(Layer)).summary() == "asleep"

    def test_awake_says_so(self):
        assert SleepView().summary() == "awake"
