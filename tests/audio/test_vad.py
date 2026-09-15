import numpy as np
import pytest

from libre_dictum.audio.vad import VoiceActivityChunker, rms

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
