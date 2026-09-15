import logging

import pytest

from libre_dictum.audio import transcriber as transcriber_module
from libre_dictum.audio.transcriber import (
    create_transcriber,
    load_cached_first,
    takes_language,
)


@pytest.fixture
def backends(monkeypatch):
    """Replace both backends with records of how they were constructed."""
    built: list[tuple[str, str, str]] = []

    class FakeWhisper:
        def __init__(self, model_name):
            built.append(("whisper", model_name, ""))

    class FakeTransformers:
        def __init__(self, model_name, device="auto"):
            built.append(("transformers", model_name, device))

    monkeypatch.setattr(transcriber_module, "WhisperTranscriber", FakeWhisper)
    monkeypatch.setattr(transcriber_module, "TransformersTranscriber", FakeTransformers)
    return built


class TestBackendSelection:
    def test_a_whisper_prefix_selects_openai_whisper(self, backends):
        create_transcriber("whisper-turbo")
        assert backends == [("whisper", "turbo", "")]

    def test_the_prefix_is_stripped_from_the_model_name(self, backends):
        create_transcriber("whisper-large-v3")
        assert backends[0][1] == "large-v3"

    def test_anything_else_is_a_huggingface_id(self, backends):
        create_transcriber("openai/whisper-small")
        assert backends == [("transformers", "openai/whisper-small", "auto")]

    def test_the_configured_device_is_passed_through(self, backends):
        create_transcriber("some/model", device="cuda")
        assert backends[0][2] == "cuda"


class FakeLoader:
    """Stands in for AutoModelForSpeechSeq2Seq / AutoProcessor."""

    def __init__(self, cached: bool = True) -> None:
        self.cached = cached
        self.calls: list[dict] = []

    def from_pretrained(self, model_name, **kwargs):
        self.calls.append({"model_name": model_name, **kwargs})
        if kwargs.get("local_files_only") and not self.cached:
            raise OSError(
                "We couldn't connect to 'https://huggingface.co' to load the files, and "
                "couldn't find them in the cached files."
            )
        return f"loaded {model_name}"


class TestCacheFirstLoading:
    """A cached model must not cost a network round trip per file at startup."""

    def test_a_cached_model_is_loaded_without_touching_the_hub(self):
        loader = FakeLoader(cached=True)
        assert load_cached_first(loader, "some/model") == "loaded some/model"
        assert loader.calls == [{"model_name": "some/model", "local_files_only": True}]

    def test_a_missing_model_falls_back_to_downloading_it(self):
        loader = FakeLoader(cached=False)
        assert load_cached_first(loader, "some/model") == "loaded some/model"
        assert [call.get("local_files_only", False) for call in loader.calls] == [True, False]

    def test_the_fallback_is_announced_rather_than_silent(self, caplog):
        loader = FakeLoader(cached=False)
        with caplog.at_level(logging.INFO, logger="libre_dictum.audio.transcriber"):
            load_cached_first(loader, "some/model")
        assert "some/model" in caplog.text

    def test_extra_arguments_survive_both_attempts(self):
        loader = FakeLoader(cached=False)
        load_cached_first(loader, "some/model", device_map="auto")
        assert all(call["device_map"] == "auto" for call in loader.calls)


class TestWhereTheLanguageGoes:
    """Whisper wants the language at generation; Cohere's ASR wants it at extraction."""

    def test_a_processor_that_requires_it_is_detected(self):
        def cohere_like(audio, language, text=None, sampling_rate=None, **kwargs):
            pass

        assert takes_language(cohere_like)

    def test_a_star_kwargs_processor_is_not_credited_with_it(self):
        def whisper_like(*args, **kwargs):
            pass

        assert not takes_language(whisper_like)

    def test_a_processor_with_no_language_at_all_is_left_alone(self):
        def plain(audio, sampling_rate=None, return_tensors=None):
            pass

        assert not takes_language(plain)

    def test_an_unintrospectable_callable_falls_back_to_the_old_path(self):
        assert not takes_language(object())
