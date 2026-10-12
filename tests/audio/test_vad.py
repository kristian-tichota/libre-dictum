import numpy as np
import pytest

from libre_dictum.audio.vad import (
    CEILING_DB,
    FLOOR_DB,
    MicrophoneGauge,
    VoiceActivityChunker,
    loudness,
    rms,
)

SAMPLE_RATE = 1000
BLOCK_DURATION = 0.1
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_DURATION)

LOUD = np.full(BLOCK_SAMPLES, 0.5, dtype=np.float32)
QUIET = np.zeros(BLOCK_SAMPLES, dtype=np.float32)


def chunker(**overrides):
    options = {
        "sample_rate": SAMPLE_RATE,
        "block_duration": BLOCK_DURATION,
        "silence_seconds": 0.3,
        "max_chunk_seconds": 2.0,
        "energy_threshold": 0.01,
        "pre_roll_seconds": 0.2,
    }
    return VoiceActivityChunker(**{**options, **overrides})


def feed(chunker, blocks):
    return [chunk for chunk in (chunker.push(block) for block in blocks) if chunk is not None]


class TestRms:
    def test_silence_is_zero(self):
        assert rms(QUIET) == pytest.approx(0.0)

    def test_an_empty_block_is_zero(self):
        assert rms(np.array([], dtype=np.float32)) == pytest.approx(0.0)

    def test_a_constant_block_is_its_amplitude(self):
        assert rms(LOUD) == pytest.approx(0.5)


class TestSegmenting:
    def test_silence_alone_produces_nothing(self):
        assert feed(chunker(), [QUIET] * 10) == []

    def test_speech_followed_by_silence_produces_one_chunk(self):
        chunks = feed(chunker(), [LOUD] * 5 + [QUIET] * 3)
        assert len(chunks) == 1

    def test_a_chunk_ends_only_after_enough_silence(self):
        assert feed(chunker(silence_seconds=0.3), [LOUD] * 5 + [QUIET] * 2) == []

    def test_two_utterances_produce_two_chunks(self):
        blocks = [LOUD] * 3 + [QUIET] * 4 + [LOUD] * 3 + [QUIET] * 4
        assert len(feed(chunker(), blocks)) == 2

    def test_a_long_utterance_is_cut_at_the_maximum(self):
        chunks = feed(chunker(max_chunk_seconds=0.5), [LOUD] * 20)
        assert len(chunks) > 1


class TestPreRoll:
    def test_the_block_that_crossed_the_threshold_is_kept(self):
        chunks = feed(chunker(pre_roll_seconds=0.0), [LOUD] * 3 + [QUIET] * 3)
        assert len(chunks[0]) >= 3 * BLOCK_SAMPLES

    def test_quiet_blocks_before_speech_are_kept(self):
        chunks = feed(chunker(pre_roll_seconds=0.2), [QUIET] * 5 + [LOUD] * 3 + [QUIET] * 3)
        assert len(chunks[0]) == 7 * BLOCK_SAMPLES

    def test_the_pre_roll_ring_does_not_grow_without_bound(self):
        chunks = feed(chunker(pre_roll_seconds=0.2), [QUIET] * 100 + [LOUD] * 1 + [QUIET] * 3)
        assert len(chunks[0]) == 5 * BLOCK_SAMPLES


class TestFlushAndReset:
    def test_flush_returns_a_chunk_in_progress(self):
        instance = chunker()
        feed(instance, [LOUD] * 3)
        assert instance.flush() is not None

    def test_flush_with_nothing_recorded_returns_nothing(self):
        assert chunker().flush() is None

    def test_reset_drops_a_chunk_in_progress(self):
        instance = chunker()
        feed(instance, [LOUD] * 3)
        instance.reset()

        assert instance.recording is False
        assert instance.flush() is None


class TestLoudness:
    def test_silence_is_the_floor(self):
        assert loudness(0.0) == 0.0

    def test_the_ends_of_the_decibel_range_are_the_ends_of_the_scale(self):
        assert loudness(10 ** (FLOOR_DB / 20)) == pytest.approx(0.0)
        assert loudness(10 ** (CEILING_DB / 20)) == pytest.approx(1.0)

    def test_anything_beyond_the_range_is_clamped(self):
        assert loudness(1e-9) == 0.0
        assert loudness(1.0) == 1.0

    def test_louder_is_higher(self):
        assert loudness(0.001) < loudness(0.01) < loudness(0.1)


class TestTheMicrophoneGauge:
    def gauge(self, **overrides):
        segmenter = chunker(**overrides)
        return segmenter, MicrophoneGauge(segmenter, level_seconds=0.05, history=4)

    def test_each_block_gives_a_level_per_slice(self):
        segmenter, gauge = self.gauge()
        segmenter.push(LOUD)

        gauge.hear(LOUD)

        assert gauge.reading().levels == (loudness(0.5), loudness(0.5))

    def test_only_the_most_recent_levels_are_kept(self):
        _, gauge = self.gauge()
        for block in (QUIET, QUIET, LOUD, LOUD):
            gauge.hear(block)

        assert gauge.reading().levels == (loudness(0.5),) * 4

    def test_the_gate_is_the_threshold_on_the_same_scale(self):
        _, gauge = self.gauge(energy_threshold=0.01)

        assert gauge.reading().gate == loudness(0.01)

    def test_speech_starts_a_recording_with_all_its_patience_left(self):
        segmenter, gauge = self.gauge()
        segmenter.push(LOUD)

        reading = gauge.reading()

        assert reading.recording
        assert reading.left == pytest.approx(0.3)
        assert reading.remaining == pytest.approx(1.0)

    def test_quiet_after_speech_counts_down(self):
        segmenter, gauge = self.gauge()
        feed(segmenter, [LOUD, QUIET])

        reading = gauge.reading()

        assert reading.quiet == pytest.approx(0.1)
        assert reading.left == pytest.approx(0.2)

    def test_speech_again_resets_the_count(self):
        segmenter, gauge = self.gauge()
        feed(segmenter, [LOUD, QUIET, QUIET, LOUD])

        assert gauge.reading().quiet == 0.0

    def test_waiting_for_speech_is_not_recording(self):
        segmenter, gauge = self.gauge()
        feed(segmenter, [QUIET])

        assert not gauge.reading().recording

    def test_a_transcription_is_marked(self):
        _, gauge = self.gauge()

        assert gauge.reading(transcribing=True).transcribing

    def test_reset_forgets_the_levels(self):
        _, gauge = self.gauge()
        gauge.hear(LOUD)

        gauge.reset()

        assert gauge.reading().levels == ()
