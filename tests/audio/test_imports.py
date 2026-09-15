import subprocess
import sys
import textwrap


class TestImportingWithoutASoundStack:
    """Only the modules that actually open a stream may need a sound stack."""

    SCRIPT = textwrap.dedent("""
        import sys

        class Blocked:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] == "sounddevice":
                    raise ImportError(f"no {name} here")
                return None

        sys.meta_path.insert(0, Blocked())
        """)

    def run_blocked(self, *lines: str) -> subprocess.CompletedProcess[str]:
        script = "\n".join([self.SCRIPT, *lines])
        return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)

    def test_everything_that_opens_no_microphone_imports(self):
        result = self.run_blocked(
            "from libre_dictum.audio import AudioStream",
            "from libre_dictum.audio.grammar import build_grammar",
            "from libre_dictum.audio.partials import PartialTracker",
            "from libre_dictum.audio.vad import VoiceActivityChunker",
            "from libre_dictum.audio.transcriber import create_transcriber",
            "import libre_dictum.app",
        )

        assert result.returncode == 0, result.stderr

    def test_a_recognizer_still_fails_loudly_when_asked_for(self):
        result = self.run_blocked("from libre_dictum.audio import VoskStream")

        assert result.returncode != 0
        assert "sounddevice" in result.stderr
