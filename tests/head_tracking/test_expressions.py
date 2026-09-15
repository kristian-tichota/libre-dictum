import pytest

from libre_dictum.tracking.expressions import (
    ACTIVE_MARGIN,
    DEFAULT_SCRIPT,
    GATES,
    NOTE_FLOOR,
    PASSES,
    REST,
    SKIP_AFTER,
    Baseline,
    FaceFrame,
    Pass,
    Rest,
    attempting,
    build_plan,
    decode,
    encode,
    gate,
    instructions,
    normalised,
    pass_labels,
    read_frames,
    recording_kind,
    rejection,
    segmenter,
    still_owed,
)
from libre_dictum.tracking.gestures import FACE_FEATURES
from libre_dictum.tracking.takes import Prompt

STEP = 0.02


def face(**over):
    """A full set of blendshapes, zero except where named."""
    return dict.fromkeys(FACE_FEATURES, 0.0) | over


def frames(values, *, feature="mouthSmileLeft", step=STEP, label="smile", take=1):
    """One feature over time, as recorded frames."""
    return [
        FaceFrame(at=index * step, scores=face(**{feature: value}), label=label, take=take)
        for index, value in enumerate(values)
    ]


class TestTheRecordingFormat:
    def test_a_frame_survives_a_round_trip(self):
        original = FaceFrame(
            at=1.25,
            scores=face(jawOpen=0.5, mouthPucker=0.25),
            yaw=-12.5,
            pitch=3.0,
            label="pucker",
            take=4,
            resumed=True,
        )
        restored = decode(encode(original))
        assert restored.at == 1.25
        assert restored.scores["jawOpen"] == 0.5
        assert restored.scores["mouthPucker"] == 0.25
        assert (restored.yaw, restored.pitch) == (-12.5, 3.0)
        assert (restored.label, restored.take, restored.resumed) == ("pucker", 4, True)

    def test_every_blendshape_comes_back_under_its_own_name(self):
        original = FaceFrame(
            at=0.0, scores={name: index / 100 for index, name in enumerate(FACE_FEATURES)}
        )
        assert decode(encode(original)).scores == pytest.approx(original.scores)

    def test_a_frame_with_no_face_is_absent_rather_than_zero(self):
        restored = decode(encode(FaceFrame(at=2.0, label="smile")))
        assert restored.scores == {}
        assert not restored.present

    def test_a_recording_from_a_different_model_is_refused(self):
        with pytest.raises(ValueError, match="different face model"):
            decode('{"t":0.0,"b":[0.1,0.2,0.3]}')

    def test_a_line_that_is_not_a_frame_is_refused(self):
        with pytest.raises(ValueError, match="not a recorded frame"):
            decode('{"nope":1}')

    def test_blank_lines_are_skipped_and_a_bad_one_still_raises(self):
        good = encode(FaceFrame(at=0.0, scores=face()))
        assert len(list(read_frames([good, "", "  \n"]))) == 1
        with pytest.raises(ValueError):
            list(read_frames([good, "{"]))


class TestTellingTheTwoRecordingsApart:
    def test_a_face_recording_says_so(self):
        assert recording_kind({"kind": "face"}) == "face"

    def test_a_recording_with_no_kind_at_all_is_a_hand_one(self):
        assert recording_kind({"schema": 1}) == "hands"


class TestTheIdleBaseline:
    def test_it_reads_the_level_a_feature_sits_at_rather_than_its_peak(self):
        quiet = [0.1] * 100 + [0.9]
        baseline = Baseline.measure(frames(quiet, label=REST))
        assert baseline.idle("mouthSmileLeft") == pytest.approx(0.1)

    def test_a_face_that_was_not_in_shot_does_not_count_as_resting(self):
        absent = [FaceFrame(at=index * STEP, label=REST, take=1) for index in range(50)]
        baseline = Baseline.measure(absent + frames([0.2] * 50, label=REST))
        assert baseline.frames == 50
        assert baseline.idle("mouthSmileLeft") == pytest.approx(0.2)

    def test_a_feature_the_rest_pass_never_saw_idles_at_zero(self):
        assert Baseline().idle("cheekPuff") == 0.0
        assert not Baseline().measured

    def test_only_the_features_that_are_not_quiet_are_worth_reporting(self):
        baseline = Baseline.measure(
            [FaceFrame(at=0.0, scores=face(mouthClose=0.3, browDownLeft=0.15, jawOpen=0.001))]
        )
        assert baseline.moving() == (("mouthClose", 0.3), ("browDownLeft", 0.15))


class TestNormalisingAgainstThisFaceSRest:
    def test_a_score_becomes_its_rise_from_that_feature_s_own_resting_level(self):
        levels = {"eyeBlinkLeft": 0.305, "eyeBlinkRight": 0.394}
        rise = normalised({"eyeBlinkLeft": 0.55, "eyeBlinkRight": 0.55}, levels)
        assert rise["eyeBlinkLeft"] == pytest.approx(0.245 / 0.695, abs=5e-3)
        assert rise["eyeBlinkRight"] == pytest.approx(0.156 / 0.606, abs=5e-3)
        assert rise["eyeBlinkLeft"] > rise["eyeBlinkRight"]

    def test_a_feature_at_rest_reads_as_zero_and_one_below_rest_does_not_go_negative(self):
        rise = normalised(
            {"browDownLeft": 0.0, "mouthPucker": 0.334, "browOuterUpLeft": 0.0},
            {"browDownLeft": 0.403, "mouthPucker": 0.334},
        )
        assert rise["browDownLeft"] == 0.0
        assert rise["mouthPucker"] == 0.0
        assert rise["browOuterUpLeft"] == 0.0

    def test_it_is_monotone_per_feature_which_is_what_makes_a_migration_exact(self):
        levels = {"mouthPucker": 0.334}
        raw_threshold = 0.80
        migrated = (raw_threshold - 0.334) / (1 - 0.334)
        for raw in (x / 100 for x in range(101)):
            fired_raw = raw >= raw_threshold
            fired_norm = normalised({"mouthPucker": raw}, levels)["mouthPucker"] >= migrated
            assert fired_raw == fired_norm, raw

    def test_an_unmeasured_face_leaves_every_score_exactly_as_it_was(self):
        scores = {"mouthSmileLeft": 0.42, "handFist": 0.9}
        assert normalised(scores, {}) == scores

    def test_a_score_with_no_measured_level_passes_through_untouched(self):
        rise = normalised({"handFist": 0.9, "mouthPucker": 0.8}, {"mouthPucker": 0.334})
        assert rise["handFist"] == 0.9
        assert rise["mouthPucker"] < 0.8

    def test_a_feature_that_idles_at_its_ceiling_says_nothing_rather_than_dividing_by_zero(self):
        assert normalised({"mouthClose": 1.0}, {"mouthClose": 1.0}) == {"mouthClose": 0.0}


class TestTheGate:
    def test_it_opens_a_share_of_the_range_the_feature_has_left_above_its_idle_level(self):
        baseline = Baseline(levels={"eyeBlinkLeft": 0.2})
        (reading,) = gate(face(eyeBlinkLeft=0.35), baseline, ("eyeBlinkLeft",))
        assert reading.opens_at == pytest.approx(0.2 + ACTIVE_MARGIN * 0.8)
        assert not reading.open

        (reading,) = gate(face(eyeBlinkLeft=0.9), baseline, ("eyeBlinkLeft",))
        assert reading.open

    def test_a_feature_that_idles_higher_is_asked_for_proportionally_less(self):
        quiet = Baseline(levels={"eyeBlinkLeft": 0.05})
        busy = Baseline(levels={"eyeBlinkRight": 0.40})
        (low,) = gate(face(eyeBlinkLeft=0.5), quiet, ("eyeBlinkLeft",))
        (high,) = gate(face(eyeBlinkRight=0.5), busy, ("eyeBlinkRight",))
        assert high.opens_at - 0.40 < low.opens_at - 0.05

    def test_a_feature_that_already_idles_at_its_ceiling_asks_for_no_more_than_one(self):
        (reading,) = gate(
            face(mouthClose=1.0), Baseline(levels={"mouthClose": 1.0}), ("mouthClose",)
        )
        assert reading.opens_at == 1.0
        assert reading.open

    def test_a_frame_with_no_face_reads_as_nothing_rather_than_as_zero(self):
        (reading,) = gate({}, Baseline(), ("cheekPuff",))
        assert reading.score is None
        assert not reading.open

    def test_either_half_of_a_two_sided_expression_is_an_attempt(self):
        readings = gate(face(mouthSmileLeft=0.8), Baseline(), ("mouthSmileLeft", "mouthSmileRight"))
        assert attempting(readings)
        assert not attempting(gate(face(), Baseline(), ("mouthSmileLeft",)))

    def test_every_performance_in_the_script_has_a_gate(self):
        assert {label for label, _ in DEFAULT_SCRIPT} == set(GATES)

    def test_every_gate_watches_a_blendshape_the_model_actually_reports(self):
        named = {feature for features in GATES.values() for feature in features}
        assert named <= set(FACE_FEATURES)


class TestRestingAndGivingUp:
    def test_a_face_in_shot_and_doing_nothing_is_at_rest(self):
        rest = Rest()
        for index in range(10):
            rest.feed(index * 0.1, present=True, active=False)
        assert rest.held(0.9) == pytest.approx(0.9)

    def test_activity_ends_the_rest(self):
        rest = Rest()
        rest.feed(0.0, present=True, active=False)
        rest.feed(0.5, present=True, active=True)
        assert rest.held(0.5) == 0.0

    def test_a_face_that_has_left_is_not_a_face_at_rest(self):
        rest = Rest()
        rest.feed(0.0, present=True, active=False)
        rest.feed(1.0, present=False, active=False)
        assert rest.held(SKIP_AFTER) == 0.0

    def test_it_can_be_forgotten_after_a_span_was_thrown_away(self):
        rest = Rest(since=0.0)
        rest.reset()
        assert rest.held(100.0) == 0.0


class TestWhatIsSaidAboutASpanTooShortToKeep:
    def test_a_blink_is_discarded_in_silence(self):
        assert rejection(Prompt("left_wink", 1), NOTE_FLOOR / 2) == ""

    def test_a_real_attempt_that_was_too_quick_is_said_out_loud(self):
        note = rejection(Prompt("left_wink", 1), 0.4)
        assert "left_wink" in note and "0.4" in note


class TestTheSegmenter:
    def test_a_blink_length_span_is_below_the_minimum(self):
        spans = segmenter()
        closed = _feed(spans, [False] * 30 + [True] * 8 + [False] * 30)
        assert closed and not closed[0].kept

    def test_a_held_expression_is_kept(self):
        spans = segmenter()
        closed = _feed(spans, [False] * 30 + [True] * 60 + [False] * 30)
        assert closed and closed[0].kept


def _feed(spans, presence, *, step=STEP):
    return [
        event
        for index, present in enumerate(presence)
        if (event := spans.feed(index * step, present)) is not None
    ]


class TestThePlan:
    def test_the_rest_pass_comes_first_because_nothing_can_be_gated_without_it(self):
        plan = build_plan(["smile"], 2, passes=(Pass(REST, 20.0, "still"),))
        assert plan[0].label == REST
        assert plan[0].timed

    def test_the_performances_are_round_robin_and_the_other_passes_come_last(self):
        plan = build_plan(
            ["smile", "pucker"],
            2,
            passes=(Pass(REST, 20.0, "still"), Pass("talking", 60.0, "talk")),
        )
        assert [(prompt.label, prompt.take) for prompt in plan] == [
            (REST, 1),
            ("smile", 1),
            ("pucker", 1),
            ("smile", 2),
            ("pucker", 2),
            ("talking", 1),
        ]

    def test_a_resumed_session_asks_only_for_what_the_file_does_not_hold(self):
        plan = build_plan(["smile", "pucker"], 2, passes=())
        owed = still_owed(plan, [("smile", 1), ("pucker", 1)])
        assert [(prompt.label, prompt.take) for prompt in owed] == [("smile", 2), ("pucker", 2)]

    def test_a_performance_given_up_on_is_not_asked_for_again(self):
        plan = build_plan(["smile", "right_smirk"], 3, passes=())
        owed = still_owed(plan, [], skipped=["right_smirk"])
        assert {prompt.label for prompt in owed} == {"smile"}

    def test_the_passes_are_named_and_every_prompt_has_something_to_say(self):
        assert pass_labels()[0] == REST
        spoken = instructions()
        assert set(spoken) == {label for label, _ in DEFAULT_SCRIPT} | set(pass_labels())
        assert all(spoken.values())

    def test_the_talking_pass_is_in_there(self):
        assert "talking" in pass_labels()
        assert any(one.label == "talking" and one.seconds >= 30.0 for one in PASSES)
