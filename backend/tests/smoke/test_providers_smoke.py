"""Provider smoke tests (T24): real faster-whisper transcription, opt-in only.

Skipped unless ``RUN_PROVIDER_SMOKE=1``. May download model weights to the
user cache on first run; weights are never committed.
"""

import os
import subprocess
from pathlib import Path

import pytest

from backend.providers.whisper import FasterWhisperSpeechProvider
from backend.schemas.context import SpeechExtraction
from backend.tests.fixtures.video_factory import make_video, require_ffmpeg

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_PROVIDER_SMOKE") != "1",
    reason="opt-in: set RUN_PROVIDER_SMOKE=1 (may download model weights)",
)


def _extract_audio_wav(video: Path, out: Path) -> Path:
    """T19-style mono 16 kHz audio.wav extracted from the factory video."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


async def test_faster_whisper_transcribes_synthetic_audio(tmp_path):
    """Real transcription over video_factory audio; schema must validate."""
    require_ffmpeg()
    wav = _extract_audio_wav(make_video(tmp_path), tmp_path / "audio.wav")

    provider = FasterWhisperSpeechProvider()  # model size from Settings (tiny)
    result = await provider.transcribe(str(wav))

    assert isinstance(result, SpeechExtraction)
    assert isinstance(result.transcript, str)
    assert result.language is None or isinstance(result.language, str)
    assert isinstance(result.segments, list)
