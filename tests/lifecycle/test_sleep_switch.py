from __future__ import annotations

from libre_dictum.layers import (
    ALL_LAYERS,
    Confirmation,
    Layer,
    SleepSwitch,
    SleepView,
    parse_layers,
)


class TestNothingHappensOnTheFirstAsk:
    """The whole misfire guard: two independent misfires in one window, or nothing."""

    def test_the_first_request_only_arms(self):
        switch = SleepSwitch()

        assert switch.request(ALL_LAYERS, asleep=True, now=0.0) is Confirmation.ARMED
        assert not switch.asleep(Layer.VOICE)
        assert not switch.all_asleep

    def test_the_same_request_repeated_applies_it(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=1.0) is Confirmation.APPLIED
        assert switch.all_asleep

    def test_a_repeat_after_the_window_arms_again_instead(self):
        switch = SleepSwitch(confirm_seconds=5.0)
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=5.01) is Confirmation.ARMED
        assert not switch.all_asleep

    def test_a_repeat_exactly_on_the_deadline_still_counts(self):
        switch = SleepSwitch(confirm_seconds=5.0)
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=5.0) is Confirmation.APPLIED

    def test_a_different_request_re_arms_rather_than_confirming(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=1.0) is Confirmation.ARMED
        assert not switch.all_asleep
        assert switch.request(ALL_LAYERS, asleep=True, now=2.0) is Confirmation.APPLIED

    def test_the_opposite_direction_is_a_different_request(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)

        assert switch.request({Layer.GESTURE}, asleep=False, now=2.0) is Confirmation.ARMED
        assert switch.asleep(Layer.GESTURE)

    def test_applying_leaves_nothing_armed(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)
        switch.request(ALL_LAYERS, asleep=True, now=1.0)

        assert switch.request(ALL_LAYERS, asleep=False, now=2.0) is Confirmation.ARMED
        assert switch.all_asleep


class TestDeterministicBothWays:
    """Never a toggle, so a doubled press is a no-op rather than a reversal."""

    def test_sleeping_what_is_already_asleep_does_nothing(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)

        assert switch.request({Layer.GESTURE}, asleep=True, now=2.0) is Confirmation.IDLE
        assert switch.asleep(Layer.GESTURE)

    def test_waking_what_is_already_awake_does_nothing(self):
        switch = SleepSwitch()

        assert switch.request(ALL_LAYERS, asleep=False, now=0.0) is Confirmation.IDLE

    def test_a_no_op_does_not_clear_a_pending_request(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)
        switch.request({Layer.VOICE}, asleep=True, now=2.0)

        assert switch.request({Layer.GESTURE}, asleep=True, now=3.0) is Confirmation.IDLE
        assert switch.request({Layer.VOICE}, asleep=True, now=4.0) is Confirmation.APPLIED

    def test_a_partly_asleep_set_is_still_a_change(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=2.0) is Confirmation.ARMED
        assert switch.request(ALL_LAYERS, asleep=True, now=3.0) is Confirmation.APPLIED
        assert switch.all_asleep

    def test_waking_one_layer_leaves_the_others_asleep(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)
        switch.request(ALL_LAYERS, asleep=True, now=1.0)

        switch.request({Layer.PEDAL}, asleep=False, now=2.0)
        switch.request({Layer.PEDAL}, asleep=False, now=3.0)

        assert not switch.asleep(Layer.PEDAL)
        assert switch.asleep(Layer.VOICE)
        assert not switch.all_asleep


class TestWhatADisplayIsTold:
    def test_an_armed_request_is_a_line_to_put_on_screen(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        assert switch.view(now=1.0).prompt == "sleep? again to confirm"

    def test_one_layer_is_named_in_the_prompt(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)

        assert switch.view(now=0.5).prompt == "sleep gesture? again to confirm"

    def test_the_way_back_is_named_too(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)
        switch.request({Layer.GESTURE}, asleep=False, now=2.0)

        assert switch.view(now=2.5).prompt == "wake gesture? again to confirm"

    def test_an_expired_request_stops_being_shown(self):
        switch = SleepSwitch(confirm_seconds=5.0)
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        assert switch.view(now=6.0).prompt is None

    def test_an_expired_request_is_forgotten_rather_than_merely_hidden(self):
        switch = SleepSwitch(confirm_seconds=5.0)
        switch.request(ALL_LAYERS, asleep=True, now=0.0)
        switch.view(now=6.0)

        assert switch.request(ALL_LAYERS, asleep=True, now=6.1) is Confirmation.ARMED

    def test_nothing_asleep_and_nothing_asked_is_an_empty_view(self):
        assert SleepSwitch().view(now=0.0) == SleepView()

    def test_the_view_says_which_layers(self):
        switch = SleepSwitch()
        switch.request({Layer.GESTURE}, asleep=True, now=0.0)
        switch.request({Layer.GESTURE}, asleep=True, now=1.0)

        assert switch.view(now=1.0).summary() == "asleep: gesture"
        assert switch.view(now=1.0).dozing

    def test_everything_asleep_needs_no_list(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)
        switch.request(ALL_LAYERS, asleep=True, now=1.0)

        assert switch.view(now=1.0).summary() == "asleep"

    def test_awake_says_so(self):
        assert SleepSwitch().view(now=0.0).summary() == "awake"
        assert not SleepSwitch().view(now=0.0).dozing


class TestWhoeverSleptItWakesIt:
    """The rule that makes a sleep definitive: a wake from elsewhere is refused."""

    def slept(self, by: Layer | None) -> SleepSwitch:
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0, by=by)
        switch.request(ALL_LAYERS, asleep=True, now=1.0, by=by)
        return switch

    def test_the_owner_may_wake_it(self):
        switch = self.slept(Layer.PEDAL)

        assert switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.PEDAL) is (
            Confirmation.ARMED
        )
        assert switch.request(ALL_LAYERS, asleep=False, now=3.0, by=Layer.PEDAL) is (
            Confirmation.APPLIED
        )

    def test_anybody_else_is_refused(self):
        switch = self.slept(Layer.PEDAL)

        assert switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE) is (
            Confirmation.REFUSED
        )
        assert switch.all_asleep

    def test_a_refusal_does_not_arm_anything(self):
        switch = self.slept(Layer.PEDAL)
        switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE)

        assert switch.request(ALL_LAYERS, asleep=False, now=2.5, by=Layer.VOICE) is (
            Confirmation.REFUSED
        )
        assert switch.all_asleep

    def test_a_sleep_nobody_owns_may_be_woken_by_anybody(self):
        switch = self.slept(None)

        assert switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE) is (
            Confirmation.ARMED
        )

    def test_the_owner_is_whoever_confirmed_it(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0, by=Layer.GESTURE)
        switch.request(ALL_LAYERS, asleep=True, now=1.0, by=Layer.PEDAL)

        assert switch.view(1.0).owners == frozenset({Layer.PEDAL})

    def test_ownership_is_per_layer(self):
        switch = SleepSwitch()
        for now in (0.0, 1.0):
            switch.request({Layer.GESTURE}, asleep=True, now=now, by=Layer.GESTURE)
        for now in (2.0, 3.0):
            switch.request({Layer.PEDAL}, asleep=True, now=now, by=Layer.VOICE)

        assert switch.request({Layer.GESTURE}, asleep=False, now=4.0, by=Layer.GESTURE) is (
            Confirmation.ARMED
        )
        assert switch.request({Layer.PEDAL}, asleep=False, now=5.0, by=Layer.GESTURE) is (
            Confirmation.REFUSED
        )

    def test_waking_a_set_is_refused_if_any_of_it_is_somebody_else_s(self):
        switch = SleepSwitch()
        for now in (0.0, 1.0):
            switch.request({Layer.GESTURE}, asleep=True, now=now, by=Layer.GESTURE)
        for now in (2.0, 3.0):
            switch.request({Layer.PEDAL}, asleep=True, now=now, by=Layer.PEDAL)

        assert switch.request(ALL_LAYERS, asleep=False, now=4.0, by=Layer.GESTURE) is (
            Confirmation.REFUSED
        )

    def test_a_refusal_is_said_out_loud(self):
        switch = self.slept(Layer.PEDAL)
        switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE)

        assert switch.view(2.0).notice == "refused: only pedal wakes this"

    def test_the_notice_does_not_outlive_the_sleep_it_described(self):
        switch = self.slept(Layer.PEDAL)
        switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE)
        for now in (3.0, 4.0):
            switch.request(ALL_LAYERS, asleep=False, now=now, by=Layer.PEDAL)

        assert switch.view(4.0) == SleepView()

    def test_and_the_notice_expires(self):
        switch = self.slept(Layer.PEDAL)
        switch.request(ALL_LAYERS, asleep=False, now=2.0, by=Layer.VOICE)

        assert switch.view(8.0).notice is None

    def test_the_summary_says_which_mechanism_to_use(self):
        assert self.slept(Layer.PEDAL).view(1.0).summary() == "asleep (wake with pedal)"

    def test_a_sleep_nobody_owns_names_nobody(self):
        assert self.slept(None).view(1.0).summary() == "asleep"


class TestTheWayBack:
    def test_it_answers_to_nobody(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0, by=Layer.PEDAL)
        switch.request(ALL_LAYERS, asleep=True, now=1.0, by=Layer.PEDAL)

        switch.wake_everything()

        assert switch.view(1.0) == SleepView()

    def test_waking_everything_needs_no_confirmation(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)
        switch.request(ALL_LAYERS, asleep=True, now=1.0)

        switch.wake_everything()

        assert not switch.view(now=1.0).dozing

    def test_it_forgets_a_pending_request_too(self):
        switch = SleepSwitch()
        switch.request(ALL_LAYERS, asleep=True, now=0.0)

        switch.wake_everything()

        assert switch.view(now=0.5).prompt is None


class TestNamingTheLayers:
    def test_an_empty_argument_means_every_mechanism(self):
        assert parse_layers("") == ALL_LAYERS
        assert parse_layers("   ") == ALL_LAYERS

    def test_one_layer(self):
        assert parse_layers("gesture") == {Layer.GESTURE}

    def test_several(self):
        assert parse_layers("gesture, pedal") == {Layer.GESTURE, Layer.PEDAL}

    def test_case_and_spacing_do_not_matter(self):
        assert parse_layers(" Gesture ,PEDAL") == {Layer.GESTURE, Layer.PEDAL}

    def test_a_name_that_is_not_a_mechanism_says_which_are(self):
        try:
            parse_layers("mouth")
        except ValueError as exc:
            assert "mouth" in str(exc)
            assert "voice, gesture, pedal" in str(exc)
        else:
            raise AssertionError("a bad layer name must raise")
