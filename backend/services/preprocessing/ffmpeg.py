"""Video preprocessing (T19): probe, normalize, mono 16 kHz audio extraction.

``preprocess`` is the trusted bridge between ingestion (T18, validated
``original.mp4``) and downstream consumers (keyframes/sampler, context
extractors). Inputs are untrusted: ``ver_id`` must match the generated
``ver_<uuid4 hex>`` format (T18's rule, reused), every ffprobe/ffmpeg call is
a fixed-argv subprocess (no shell, no user-derived flags), and uploaded media
is never executed. All failures raise ``PreprocessingError`` with a stable
machine ``code``: ``unsafe_ver_id``, ``media_tool_unavailable``,
``undecodable``, ``no_video_stream``, ``video_too_long``, ``transcode_failed``.
No audio stream is NOT an error: the no-audio path succeeds with an empty
(44-byte header-only) ``audio.wav`` and ``has_audio=False``.
"""

import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from backend.config import Settings
from backend.services.ingestion.video_ingestor import _VER_ID_RE

_PROBE_TIMEOUT_SEC = 15  # bounded probe; a pathological container must not hang a worker
_TRANSCODE_TIMEOUT_SEC = 120  # bounded transcode; 15s 640x360 input needs only seconds
_AUDIO_RATE = 16000
_AUDIO_CHANNELS = 1


class PreprocessingError(Exception):
    """Deterministic preprocessing failure carrying a stable machine ``code``."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreprocessingArtifacts:
    """Paths + facts produced by ``preprocess`` for downstream consumers."""

    ver_id: str
    original_path: Path
    normalized_path: Path
    audio_path: Path
    duration_sec: float
    has_audio: bool


def _run_media(args: list[str], timeout: int, fail_code: str, fail_prefix: str) -> subprocess.CompletedProcess:
    """Run a fixed-argv ffmpeg/ffprobe command; map timeout/non-zero exit explicitly."""
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PreprocessingError(fail_code, f"{fail_prefix}: timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:300]
        raise PreprocessingError(fail_code, f"{fail_prefix}: {detail}")
    return result


def _probe(path: Path) -> dict:
    result = _run_media(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "stream=codec_type:format=duration",
            str(path),
        ],
        _PROBE_TIMEOUT_SEC,
        "undecodable",
        "ffprobe cannot read input as media",
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PreprocessingError("undecodable", "ffprobe returned non-JSON output") from exc


def _write_empty_wav(path: Path) -> None:
    """44-byte header-only WAV: the expected 'empty audio' artifact for no-audio input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(_AUDIO_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(_AUDIO_RATE)


def preprocess(ver_id: str, original_path: Path, settings: Settings | None = None) -> PreprocessingArtifacts:
    """Probe, normalize (libx264) and extract mono 16 kHz audio from ``original_path``.

    Writes ``WORKDIR/{ver_id}/normalized.mp4`` and ``WORKDIR/{ver_id}/audio.wav``.
    Raises ``PreprocessingError`` on reject/transcode failure and removes its
    own partial artifacts; the T18-owned ``original`` is never touched.
    """
    if settings is None:
        settings = Settings()
    if _VER_ID_RE.fullmatch(ver_id) is None:
        raise PreprocessingError(
            "unsafe_ver_id", f"unsafe verification id {ver_id!r}; expected generated 'ver_<uuid4 hex>'"
        )
    missing = [tool for tool in ("ffprobe", "ffmpeg") if shutil.which(tool) is None]
    if missing:
        raise PreprocessingError(
            "media_tool_unavailable", f"media tools not found on PATH: {', '.join(missing)}"
        )

    work_dir = Path(settings.workdir) / ver_id
    normalized = work_dir / "normalized.mp4"
    audio = work_dir / "audio.wav"
    try:
        probe = _probe(Path(original_path))
        streams = probe.get("streams", [])
        if not any(s.get("codec_type") == "video" for s in streams):
            raise PreprocessingError("no_video_stream", "input has no video stream (audio-only file)")
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        duration = float(probe.get("format", {}).get("duration") or 0.0)
        if duration > settings.max_video_duration_sec:
            raise PreprocessingError(
                "video_too_long",
                f"video duration {duration:.2f}s exceeds MAX_VIDEO_DURATION_SEC="
                f"{settings.max_video_duration_sec}",
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        _run_media(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(original_path),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-shortest",
                str(normalized),
            ],
            _TRANSCODE_TIMEOUT_SEC,
            "transcode_failed",
            "video normalization failed",
        )
        if has_audio:
            _run_media(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(original_path),
                    "-vn",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    str(_AUDIO_RATE),
                    "-ac",
                    str(_AUDIO_CHANNELS),
                    str(audio),
                ],
                _TRANSCODE_TIMEOUT_SEC,
                "transcode_failed",
                "audio extraction failed",
            )
        else:
            _write_empty_wav(audio)
    except Exception:
        # preprocess owns only its artifacts; the T18-validated original is preserved
        for partial in (normalized, audio):
            partial.unlink(missing_ok=True)
        raise
    return PreprocessingArtifacts(
        ver_id=ver_id,
        original_path=Path(original_path),
        normalized_path=normalized,
        audio_path=audio,
        duration_sec=duration,
        has_audio=has_audio,
    )
