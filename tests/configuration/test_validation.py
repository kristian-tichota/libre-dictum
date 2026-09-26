import pytest

from libre_dictum.errors import ConfigError
from libre_dictum.layers import Layer
from libre_dictum.tracking.hands import Calibration

MINIMAL = {"modes": {"root": {"type": "vosk", "path": "m"}}}


class TestRequiredFields:
    def test_a_vosk_mode_needs_a_model_path(self, settings_of):
        with pytest.raises(ConfigError, match="'root'.*path"):
            settings_of({"modes": {"root": {"type": "vosk"}}})

    def test_a_transformer_mode_needs_a_model_name(self, settings_of):
        with pytest.raises(ConfigError, match="'dictate'.*model_name"):
            settings_of({"modes": {"dictate": {"type": "transformer"}}})

    def test_an_unknown_mode_type_lists_the_known_ones(self, settings_of):
        with pytest.raises(ConfigError, match="unknown type 'kaldi'"):
            settings_of({"modes": {"root": {"type": "kaldi"}}})

    def test_a_config_with_no_runnable_mode_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="No runnable modes"):
            settings_of({"modes": {"::shared": {"commands": {}}}})

    def test_modes_must_be_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="'modes' must be"):
            settings_of({"modes": ["root"]})


class TestStartingMode:
    def test_an_unknown_starting_mode_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="'typo'.*not a defined mode"):
            settings_of({"starting_mode": "typo", "modes": {"root": {"type": "vosk", "path": "m"}}})

    def test_a_template_cannot_be_the_starting_mode(self, settings_of):
        with pytest.raises(ConfigError, match="import template"):
            settings_of(
                {
                    "starting_mode": "::shared",
                    "modes": {
                        "::shared": {"commands": {}},
                        "root": {"type": "vosk", "path": "m", "imports": ["::shared"]},
                    },
                }
            )


class TestRestInAVoskMode:
    def test_rest_is_rejected_in_a_command_mode(self, settings_of):
        with pytest.raises(ConfigError, match=r"\{rest\}"):
            settings_of(
                {"modes": {"root": {"type": "vosk", "path": "m", "commands": {"{rest}": "a"}}}}
            )

    def test_the_error_names_the_mode_and_the_command(self, settings_of):
        with pytest.raises(ConfigError, match="'say {rest}'.*'root'"):
            settings_of(
                {"modes": {"root": {"type": "vosk", "path": "m", "commands": {"say {rest}": "a"}}}}
            )

    def test_rest_is_fine_in_a_dictation_mode(self, settings_of):
        settings = settings_of(
            {
                "modes": {
                    "dictate": {
                        "type": "transformer",
                        "model_name": "whisper-turbo",
                        "commands": {"{rest}": "write({1})"},
                    }
                }
            }
        )
        assert settings.modes["dictate"].commands[0].pattern.has_rest


class TestIcons:
    @pytest.mark.parametrize("icon", [[0, 255], [0, 0, 0, 0], [0, 0, 300], ["a", "b", "c"]])
    def test_a_malformed_icon_is_rejected(self, settings_of, icon):
        with pytest.raises(ConfigError, match="Icon of mode 'root'"):
            settings_of({"modes": {"root": {"type": "vosk", "path": "m", "icon": icon}}})


class TestHeadTracking:
    def test_head_tracking_needs_a_model_path(self, settings_of):
        with pytest.raises(ConfigError, match="ht_model_path"):
            settings_of(
                {
                    "enable_head_tracking": True,
                    "modes": {"root": {"type": "vosk", "path": "m"}},
                }
            )

    def test_head_tracking_is_off_without_the_flag(self, settings_of):
        settings = settings_of(
            {"ht_model_path": "landmarker.task", "modes": {"root": {"type": "vosk", "path": "m"}}}
        )
        assert settings.head_tracking is None


class TestWhichCamera:
    """camera names one; the index it replaced is a guess a reboot invalidates."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def camera_of(self, settings_of, **keys):
        return settings_of({**self.ENABLED, **keys}).head_tracking.camera

    def test_naming_no_camera_means_finding_one(self, settings_of):
        assert self.camera_of(settings_of) is None

    @pytest.mark.parametrize(
        "spec", ["SOLOMON", "/dev/video0", "/dev/v4l/by-id/usb-a-camera-video-index0", 1]
    )
    def test_a_name_a_path_and_an_index_are_all_accepted(self, settings_of, spec):
        assert self.camera_of(settings_of, camera=spec) == spec

    def test_the_deprecated_index_still_works(self, settings_of):
        assert self.camera_of(settings_of, camera_index=1) == 1

    def test_the_deprecated_index_says_it_is_deprecated(self, settings_of, caplog):
        self.camera_of(settings_of, camera_index=1)

        assert "deprecated" in caplog.text

    def test_naming_a_camera_twice_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="both 'camera' and 'camera_index'"):
            self.camera_of(settings_of, camera="SOLOMON", camera_index=1)

    def test_an_index_that_is_not_a_number_is_rejected_at_load(self, settings_of):
        with pytest.raises(ConfigError, match="whole number"):
            self.camera_of(settings_of, camera_index="first")

    def test_an_empty_name_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="delete the key"):
            self.camera_of(settings_of, camera="   ")


class TestReservedPhrases:
    """A phrase dispatch handles itself can never also be a mode or a command."""

    def test_a_mode_named_like_the_panic_command_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="panic_command"):
            settings_of(
                {
                    "panic_command": "release",
                    "modes": {"release": {"type": "vosk", "path": "m"}},
                }
            )

    def test_a_command_shadowed_by_the_reload_phrase_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="'reload config'.*reload_command"):
            settings_of(
                {
                    "modes": {
                        "root": {
                            "type": "vosk",
                            "path": "m",
                            "commands": {"reload config": "f5"},
                        }
                    }
                }
            )

    def test_a_command_shadowed_by_the_panic_phrase_names_the_mode(self, settings_of):
        with pytest.raises(ConfigError, match="'root'"):
            settings_of(
                {
                    "modes": {
                        "root": {
                            "type": "vosk",
                            "path": "m",
                            "commands": {"Release Everything!": "esc"},
                        }
                    }
                }
            )

    def test_a_command_shadowed_by_the_previous_mode_phrase_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="previous_mode_keyword"):
            settings_of(
                {
                    "modes": {
                        "root": {
                            "type": "vosk",
                            "path": "m",
                            "commands": {"previous mode": "esc"},
                        }
                    }
                }
            )

    def test_the_collision_disappears_when_the_phrase_is_switched_off(self, settings_of):
        settings = settings_of(
            {
                "panic_command": None,
                "modes": {
                    "root": {
                        "type": "vosk",
                        "path": "m",
                        "commands": {"release everything": "esc"},
                    }
                },
            }
        )
        assert settings.modes["root"].commands


class TestResponses:
    """A response that could never run is a load error, not a silent no-op."""

    def mode_with(self, response, **extra):
        return {
            "modes": {
                "root": {
                    "type": "vosk",
                    "path": "m",
                    "commands": {"do it": response},
                    **extra,
                }
            }
        }

    def test_an_unknown_key_inside_hold_names_the_command_and_the_key(self, settings_of):
        with pytest.raises(ConfigError, match="'do it'.*'root'.*bogus"):
            settings_of(self.mode_with("hold(bogus)"))

    @pytest.mark.parametrize(
        "response",
        ["ctrl + c", "hold(ctrl)", "release(ctrl)", "toggle(shift)", "left_mouse", "3(pagedown)"],
    )
    def test_a_runnable_response_is_accepted(self, settings_of, response):
        settings_of(self.mode_with(response))

    @pytest.mark.parametrize(
        "response", ["hold(bogus)", "release(bogus)", "toggle(bogus)", "ctrl + nonsense"]
    )
    def test_an_unrunnable_response_is_rejected(self, settings_of, response):
        with pytest.raises(ConfigError):
            settings_of(self.mode_with(response))

    def test_a_literal_type_names_the_character_that_has_no_key(self, settings_of):
        with pytest.raises(ConfigError, match="'do it'.*'root'.*\u597d"):
            settings_of(self.mode_with("type(hi \u597d)"))

    @pytest.mark.parametrize("response", ["type(Hello, World!)", "type(~/.config)", "type(a)"])
    def test_typeable_text_is_accepted(self, settings_of, response):
        settings_of(self.mode_with(response))

    def test_punctuation_a_model_writes_is_accepted_because_it_is_folded(self, settings_of):
        settings_of(self.mode_with("type(\u201csmart\u201d \u2014 caf\u00e9\u2026)"))

    def test_a_dictated_type_is_left_for_execution(self, settings_of):
        settings_of(self.mode_with("type({1})"))

    def test_a_response_is_validated_after_its_aliases(self, settings_of):
        with pytest.raises(ConfigError, match="bogus"):
            settings_of(
                self.mode_with("shout", aliases={"^shout$": "hold(shift) + bogus"}),
            )

    def test_a_placeholder_is_left_for_execution_time(self, settings_of):
        settings_of(
            {"modes": {"root": {"type": "vosk", "path": "m", "commands": {"press {any}": "{1}"}}}}
        )

    def test_a_group_behind_a_placeholder_count_is_still_checked(self, settings_of):
        with pytest.raises(ConfigError, match="nonsense"):
            settings_of(self.mode_with("{1=1}(nonsense)"))

    def test_a_placeholder_count_over_a_chord_is_accepted(self, settings_of):
        settings_of(self.mode_with("{1}(ctrl + tab)"))

    def test_a_non_numeric_repeat_count_explains_itself(self, settings_of):
        with pytest.raises(ConfigError, match="repeat count must be a number"):
            settings_of(self.mode_with("two(down)"))

    def test_zero_is_rejected_as_a_placeholder(self, settings_of):
        with pytest.raises(ConfigError, match=r"numbered from 1"):
            settings_of(self.mode_with("{0}"))

    def test_a_gesture_action_is_validated(self, settings_of):
        with pytest.raises(ConfigError, match="Gesture 'blink' on press.*bogus"):
            settings_of(
                {
                    "enable_head_tracking": True,
                    "ht_model_path": "landmarker.task",
                    "modes": {
                        "root": {"type": "vosk", "path": "m", "gestures": {"blink": "bogus"}}
                    },
                }
            )

    def test_an_enter_command_is_validated(self, settings_of):
        with pytest.raises(ConfigError, match="enter_command.*bogus"):
            settings_of(
                {"modes": {"root": {"type": "vosk", "path": "m", "enter_command": "bogus"}}}
            )

    def test_a_mode_switch_to_an_unknown_mode_is_rejected(self, settings_of):
        with pytest.raises(ConfigError, match="nowhere"):
            settings_of(self.mode_with("mode(nowhere)"))

    def test_a_mode_switch_to_a_known_mode_is_accepted(self, settings_of):
        settings_of(self.mode_with("mode(Root)"))

    def test_a_mode_switch_to_the_previous_keyword_is_accepted(self, settings_of):
        settings_of(self.mode_with("mode(previous mode)"))


class TestGestureNames:
    def test_an_undefined_gesture_lists_what_is_defined(self, settings_of):
        with pytest.raises(ConfigError, match="eyebrow_raise.*Defined gestures: blink"):
            settings_of(
                {
                    "enable_head_tracking": True,
                    "ht_model_path": "landmarker.task",
                    "ht_custom_gestures": {"blink": {"eyeBlinkLeft": {"min": 0.5}}},
                    "modes": {
                        "root": {"type": "vosk", "path": "m", "gestures": {"eyebrow_raise": "esc"}}
                    },
                }
            )

    def test_a_shipped_gesture_is_accepted(self, settings_of):
        settings_of(
            {
                "enable_head_tracking": True,
                "ht_model_path": "landmarker.task",
                "modes": {"root": {"type": "vosk", "path": "m", "gestures": {"pucker": "esc"}}},
            }
        )

    def test_gestures_are_not_checked_without_head_tracking(self, settings_of):
        settings_of({"modes": {"root": {"type": "vosk", "path": "m", "gestures": {"nope": "esc"}}}})


class TestGestureBindings:
    """A gesture binds both edges, exactly as a pedal does, and for the same reason."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "ht_custom_gestures": {"fist": {"handFist": {"min": 0.8}}},
    }

    def build(self, settings_of, entry):
        config = {
            **self.ENABLED,
            "modes": {"root": {"type": "vosk", "path": "m", "gestures": {"fist": entry}}},
        }
        return settings_of(config).modes["root"].gestures

    def test_a_bare_response_is_the_press(self, settings_of):
        binding = self.build(settings_of, "left_mouse")["fist"]

        assert (binding.press, binding.release) == ("left_mouse", None)

    def test_both_edges_can_be_given(self, settings_of):
        binding = self.build(
            settings_of, {"press": "hold(left_mouse)", "release": "release(left_mouse)"}
        )["fist"]

        assert (binding.press, binding.release) == ("hold(left_mouse)", "release(left_mouse)")

    def test_the_release_alone_is_allowed(self, settings_of):
        binding = self.build(settings_of, {"release": "esc"})["fist"]

        assert (binding.press, binding.release) == (None, "esc")

    def test_null_takes_back_a_binding_a_template_gave(self, settings_of):
        assert self.build(settings_of, None) == {}

    def test_an_edge_that_does_not_exist_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="Gesture 'fist' in mode 'root' has hold"):
            self.build(settings_of, {"hold": "left_mouse"})

    def test_a_binding_that_is_neither_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="Gesture 'fist' in mode 'root' is 5"):
            self.build(settings_of, 5)

    def test_both_edges_are_validated_as_responses(self, settings_of):
        with pytest.raises(ConfigError, match="Gesture 'fist' on release.*bogus"):
            self.build(settings_of, {"press": "left_mouse", "release": "bogus"})

    def test_a_mode_target_on_either_edge_is_checked(self, settings_of):
        with pytest.raises(ConfigError, match="Gesture 'fist' on release.*not a runnable mode"):
            self.build(settings_of, {"release": "mode(typo)"})


class TestFeatureNames:
    """A misspelled feature is a load error."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def test_a_misspelled_blendshape_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'eyeBlinkleft'.*no model reports"):
            settings_of({**self.ENABLED, "ht_custom_gestures": {"b": {"eyeBlinkleft": {"min": 1}}}})

    def test_the_error_suggests_the_name_that_was_meant(self, settings_of):
        with pytest.raises(ConfigError, match="Did you mean 'eyeBlinkLeft'"):
            settings_of({**self.ENABLED, "ht_custom_gestures": {"b": {"eyeBlinkleft": {"min": 1}}}})

    def test_a_hand_feature_with_its_words_the_wrong_way_round_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="handIndexPinch"):
            settings_of(
                {**self.ENABLED, "ht_custom_gestures": {"p": {"handIndexPinch": {"min": 1}}}}
            )

    def test_every_shipped_gesture_names_real_features(self, settings_of):
        settings_of(self.ENABLED)

    def test_a_hand_feature_is_a_feature(self, settings_of):
        settings_of({**self.ENABLED, "ht_custom_gestures": {"f": {"leftHandFist": {"min": 0.8}}}})

    def test_a_hand_and_a_face_feature_may_share_one_gesture(self, settings_of):
        settings_of(
            {
                **self.ENABLED,
                "ht_custom_gestures": {
                    "shout": {"handFist": {"min": 0.8}, "jawOpen": {"min": 0.5}}
                },
            }
        )

    def test_a_gesture_needing_hands_that_are_off_is_reported_but_not_refused(
        self, settings_of, caplog
    ):
        settings_of({**self.ENABLED, "ht_custom_gestures": {"f": {"handFist": {"min": 0.8}}}})

        assert "'ht_hand_enabled' is off" in caplog.text
        assert "f" in caplog.text

    def test_nothing_is_said_when_the_hands_are_on(self, settings_of, caplog):
        settings_of(
            {
                **self.ENABLED,
                "ht_hand_enabled": True,
                "ht_hand_model_path": "hand_landmarker.task",
                "ht_custom_gestures": {"f": {"handFist": {"min": 0.8}}},
            }
        )

        assert "ht_hand_enabled' is off" not in caplog.text


class TestDebugGestureNames:
    """ht_debug_gestures takes a gesture name or a bare feature name, and nothing else."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "ht_custom_gestures": {"blink": {"eyeBlinkLeft": {"min": 0.5}}},
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def test_a_defined_gesture_is_accepted(self, settings_of):
        settings_of({**self.ENABLED, "ht_debug_gestures": ["blink"]})

    def test_a_bare_feature_is_accepted(self, settings_of):
        settings_of({**self.ENABLED, "ht_debug_gestures": ["handFist", "jawOpen"]})

    def test_a_name_that_is_neither_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'blimk'.*neither a defined gesture nor a feature"):
            settings_of({**self.ENABLED, "ht_debug_gestures": ["blimk"]})

    def test_the_error_lists_what_is_defined(self, settings_of):
        with pytest.raises(ConfigError, match="Defined gestures: blink"):
            settings_of({**self.ENABLED, "ht_debug_gestures": ["nonsense"]})


class TestHandTracking:
    """The second model on the same frame."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def test_hands_are_off_by_default(self, settings_of):
        assert settings_of(self.ENABLED).head_tracking.hands is None

    def test_hands_need_their_own_model_path(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_model_path.*hand_landmarker.task"):
            settings_of({**self.ENABLED, "ht_hand_enabled": True})

    def test_the_defaults_are_two_hands_at_a_quarter_of_the_frame_rate(self, settings_of):
        hands = settings_of(
            {**self.ENABLED, "ht_hand_enabled": True, "ht_hand_model_path": "hand.task"}
        ).head_tracking.hands

        assert (hands.max_hands, hands.stride) == (2, 4)

    def test_a_stride_below_one_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_stride' is 0"):
            settings_of(
                {
                    **self.ENABLED,
                    "ht_hand_enabled": True,
                    "ht_hand_model_path": "hand.task",
                    "ht_hand_stride": 0,
                }
            )

    def test_tracking_no_hands_is_what_the_switch_is_for(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_max_hands' is 0"):
            settings_of(
                {
                    **self.ENABLED,
                    "ht_hand_enabled": True,
                    "ht_hand_model_path": "hand.task",
                    "ht_hand_max_hands": 0,
                }
            )

    def test_a_hand_key_is_global_only(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_stride'"):
            settings_of(
                {
                    **self.ENABLED,
                    "modes": {"root": {"type": "vosk", "path": "m", "ht_hand_stride": 2}},
                }
            )

    def test_a_misspelled_hand_key_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_strid"):
            settings_of({**self.ENABLED, "ht_hand_strid": 2})


class TestTheHandCalibration:
    """Where each hand score's ends sit -- the constants a gesture that never fires blames."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "ht_hand_enabled": True,
        "ht_hand_model_path": "hand.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def calibration(self, settings_of, block=None):
        data = dict(self.ENABLED)
        if block is not None:
            data["ht_hand_calibration"] = block
        return settings_of(data).head_tracking.hands.calibration

    def test_with_none_set_the_defaults_stand(self, settings_of):
        assert self.calibration(settings_of) == Calibration()

    def test_a_constant_is_set_by_its_own_name(self, settings_of):
        assert self.calibration(settings_of, {"folded_extension": 0.36}).folded_extension == 0.36

    def test_a_constant_left_out_keeps_its_default(self, settings_of):
        measured = self.calibration(settings_of, {"folded_extension": 0.36})

        assert measured.folded_extension_thumb == Calibration().folded_extension_thumb

    def test_an_integer_is_taken_as_the_number_it_is(self, settings_of):
        assert self.calibration(settings_of, {"pinch_open": 1}).pinch_open == 1.0

    def test_a_name_that_is_not_a_constant_is_refused_listing_them(self, settings_of):
        with pytest.raises(ConfigError, match="folded_extention.*mean nothing"):
            self.calibration(settings_of, {"folded_extention": 0.36})

    def test_the_refusal_names_the_constants_that_do_exist(self, settings_of):
        with pytest.raises(ConfigError, match="folded_extension_thumb"):
            self.calibration(settings_of, {"nonesuch": 0.36})

    def test_a_value_that_is_not_a_number_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="takes numbers"):
            self.calibration(settings_of, {"folded_extension": "quite folded"})

    def test_a_block_that_is_not_an_object_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="must be a JSON object"):
            self.calibration(settings_of, [0.36])

    def test_a_pair_whose_two_ends_coincide_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="pinch_closed/pinch_open.*pins the score"):
            self.calibration(settings_of, {"pinch_closed": 0.4, "pinch_open": 0.4})

    def test_the_other_pair_too(self, settings_of):
        with pytest.raises(ConfigError, match="spread_closed/spread_open"):
            self.calibration(settings_of, {"spread_closed": 0.3, "spread_open": 0.3})

    def test_a_fold_of_one_would_make_every_finger_read_as_folded(self, settings_of):
        with pytest.raises(ConfigError, match="folded_extension is 1.0"):
            self.calibration(settings_of, {"folded_extension": 1.0})

    def test_a_palm_area_of_zero_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="palm_area_face_on"):
            self.calibration(settings_of, {"palm_area_face_on": 0.0})

    def test_it_is_a_global_key(self, settings_of):
        with pytest.raises(ConfigError, match="ht_hand_calibration"):
            settings_of(
                {
                    **self.ENABLED,
                    "modes": {"root": {"type": "vosk", "path": "m", "ht_hand_calibration": {}}},
                }
            )

    def test_it_reaches_the_settings_the_tracker_scores_with(self, settings_of):
        assert self.calibration(settings_of, {"folded_extension": 0.36}) != Calibration()


class TestTheFaceBaseline:
    """ht_face_baseline: what each blendshape sits at on this face doing nothing."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def baseline(self, settings_of, block=None):
        data = dict(self.ENABLED)
        if block is not None:
            data["ht_face_baseline"] = block
        return settings_of(data).head_tracking.face_baseline

    def test_with_none_set_the_scores_are_left_raw(self, settings_of):
        assert self.baseline(settings_of) == {}

    def test_a_resting_level_is_read_under_its_blendshape_s_own_name(self, settings_of):
        assert self.baseline(settings_of, {"eyeBlinkRight": 0.394}) == {"eyeBlinkRight": 0.394}

    def test_an_integer_zero_is_taken_as_the_number_it_is(self, settings_of):
        assert self.baseline(settings_of, {"jawOpen": 0}) == {"jawOpen": 0.0}

    def test_a_hand_score_does_not_belong_here_and_says_so(self, settings_of):
        with pytest.raises(ConfigError, match="handFist.*not one of mediapipe"):
            self.baseline(settings_of, {"handFist": 0.2})

    def test_a_misspelled_blendshape_is_refused_with_a_suggestion(self, settings_of):
        with pytest.raises(ConfigError, match="eyeBlinkLoft.*Did you mean"):
            self.baseline(settings_of, {"eyeBlinkLoft": 0.3})

    def test_a_level_at_one_is_refused_because_nothing_could_clear_it(self, settings_of):
        with pytest.raises(ConfigError, match="mouthClose.*range left above it"):
            self.baseline(settings_of, {"mouthClose": 1.0})

    def test_a_negative_level_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match=r"in \[0, 1\)"):
            self.baseline(settings_of, {"mouthClose": -0.1})

    def test_a_value_that_is_not_a_number_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="resting level is a number"):
            self.baseline(settings_of, {"mouthClose": "quite shut"})

    def test_a_block_that_is_not_an_object_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="must be a JSON object"):
            self.baseline(settings_of, [0.4])

    def test_it_is_a_global_key(self, settings_of):
        with pytest.raises(ConfigError, match="ht_face_baseline"):
            settings_of(
                {
                    **self.ENABLED,
                    "modes": {"root": {"type": "vosk", "path": "m", "ht_face_baseline": {}}},
                }
            )


class TestGrammarSize:
    def test_a_huge_numeric_expansion_is_reported(self, settings_of, caplog):
        template = "code " + " ".join(["{numeric}"] * 6)
        settings_of(
            {"modes": {"root": {"type": "vosk", "path": "m", "commands": {template: "esc"}}}}
        )

        assert "1000000 grammar phrases" in caplog.text
        assert template in caplog.text

    def test_a_reasonable_expansion_is_quiet(self, settings_of, caplog):
        settings_of(
            {
                "modes": {
                    "root": {"type": "vosk", "path": "m", "commands": {"line {numeric}": "esc"}}
                }
            }
        )
        assert caplog.text == ""


class TestPedalNames:
    DEVICE = {"enable_pedals": True, "pedal": {"buttons": {"left": 4, "right": 6}}}

    def test_an_undefined_pedal_lists_the_ones_the_board_has(self, settings_of):
        with pytest.raises(ConfigError, match="'middle'.*Defined pedals: left, right"):
            settings_of(
                {
                    **self.DEVICE,
                    "modes": {"root": {"type": "vosk", "path": "m", "pedals": {"middle": "enter"}}},
                }
            )

    def test_a_defined_pedal_is_accepted(self, settings_of):
        settings = settings_of(
            {
                **self.DEVICE,
                "modes": {"root": {"type": "vosk", "path": "m", "pedals": {"left": "enter"}}},
            }
        )

        assert settings.modes["root"].pedals["left"].press == "enter"

    def test_pedals_are_not_checked_without_a_device(self, settings_of):
        settings_of({"modes": {"root": {"type": "vosk", "path": "m", "pedals": {"no": "esc"}}}})

    def test_a_template_nobody_imports_is_still_checked(self, settings_of):
        with pytest.raises(ConfigError, match="'middle'"):
            settings_of(
                {
                    **self.DEVICE,
                    "modes": {
                        "root": {"type": "vosk", "path": "m"},
                        "spare": {"pedals": {"middle": "enter"}},
                    },
                }
            )


class TestPedalBindings:
    DEVICE = {"enable_pedals": True}

    def mode(self, pedals):
        return {**self.DEVICE, "modes": {"root": {"type": "vosk", "path": "m", "pedals": pedals}}}

    def test_a_bare_response_is_the_press(self, settings_of):
        binding = settings_of(self.mode({"left": "enter"})).modes["root"].pedals["left"]

        assert (binding.press, binding.release) == ("enter", None)

    def test_both_edges_can_be_given(self, settings_of):
        binding = (
            settings_of(self.mode({"left": {"press": "hold(shift)", "release": "release(shift)"}}))
            .modes["root"]
            .pedals["left"]
        )

        assert (binding.press, binding.release) == ("hold(shift)", "release(shift)")

    def test_null_is_a_pedal_that_deliberately_does_nothing(self, settings_of):
        assert settings_of(self.mode({"left": None})).modes["root"].pedals == {}

    def test_an_unknown_edge_is_refused_by_name(self, settings_of):
        with pytest.raises(ConfigError, match="hold.*no other edge"):
            settings_of(self.mode({"left": {"hold": "enter"}}))

    def test_a_list_is_not_a_binding(self, settings_of):
        with pytest.raises(ConfigError, match="'press' and/or 'release'"):
            settings_of(self.mode({"left": ["enter"]}))

    def test_both_edges_are_validated_as_responses(self, settings_of):
        with pytest.raises(ConfigError, match="Pedal 'left' on release.*neither a known key"):
            settings_of(self.mode({"left": {"press": "enter", "release": "bogus"}}))

    def test_a_binding_in_a_template_names_the_template(self, settings_of):
        with pytest.raises(ConfigError, match=r"of mode '::feet'"):
            settings_of(
                {
                    **self.DEVICE,
                    "modes": {
                        "::feet": {"pedals": {"left": "bogus"}},
                        "root": {"type": "vosk", "path": "m", "imports": ["::feet"]},
                    },
                }
            )


class TestThePedalDevice:
    def test_the_defaults_are_the_stream_deck_pedal(self, settings_of):
        pedal = settings_of({"enable_pedals": True, **MINIMAL}).pedal

        assert (pedal.vendor_id, pedal.product_id) == (0x0FD9, 0x0086)
        assert pedal.buttons == {"left": 4, "middle": 5, "right": 6}

    def test_pedals_are_off_by_default(self, settings_of):
        assert settings_of(MINIMAL).pedal is None

    @pytest.mark.parametrize("written", ["0x0fd9", "0X0FD9", 4057, "4057"])
    def test_a_usb_id_may_be_hex_or_decimal(self, settings_of, written):
        settings = settings_of({"enable_pedals": True, "pedal": {"vendor_id": written}, **MINIMAL})

        assert settings.pedal.vendor_id == 0x0FD9

    def test_a_usb_id_that_is_neither_says_so(self, settings_of):
        with pytest.raises(ConfigError, match="'pedal.vendor_id'.*0x0fd9"):
            settings_of({"enable_pedals": True, "pedal": {"vendor_id": "elgato"}, **MINIMAL})

    def test_an_offset_past_the_report_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="byte 9.*report_length.*8"):
            settings_of({"enable_pedals": True, "pedal": {"buttons": {"left": 9}}, **MINIMAL})

    def test_an_offset_that_is_not_a_byte_index_says_so(self, settings_of):
        with pytest.raises(ConfigError, match="'pedal.buttons.left'.*byte offset"):
            settings_of({"enable_pedals": True, "pedal": {"buttons": {"left": -1}}, **MINIMAL})

    def test_a_board_with_no_pedals_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'pedal.buttons'"):
            settings_of({"enable_pedals": True, "pedal": {"buttons": {}}, **MINIMAL})

    def test_the_description_is_readable_in_a_log(self, settings_of):
        pedal = settings_of({"enable_pedals": True, **MINIMAL}).pedal

        assert pedal.describe() == "0fd9:0086 (left, middle, right)"


class TestLayerTargets:
    CONFIG = {
        "modes": {
            "root": {"type": "vosk", "path": "m"},
            "feet": {"pedals": {"left": "enter"}},
        }
    }

    def command(self, response):
        modes = {**self.CONFIG["modes"]}
        modes["root"] = {**modes["root"], "commands": {"go": response}}
        return {"enable_pedals": True, "modes": modes}

    def test_a_pedal_layer_may_be_pointed_at_a_template(self, settings_of):
        settings_of(self.command("mode(pedal:feet)"))

    def test_the_voice_layer_may_not(self, settings_of):
        with pytest.raises(ConfigError, match="'voice:feet'.*not a runnable mode"):
            settings_of(self.command("mode(voice:feet)"))

    def test_an_unqualified_target_may_not_either(self, settings_of):
        with pytest.raises(ConfigError, match="'feet'.*not a runnable mode"):
            settings_of(self.command("mode(feet)"))

    def test_a_layer_target_that_names_no_mode_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'pedal:nope'.*not a defined mode"):
            settings_of(self.command("mode(pedal:nope)"))

    def test_the_go_back_keyword_works_per_layer(self, settings_of):
        settings_of(self.command("mode(pedal:previous mode)"))

    def test_a_layer_may_rejoin_the_voice_layer(self, settings_of):
        settings_of(self.command("mode(pedal:voice)"))

    def test_a_starting_layer_mode_may_be_a_template(self, settings_of):
        settings = settings_of({**self.CONFIG, "starting_pedal_mode": "feet"})

        assert settings.starting_pedal_mode == "feet"

    def test_an_unknown_starting_layer_mode_lists_the_modes(self, settings_of):
        with pytest.raises(ConfigError, match="'starting_gesture_mode'.*Known modes: feet, root"):
            settings_of({**self.CONFIG, "starting_gesture_mode": "nope"})

    def test_it_defaults_to_following_the_voice_layer(self, settings_of):
        settings = settings_of(self.CONFIG)

        assert settings.starting_for(Layer.GESTURE) is None
        assert settings.starting_for(Layer.VOICE) == "root"


class TestControlCommands:
    def control(self, commands, *, enabled=True):
        return {
            "enable_pedals": True,
            "enable_control": enabled,
            "control": {"commands": commands},
            "modes": {"root": {"type": "vosk", "path": "m"}, "feet": {"pedals": {}}},
        }

    def test_names_are_normalized_as_an_utterance_is(self, settings_of):
        settings = settings_of(self.control({"Break  Started": "mode(pedal:feet)"}))

        assert settings.control.commands == {"break started": "mode(pedal:feet)"}

    def test_two_names_that_normalize_alike_are_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'go' and 'GO' are one name"):
            settings_of(self.control({"go": "enter", "GO": "esc"}))

    def test_a_response_that_could_never_run_names_the_command(self, settings_of):
        with pytest.raises(ConfigError, match="Control command 'go' switches to 'pedal:nope'"):
            settings_of(self.control({"go": "mode(pedal:nope)"}))

    def test_a_missing_response_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="'go' needs a response"):
            settings_of(self.control({"go": ""}))

    def test_the_block_must_be_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="'control' must be"):
            settings_of({**MINIMAL, "control": ["go"]})

    def test_commands_must_be_an_object(self, settings_of):
        with pytest.raises(ConfigError, match="'control.commands' must be"):
            settings_of({**MINIMAL, "enable_control": True, "control": {"commands": ["go"]}})

    def test_nothing_is_read_while_control_is_off(self, settings_of):
        settings = settings_of(self.control({"go": 5}, enabled=False))

        assert settings.control is None


class TestTheControlLaw:
    """ht_pointer_law: which of the two laws a mode uses, and what each one needs."""

    SCREEN = {"width_mm": 597.0, "height_mm": 336.0, "distance_mm": 600.0}

    def config(self, law=None, *, screen=True, auto_recenter=None):
        mode = {"type": "vosk", "path": "m", "ht_enabled": True}
        if law is not None:
            mode["ht_pointer_law"] = law
        data = {
            "enable_head_tracking": True,
            "ht_model_path": "landmarker.task",
            "modes": {"root": mode},
        }
        if screen:
            data["ht_screen_calibration"] = self.SCREEN
        if auto_recenter is not None:
            data["ht_auto_recenter_seconds"] = auto_recenter
        return data

    def test_the_rate_law_is_the_default_so_every_old_config_still_means_what_it_did(
        self, settings_of
    ):
        assert settings_of(self.config()).modes["root"].pointer.law == "rate"

    def test_a_mode_can_ask_for_the_absolute_law(self, settings_of):
        assert settings_of(self.config("absolute")).modes["root"].pointer.law == "absolute"

    def test_a_law_that_is_not_one_of_the_two_is_refused_listing_them(self, settings_of):
        with pytest.raises(ConfigError, match="'ht_pointer_law' of 'laser'"):
            settings_of(self.config("laser"))

    def test_the_absolute_law_without_a_calibration_is_refused_by_mode_name(self, settings_of):
        with pytest.raises(ConfigError, match="'root'.*ht_screen_calibration"):
            settings_of(self.config("absolute", screen=False))

    def test_the_rate_law_needs_no_calibration(self, settings_of):
        assert settings_of(self.config("rate", screen=False)).modes["root"].pointer.law == "rate"

    def test_auto_recentring_beside_an_absolute_mode_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="Auto-recentring moves neutral"):
            settings_of(self.config("absolute", auto_recenter=4.0))

    def test_auto_recentring_beside_a_rate_mode_is_fine(self, settings_of):
        assert settings_of(self.config("rate", auto_recenter=4.0)).head_tracking is not None

    def test_an_absolute_mode_is_not_held_to_the_rate_curve_s_rules(self, settings_of):
        data = self.config("absolute")
        data["modes"]["root"].update({"ht_dead_angle_h": 30.0, "ht_full_speed_angle": 5.0})
        assert settings_of(data).modes["root"].pointer.law == "absolute"


class TestTheScreenCalibration:
    """ht_screen_calibration: where a head angle points."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }

    def screen(self, settings_of, block=None):
        data = dict(self.ENABLED)
        if block is not None:
            data["ht_screen_calibration"] = block
        return settings_of(data).head_tracking.screen

    def test_with_none_set_there_is_no_mapping(self, settings_of):
        assert not self.screen(settings_of).measured

    def test_a_tape_measure_gives_a_usable_mapping(self, settings_of):
        mapping = self.screen(settings_of, {"width_mm": 597, "height_mm": 336, "distance_mm": 600})
        assert mapping.measured
        assert mapping.at(0.0, 0.0) == pytest.approx((0.5, 0.5))

    def test_a_fitted_block_round_trips_through_the_loader(self, settings_of):
        written = {
            "x": [-1.004, 0.0, 0.5],
            "y": [0.0, 1.785, 0.5],
            "perspective": [0.02, -0.04],
            "residual": 0.004,
            "samples": 9,
        }
        mapping = self.screen(settings_of, written)
        assert mapping.as_config()["x"] == written["x"]
        assert mapping.as_config()["perspective"] == written["perspective"]
        assert mapping.samples == 9

    def test_a_screen_with_no_size_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="will not work"):
            self.screen(settings_of, {"width_mm": 0, "height_mm": 336, "distance_mm": 600})

    def test_a_block_describing_no_mapping_at_all_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="describes no mapping"):
            self.screen(settings_of, {"distance_mm": 600})

    def test_the_wrong_number_of_coefficients_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="list of 3 coefficients"):
            self.screen(settings_of, {"x": [1.0, 2.0], "y": [0.0, -1.0, 0.5]})

    def test_a_perspective_that_is_not_a_pair_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="two shared denominator terms"):
            self.screen(settings_of, {"x": [1, 0, 0.5], "y": [0, -1, 0.5], "perspective": [0.1]})

    def test_a_block_that_is_not_an_object_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="must be a JSON object"):
            self.screen(settings_of, [597, 336, 600])

    def test_it_is_a_global_key(self, settings_of):
        with pytest.raises(ConfigError, match="ht_screen_calibration"):
            settings_of(
                {
                    **self.ENABLED,
                    "modes": {"root": {"type": "vosk", "path": "m", "ht_screen_calibration": {}}},
                }
            )


class TestTheScreenBounds:
    """ht_screen_bounds: which monitor the calibrated screen is, out of all of them."""

    ENABLED = {
        "enable_head_tracking": True,
        "ht_model_path": "landmarker.task",
        "modes": {"root": {"type": "vosk", "path": "m"}},
    }
    SIDE_BY_SIDE = {
        "x": 1920,
        "y": 0,
        "width": 1920,
        "height": 1080,
        "desktop_width": 3840,
        "desktop_height": 1080,
    }

    def bounds(self, settings_of, block=None):
        data = dict(self.ENABLED)
        if block is not None:
            data["ht_screen_bounds"] = block
        return settings_of(data).head_tracking.placement

    def test_with_none_set_the_screen_is_the_whole_desktop(self, settings_of):
        assert self.bounds(settings_of).whole_desktop

    def test_it_reaches_the_settings_as_fractions_of_the_desktop(self, settings_of):
        placement = self.bounds(settings_of, self.SIDE_BY_SIDE)
        assert not placement.whole_desktop
        assert placement.into_desktop(0.0, 0.5) == pytest.approx((0.5, 0.5))
        assert placement.into_desktop(1.0, 0.5) == pytest.approx((1.0, 0.5))

    @pytest.mark.parametrize("dropped", sorted(SIDE_BY_SIDE))
    def test_every_one_of_the_six_is_required(self, settings_of, dropped):
        block = {k: v for k, v in self.SIDE_BY_SIDE.items() if k != dropped}
        with pytest.raises(ConfigError, match=f"missing '{dropped}'|missing .*'{dropped}'"):
            self.bounds(settings_of, block)

    def test_a_desktop_smaller_than_the_monitor_in_it_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="does not fit inside a desktop"):
            self.bounds(settings_of, {**self.SIDE_BY_SIDE, "desktop_width": 1920})

    def test_a_desktop_with_no_width_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="desktop_width"):
            self.bounds(settings_of, {**self.SIDE_BY_SIDE, "desktop_width": 0})

    def test_pixels_that_are_not_numbers_are_refused(self, settings_of):
        with pytest.raises(ConfigError, match="numbers of pixels"):
            self.bounds(settings_of, {**self.SIDE_BY_SIDE, "x": "1920px"})

    def test_a_block_that_is_not_an_object_is_refused(self, settings_of):
        with pytest.raises(ConfigError, match="must be a JSON object"):
            self.bounds(settings_of, [1920, 0, 1920, 1080])
