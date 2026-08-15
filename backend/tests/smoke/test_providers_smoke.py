"""Provider smoke tests (T24/T25/T26): real faster-whisper transcription,
PaddleOCR extraction, and a real GPT-5.6 Luna visual call, opt-in only.

Skipped unless ``RUN_PROVIDER_SMOKE=1``. May download model weights to the
user cache on first run; weights are never committed. The Luna test also
requires ``OPENCODE_GO_API_KEY`` (never committed, never logged).
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from backend.providers.luna import OpenCodeGoLunaProvider
from backend.providers.whisper import FasterWhisperSpeechProvider
from backend.schemas.context import SpeechExtraction, VisualObservation
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


def _make_text_image(tmp_path: Path) -> Path:
    """White 1280x720 JPEG with large bold "JAKARTA" centered (Pillow/DejaVu)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(img)
    font: Any = ImageFont.load_default()
    dejavu_bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if dejavu_bold.is_file():
        font = ImageFont.truetype(str(dejavu_bold), 140)
    text = "JAKARTA"
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((img.width - (box[2] - box[0])) / 2, (img.height - (box[3] - box[1])) / 2),
        text,
        fill="black",
        font=font,
    )
    out = tmp_path / "ocr_text.jpg"
    img.save(out, "JPEG")
    return out


def test_paddleocr_extracts_text_from_synthetic_image(tmp_path):
    """Real PaddleOCR over one big-text image; at least one valid OCRHit.

    Runs in a fresh subprocess: faster-whisper's ctranslate2 and PaddlePaddle
    crash each other (C++ symbol clash, SIGSEGV) when both are used in one
    process, so this provider check is isolated exactly like a service that
    would run heavy providers in separate workers.
    """
    image = _make_text_image(tmp_path)
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from backend.providers.paddleocr import PaddleOCRProvider\n"
        "hits = PaddleOCRProvider().extract([%r])\n"
        "assert len(hits) >= 1, hits\n"
        "for h in hits:\n"
        "    assert isinstance(h.text, str) and h.text.strip()\n"
        "    assert isinstance(h.confidence, float)\n"
        "    assert h.frame_id == %r\n"
        "print('OK', len(hits))"
    ) % (
        str(Path(__file__).resolve().parents[2]),
        str(image),
        image.stem,
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(
    os.environ.get("OPENCODE_GO_API_KEY", "") == "",
    reason="opt-in: needs OPENCODE_GO_API_KEY (never committed)",
)
async def test_luna_visual_observation_over_real_keyframe(tmp_path):
    """Real GPT-5.6 Luna call over one synthetic keyframe (HANDOFF §5.3, §41).

    The provider reads the key and base URL from Settings, so this only runs
    when the credential env var is present; the key itself is never logged.
    """
    image = _make_text_image(tmp_path)
    provider = OpenCodeGoLunaProvider()

    observation = await provider.structured(
        "Describe the observable scene in this keyframe. Return JSON only.",
        VisualObservation,
        image_paths=[str(image)],
    )

    assert isinstance(observation, VisualObservation)
    assert isinstance(observation.scene_type, str) or observation.scene_type is None
    assert isinstance(observation.objects, list)
