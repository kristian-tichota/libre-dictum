import json

import pytest

from libre_dictum.tracking.expressions import REST, Baseline, FaceFrame
from libre_dictum.tracking.face_tuning import (
    MIN_MARGIN,
    WINDOWS,
    diagnose,
    face_definitions_of,
    matrix_features,
    propose,
    render,
    render_baseline,
    render_passes,
    render_proposal,
    score,
    summarise,
    windows_for,
)
from libre_dictum.tracking.gestures import FACE_FEATURES, HOLD_KEY

STEP = 0.02


def face(**over):
    return dict.fromkeys(FACE_FEATURES, 0.0) | over


def take(label, number, values, *, feature="mouthSmileLeft", start=0.0, step=STEP, **extra):
    """One take of one label, as a run of frames driving one feature."""
    return [
        FaceFrame(
            at=start + index * step,
            scores=face(**{feature: value}, **extra),
            label=label,
            take=number,
        )
        for index, value in enumerate(values)
    ]


def held(value, seconds, *, step=STEP):
    return [value] * max(1, round(seconds / step))


def pulsed(value, seconds, *, on=0.25, step=STEP):
    """A feature that reaches value in bursts rather than holding it."""
    cycle = held(value, on, step=step) + held(0.0, on, step=step)
    return (cycle * (1 + int(seconds / (2 * on))))[: max(1, round(seconds / step))]


def pair(label, number, left, right, *, start=0.0, step=STEP):
    """One take driving both mouth corners, so a max condition has something to see."""
    return [
        FaceFrame(
            at=start + index * step,
            scores=face(mouthSmileLeft=one, mouthSmileRight=other),
            label=label,
            take=number,
        )
        for index, (one, other) in enumerate(zip(left, right, strict=True))
    ]


def recording(*groups):
    """Frames for several takes, laid end to end on one clock."""
    frames, at = [], 0.0
    for label, number, values in groups:
        frames += take(label, number, values, start=at)
        at = frames[-1].at + STEP * 20
    return frames


SMILE = {
    "smile": {
        "mouthSmileLeft": {"min": 0.5, "release": 0.2},
    }
}


class TestScoringARecording:
    def test_a_blendshape_arrives_as_the_score_a_condition_is_written_against(self):
        (scored,) = score([FaceFrame(at=1.0, scores=face(jawOpen=0.4), label="x", take=2)])
        assert scored.scores["jawOpen"] == 0.4
        assert (scored.at, scored.label, scored.take) == (1.0, "x", 2)
        assert scored.measurements == ()

    def test_the_candidate_s_own_dwell_is_always_measured(self):
        assert 0.15 in windows_for({"smile": {HOLD_KEY: 150}})
        assert set(WINDOWS) <= set(windows_for({}))


class TestReportOrder:
    def test_the_passes_come_last_however_the_session_recorded_them(self):
        frames = recording(
            (REST, 1, held(0.05, 1.0)),
            ("smile", 1, held(0.9, 1.0)),
            ("talking", 1, held(0.3, 1.0)),
        )
        assert summarise(score(frames)).labels == ("smile", REST, "talking")


class TestPerPassDiagnostics:
    def test_it_reports_the_takes_their_lengths_and_the_head_that_moved(self):
        frames = recording(("smile", 1, held(0.9, 1.0)), ("smile", 2, held(0.9, 0.5)))
        frames = [f if f.take != 2 else FaceFrame(**{**vars_of(f), "yaw": 20.0}) for f in frames]
        info = diagnose(frames)["smile"]
        assert info.takes == 2
        assert info.shortest == pytest.approx(0.5, abs=0.05)
        assert info.longest == pytest.approx(1.0, abs=0.05)
        assert info.yaw_span == pytest.approx(20.0)
        assert info.lost == 0

    def test_a_frame_the_landmarker_lost_the_face_in_is_counted(self):
        frames = take("smile", 1, held(0.9, 0.5)) + [
            FaceFrame(at=9.0, label="smile", take=1) for _ in range(3)
        ]
        assert diagnose(frames)["smile"].lost == 3

    def test_a_frame_outside_a_take_belongs_to_no_pass(self):
        assert diagnose(take("smile", 0, held(0.9, 1.0))) == {}


def vars_of(frame):
    return {
        "at": frame.at,
        "scores": frame.scores,
        "yaw": frame.yaw,
        "pitch": frame.pitch,
        "label": frame.label,
        "take": frame.take,
        "resumed": frame.resumed,
    }


class TestTheMatrixRows:
    def test_a_feature_the_candidate_names_keeps_its_row_whatever_it_scores(self):
        frames = recording((REST, 1, held(0.0, 1.0)), ("smile", 1, held(0.0, 1.0)))
        assert "mouthSmileLeft" in matrix_features(summarise(score(frames)), SMILE)

    def test_it_does_not_hand_a_terminal_fifty_two_rows(self):
        frames = recording((REST, 1, held(0.1, 1.0)), ("smile", 1, held(0.9, 1.0)))
        chosen = matrix_features(summarise(score(frames)), {})
        assert 0 < len(chosen) < len(FACE_FEATURES)


class TestProposingAThreshold:
    def test_it_sits_between_the_worst_take_and_the_strongest_rival(self):
        frames = recording(
            ("smile", 1, held(0.90, 1.5)),
            ("smile", 2, held(0.86, 1.5)),
            ("amused", 1, held(0.40, 2.0)),
            ("talking", 1, held(0.15, 2.0)),
        )
        summary = summarise(score(frames))
        proposal = propose(summary, SMILE, Baseline(levels={"mouthSmileLeft": 0.08}))
        (bound,) = proposal.bounds
        assert bound.own == pytest.approx(0.86, abs=0.01)
        assert (bound.rival, bound.rival_value) == ("amused", pytest.approx(0.40, abs=0.01))
        assert 0.40 < bound.value < 0.86

    def test_it_leans_towards_the_rival_because_a_misfire_costs_more(self):
        frames = recording(("smile", 1, held(1.0, 1.5)), ("amused", 1, held(0.0, 2.0)))
        proposal = propose(summarise(score(frames)), SMILE, Baseline())
        (bound,) = proposal.bounds
        assert bound.value > 0.5

    def test_the_release_is_measured_up_from_rest_rather_than_down_from_the_threshold(self):
        frames = recording(("smile", 1, held(0.9, 1.5)), ("amused", 1, held(0.1, 2.0)))
        (bound,) = propose(summarise(score(frames)), SMILE, Baseline()).bounds
        assert 0.0 < bound.release < bound.value
        assert bound.release < 0.5 * bound.value

    def test_the_recording_is_scored_as_the_rise_from_the_measured_resting_level(self):
        frames = recording(("smile", 1, held(0.9, 1.5)), ("amused", 1, held(0.1, 2.0)))
        raw = propose(summarise(score(frames)), SMILE, Baseline())
        rested = Baseline(levels={"mouthSmileLeft": 0.4})
        against_rest = propose(summarise(score(frames, rested)), SMILE, rested)
        assert against_rest.bounds[0].value < raw.bounds[0].value
        assert against_rest.bounds[0].own < raw.bounds[0].own

    def test_the_baseline_is_carried_into_the_proposal_beside_the_thresholds(self):
        frames = recording(("smile", 1, held(0.9, 1.5)), ("amused", 1, held(0.1, 2.0)))
        measured = Baseline(levels={"mouthSmileLeft": 0.18, "browInnerUp": 0.001})
        proposal = propose(summarise(score(frames, measured)), SMILE, measured)
        assert proposal.baseline == {"mouthSmileLeft": 0.18}
        rendered = render_proposal(proposal)
        assert '"ht_face_baseline"' in rendered and '"mouthSmileLeft": 0.18' in rendered
        assert '"ht_custom_gestures"' in rendered

    def test_a_max_condition_is_measured_against_the_rival_it_exists_to_exclude(self):
        definitions = {"left_smirk": {"mouthSmileRight": {"max": 0.3}}}
        frames = pair(
            "left_smirk",
            1,
            held(0.8, 1.6),
            held(0.05, 0.6) + held(0.30, 0.4) + held(0.05, 0.6),
        )
        frames += pair("smile", 1, held(0.9, 1.5), held(0.9, 1.5), start=5.0)
        proposal = propose(summarise(score(frames)), definitions, Baseline())
        (bound,) = proposal.bounds
        assert bound.low
        assert bound.rival == "smile"
        assert bound.own == pytest.approx(0.05, abs=0.01)
        assert 0.05 < bound.value < 0.9
        assert bound.release > bound.value


class TestChoosingTheDwell:
    def test_the_shortest_dwell_that_separates_is_the_one_proposed(self):
        frames = recording(("smile", 1, held(0.9, 2.0)), ("amused", 1, held(0.1, 2.0)))
        proposal = propose(summarise(score(frames)), SMILE, Baseline())
        assert proposal.definitions["smile"][HOLD_KEY] == round(WINDOWS[0] * 1000)

    def test_a_longer_dwell_is_chosen_when_a_short_one_cannot_separate(self):
        frames = recording(
            ("smile", 1, held(0.9, 2.0)),
            ("amused", 1, pulsed(0.95, 4.0, on=0.25)),
        )
        proposal = propose(summarise(score(frames)), SMILE, Baseline())
        assert proposal.definitions["smile"][HOLD_KEY] > round(WINDOWS[0] * 1000)

    def test_a_condition_with_no_room_at_any_dwell_is_left_exactly_as_it_was(self):
        frames = recording(("smile", 1, held(0.6, 2.0)), ("amused", 1, held(0.9, 4.0)))
        proposal = propose(summarise(score(frames)), SMILE, Baseline())
        (bound,) = proposal.bounds
        assert bound.value is None
        assert bound.margin < MIN_MARGIN
        assert proposal.definitions["smile"]["mouthSmileLeft"] == {"min": 0.5, "release": 0.2}
        assert any("no room at any dwell" in why for _, why in proposal.untouched)

    def test_a_dwell_nothing_measured_is_never_invented(self):
        frames = recording(("smile", 1, held(0.6, 2.0)), ("amused", 1, held(0.9, 4.0)))
        with_hold = {"smile": {HOLD_KEY: 250, **SMILE["smile"]}}
        summary = summarise(score(frames), windows=windows_for(with_hold))
        assert propose(summary, with_hold, Baseline()).definitions["smile"][HOLD_KEY] == 250
        assert (
            HOLD_KEY
            not in propose(summarise(score(frames)), SMILE, Baseline()).definitions["smile"]
        )

    def test_a_gesture_the_recording_never_asked_for_comes_back_verbatim(self):
        frames = recording(("smile", 1, held(0.9, 1.0)))
        proposal = propose(
            summarise(score(frames)), {"blink": {"eyeBlinkLeft": {"min": 0.7}}}, Baseline()
        )
        assert proposal.definitions["blink"] == {"eyeBlinkLeft": {"min": 0.7}}
        assert proposal.untouched == (("blink", "the recording holds no takes under this name"),)


class TestTheRenderedReport:
    def test_the_proposal_is_json_a_config_file_would_accept(self):
        frames = recording(("smile", 1, held(0.9, 2.0)), ("amused", 1, held(0.1, 2.0)))
        summary = summarise(score(frames))
        text = render_proposal(propose(summary, SMILE, Baseline()))
        block = text.split("```json")[1].split("```")[0]
        assert "smile" in json.loads(block)["ht_custom_gestures"]

    def test_a_refused_condition_reads_as_nothing_rather_than_as_a_number(self):
        frames = recording(("smile", 1, held(0.6, 2.0)), ("amused", 1, held(0.9, 4.0)))
        text = render_proposal(propose(summarise(score(frames)), SMILE, Baseline()))
        assert "  --    --" in text

    def test_the_baseline_names_what_is_not_quiet_and_says_how_it_was_measured(self):
        text = render_baseline(Baseline(levels={"mouthClose": 0.31, "jawOpen": 0.0}, frames=900))
        assert "mouthClose" in text and "0.310" in text
        assert "jawOpen" not in text
        assert "900" in text

    def test_a_very_still_face_is_said_so_rather_than_left_blank(self):
        assert "still face" in render_baseline(Baseline(levels={"jawOpen": 0.0}, frames=900))

    def test_the_takes_table_tells_a_performance_from_an_activity(self):
        frames = recording(("smile", 1, held(0.9, 1.0)), ("talking", 1, held(0.3, 1.0)))
        text = render_passes(summarise(score(frames)), diagnose(frames))
        assert "smile" in text and "held" in text
        assert "talking" in text and "pass" in text

    def test_the_whole_report_holds_every_section(self):
        frames = recording(
            (REST, 1, held(0.05, 1.0)),
            ("smile", 1, held(0.9, 2.0)),
            ("amused", 1, held(0.1, 2.0)),
        )
        summary = summarise(score(frames))
        baseline = Baseline(levels={"mouthSmileLeft": 0.05}, frames=50)
        text = render(
            summary,
            SMILE,
            diagnose(frames),
            baseline,
            proposal=propose(summary, SMILE, baseline),
        )
        for heading in ("## takes", "## baseline", "## matrix", "## per gesture", "## proposed"):
            assert heading in text

    def test_a_pass_is_not_rendered_as_a_gesture_it_never_was(self):
        frames = recording(("smile", 1, held(0.9, 2.0)), ("talking", 1, held(0.3, 2.0)))
        summary = summarise(score(frames))
        text = render(summary, SMILE, diagnose(frames), Baseline())
        assert "### smile" in text
        assert "### talking" not in text

    def test_the_matrix_does_not_explain_a_hand_abbreviation_a_face_has_not_got(self):
        frames = recording(("smile", 1, held(0.9, 2.0)), ("amused", 1, held(0.1, 2.0)))
        text = render(summarise(score(frames)), SMILE, {}, Baseline())
        assert "leftHand" not in text


class TestWhichGesturesCanBeReplayed:
    def test_a_hand_gesture_is_dropped_because_this_recording_cannot_judge_it(self):
        payload = {
            "ht_custom_gestures": {
                "smile": {"mouthSmileLeft": {"min": 0.8}},
                "left_fist": {"leftHandFist": {"min": 0.5}},
                "mixed": {"jawOpen": {"min": 0.5}, "leftHandFist": {"min": 0.5}},
            }
        }
        assert set(face_definitions_of(payload)) == {"smile"}

    def test_a_hold_is_not_mistaken_for_a_feature(self):
        payload = {"ht_custom_gestures": {"smile": {HOLD_KEY: 400, "jawOpen": {"min": 0.5}}}}
        assert set(face_definitions_of(payload)) == {"smile"}

    def test_a_config_with_no_gestures_at_all_is_empty_rather_than_an_error(self):
        assert face_definitions_of({}) == {}
        assert face_definitions_of({"ht_custom_gestures": "nonsense"}) == {}
