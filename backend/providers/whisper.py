"""Faster-whisper speech provider (T24): ``SpeechProvider`` adapter (HANDOFF §5.1).

The model is loaded lazily on the first real transcription (deferred import so
unit tests never touch the SDK and weights only download when transcribing);
T19's no-audio artifact — a 44-byte header-only / zero-frame WAV — short-circuits
to an empty ``SpeechExtraction`` before any model load. Model-load and
transcription failures retry exactly once with a fresh model, then raise
``SpeechTranscriptionError``. Transcription runs in a worker thread so the
event loop is not blocked.
"""

import asyncio
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.schemas.context import SpeechExtraction


class SpeechTranscriptionError(Exception):
    """Deterministic speech transcription failure; message names the cause."""


def _default_model_factory(model_size: str) -> Any:
    # Deferred import: faster-whisper is a heavy SDK and unit tests inject a stub.
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def _has_no_audio_frames(path: Path) -> bool:
    """True for T19's no-audio artifact: header-only / zero-frame WAV."""
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() == 0


class FasterWhisperSpeechProvider:
    """Lazy faster-whisper adapter: no model exists until a real transcription."""

    def __init__(
        self,
        model_size: str | None = None,
        model_factory: Callable[[str], Any] | None = None,
    ):
        self._model_size = model_size if model_size is not None else Settings().whisper_model_size
        self._model_factory = model_factory or _default_model_factory
        self._model: Any = None

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        path = Path(audio_path)
        if not path.is_file():
            raise SpeechTranscriptionError(f"audio file not found: {audio_path}")
        try:
            if _has_no_audio_frames(path):
                return SpeechExtraction(transcript="")
        except wave.Error as exc:
            raise SpeechTranscriptionError(f"audio file is not a readable WAV: {audio_path}") from exc
        return await asyncio.to_thread(self._transcribe_with_retry, str(path))

    def _transcribe_with_retry(self, audio_path: str) -> SpeechExtraction:
        try:
            return self._transcribe_once(audio_path)
        except Exception:
            # Drop the failed cached model so a transient load can recover, then
            # retry exactly once; never loop.
            self._model = None
            try:
                return self._transcribe_once(audio_path)
            except Exception as retry_exc:
                raise SpeechTranscriptionError(
                    f"whisper transcription failed for {audio_path}: {retry_exc}"
                ) from retry_exc

    def _transcribe_once(self, audio_path: str) -> SpeechExtraction:
        if self._model is None:
            self._model = self._model_factory(self._model_size)
        segments, info = self._model.transcribe(audio_path)
        parts: list[str] = []
        segment_dicts: list[dict] = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            parts.append(text)
            segment_dicts.append({"start": segment.start, "end": segment.end, "text": text})
        return SpeechExtraction(
            transcript=" ".join(parts),
            language=info.language,
            segments=segment_dicts,
            confidence=info.language_probability,
        )
