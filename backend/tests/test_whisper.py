"""T24: FasterWhisperSpeechProvider tests (TDD red-green).

faster-whisper is never imported here: the model factory is injected, so
unit tests neither import the SDK nor download weights. The empty-audio
short-circuit is exercised with T19's real artifact (44-byte header-only WAV).
"""

import wave
from pathlib import Path

import pytest

from backend.config import Settings
from backend.providers.base import SpeechProvider
from backend.providers.whisper import (
    FasterWhisperSpeechProvider,
    SpeechTranscriptionError,
)
from backend.schemas.context import SpeechExtraction


# --- duck-typed faster-whisper stubs: transcribe(path) -> (segments, info) ---


class _StubSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class _StubInfo:
    def __init__(self, language: str = "id", language_probability: float = 0.95):
        self.language = language
        self.language_probability = language_probability


class _StubModel:
    def __init__(self, segments: list | None = None, info: _StubInfo | None = None, transcribe_error: Exception | None = None):
        self._segments = segments or []
        self._info = info or _StubInfo()
        self._transcribe_error = transcribe_error
        self.transcribe_calls = 0

    def transcribe(self, audio_path: str):
        self.transcribe_calls += 1
        if self._transcribe_error is not None:
            raise self._transcribe_error
        return iter(self._segments), self._info


class _ScriptedFactory:
    """Model factory stub: yields scripted models; the last outcome repeats."""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def __call__(self, model_size: str):
        self.calls.append(model_size)
        outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- wav fixtures ---


def _wav_path(tmp_path: Path, frames: int = 1600) -> Path:
    """Real mono 16 kHz WAV with ``frames`` samples of silence."""
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00" * frames * 2)
    return path


def _empty_wav_path(tmp_path: Path) -> Path:
    """T19's no-audio artifact: 44-byte header-only WAV (zero frames)."""
    path = tmp_path / "empty.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
    assert path.stat().st_size == 44
    return path


# --- contract ---


def test_provider_satisfies_speech_provider_protocol():
    assert isinstance(FasterWhisperSpeechProvider(model_size="tiny"), SpeechProvider)


# --- missing path: clear error, no model load, no retry ---


async def test_transcribe_missing_path_raises_clear_error_without_model_load():
    factory = _ScriptedFactory([_StubModel()])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    with pytest.raises(SpeechTranscriptionError, match="not found"):
        await provider.transcribe("/tmp/definitely-missing-audio.wav")

    assert factory.calls == []  # missing audio is never treated as a model failure


# --- no-audio / empty wav: short-circuit before model load ---


async def test_transcribe_empty_wav_returns_empty_extraction_without_model_load(tmp_path):
    factory = _ScriptedFactory([_StubModel()])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    result = await provider.transcribe(str(_empty_wav_path(tmp_path)))

    assert isinstance(result, SpeechExtraction)
    assert result.transcript == ""
    assert result.segments == []
    assert result.language is None
    assert result.confidence is None
    assert factory.calls == []  # zero-frame audio never reaches the model


async def test_transcribe_non_wav_raises_clear_error_without_model_load(tmp_path):
    bad = tmp_path / "garbage.wav"
    bad.write_bytes(b"this is not a wav file")
    factory = _ScriptedFactory([_StubModel()])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    with pytest.raises(SpeechTranscriptionError, match="WAV"):
        await provider.transcribe(str(bad))

    assert factory.calls == []


# --- successful transcription: join segments, preserve timing/language ---


async def test_transcribe_joins_segments_and_preserves_timing(tmp_path):
    model = _StubModel(
        segments=[
            _StubSegment(0.0, 1.2, " halo dunia "),
            _StubSegment(1.2, 2.4, "ini uji coba"),
            _StubSegment(2.4, 3.0, ""),  # empty text must be skipped
        ],
        info=_StubInfo(language="id", language_probability=0.91),
    )
    factory = _ScriptedFactory([model])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    result = await provider.transcribe(str(_wav_path(tmp_path)))

    assert result.transcript == "halo dunia ini uji coba"
    assert result.language == "id"
    assert result.confidence == 0.91
    assert result.segments == [
        {"start": 0.0, "end": 1.2, "text": "halo dunia"},
        {"start": 1.2, "end": 2.4, "text": "ini uji coba"},
    ]
    assert model.transcribe_calls == 1
    assert factory.calls == ["tiny"]


async def test_transcribe_no_segments_yields_empty_transcript(tmp_path):
    model = _StubModel(segments=[], info=_StubInfo(language="en", language_probability=0.5))
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=_ScriptedFactory([model]))

    result = await provider.transcribe(str(_wav_path(tmp_path)))

    assert result.transcript == ""
    assert result.segments == []
    assert result.language == "en"
    assert result.confidence == 0.5


# --- model-load failure: retry exactly once, then clear error ---


async def test_model_load_failure_recovers_on_retry(tmp_path):
    factory = _ScriptedFactory(
        [RuntimeError("load boom"), _StubModel(segments=[_StubSegment(0.0, 1.0, "recovered")])]
    )
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    result = await provider.transcribe(str(_wav_path(tmp_path)))

    assert result.transcript == "recovered"
    assert factory.calls == ["tiny", "tiny"]  # initial + one retry


async def test_model_load_failure_raises_after_exactly_one_retry(tmp_path):
    factory = _ScriptedFactory([RuntimeError("load boom")])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    with pytest.raises(SpeechTranscriptionError, match="load boom"):
        await provider.transcribe(str(_wav_path(tmp_path)))

    assert factory.calls == ["tiny", "tiny"]  # bounded: never more than two attempts


# --- transcription failure: retry once with a fresh model, then clear error ---


async def test_transcribe_failure_recovers_on_retry_with_fresh_model(tmp_path):
    broken = _StubModel(transcribe_error=RuntimeError("transcribe boom"))
    healthy = _StubModel(segments=[_StubSegment(0.0, 1.0, "fresh start")])
    factory = _ScriptedFactory([broken, healthy])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    result = await provider.transcribe(str(_wav_path(tmp_path)))

    assert result.transcript == "fresh start"
    assert broken.transcribe_calls == 1
    assert healthy.transcribe_calls == 1
    assert factory.calls == ["tiny", "tiny"]  # failed cached model dropped and reloaded


async def test_transcribe_failure_raises_after_exactly_one_retry(tmp_path):
    model = _StubModel(transcribe_error=RuntimeError("always boom"))
    factory = _ScriptedFactory([model])
    provider = FasterWhisperSpeechProvider(model_size="tiny", model_factory=factory)

    with pytest.raises(SpeechTranscriptionError, match="always boom"):
        await provider.transcribe(str(_wav_path(tmp_path)))

    assert factory.calls == ["tiny", "tiny"]
    assert model.transcribe_calls == 2


# --- model size: Settings default vs explicit constructor override ---


async def test_provider_defaults_model_size_from_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("WHISPER_MODEL_SIZE", raising=False)
    factory = _ScriptedFactory([_StubModel()])
    provider = FasterWhisperSpeechProvider(model_factory=factory)  # no explicit size

    await provider.transcribe(str(_wav_path(tmp_path)))

    assert Settings().whisper_model_size == "tiny"
    assert factory.calls == ["tiny"]


async def test_explicit_model_size_overrides_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "base")
    factory = _ScriptedFactory([_StubModel()])
    provider = FasterWhisperSpeechProvider(model_size="small", model_factory=factory)

    await provider.transcribe(str(_wav_path(tmp_path)))

    assert factory.calls == ["small"]
