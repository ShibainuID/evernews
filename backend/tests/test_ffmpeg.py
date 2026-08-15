"""T19 preprocessing: ffmpeg probe, normalize, mono-16k audio (TDD red/green).

Uses the T18 synthetic fixtures (``backend.tests.fixtures.video_factory``);
the whole module skips with a clear reason when ffmpeg/ffprobe are absent.
No test ever commits media: everything lives under the pytest tmp dir.
"""

import subprocess
from pathlib import Path

import pytest

from backend.config import Settings
from backend.services.ingestion.video_ingestor import new_verification_id
from backend.services.preprocessing.ffmpeg import (
    PreprocessingArtifacts,
    PreprocessingError,
    preprocess,
)
from backend.tests.fixtures.video_factory import (
    make_audio_only_m4a,
    make_corrupt_mp4,
    make_video,
    make_video_no_audio,
    make_video_20s,
    require_ffmpeg,
)

require_ffmpeg()  # skips this whole module when ffmpeg/ffprobe are missing


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Isolated WORKDIR; default settings otherwise."""
    monkeypatch.delenv("WORKDIR", raising=False)
    return Settings(workdir=str(tmp_path / "work"))


def _probe_streams(path: Path) -> list[dict]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "stream=codec_type,codec_name,sample_rate,channels:format=duration",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    import json

    return json.loads(result.stdout)["streams"]


def test_preprocess_valid_video_normalizes_and_extracts_mono_16k(settings, tmp_path):
    ver_id = new_verification_id()
    original = make_video(tmp_path)

    artifacts = preprocess(ver_id, original, settings=settings)

    assert isinstance(artifacts, PreprocessingArtifacts)
    work_dir = Path(settings.workdir) / ver_id
    assert artifacts.normalized_path == work_dir / "normalized.mp4"
    assert artifacts.audio_path == work_dir / "audio.wav"
    assert artifacts.original_path == original
    assert artifacts.has_audio is True
    assert 5.0 <= artifacts.duration_sec <= 7.0

    # normalized.mp4 exists and is really h264
    assert artifacts.normalized_path.exists() and artifacts.normalized_path.stat().st_size > 0
    normalized_streams = _probe_streams(artifacts.normalized_path)
    assert any(
        s.get("codec_type") == "video" and s.get("codec_name") == "h264"
        for s in normalized_streams
    )

    # audio.wav is mono 16 kHz, proven by ffprobe
    assert artifacts.audio_path.exists() and artifacts.audio_path.stat().st_size > 44
    audio_streams = _probe_streams(artifacts.audio_path)
    audio = next(s for s in audio_streams if s.get("codec_type") == "audio")
    assert audio["sample_rate"] == "16000"
    assert audio["channels"] == 1


def test_preprocess_no_audio_succeeds_with_empty_wav(settings, tmp_path):
    ver_id = new_verification_id()

    artifacts = preprocess(ver_id, make_video_no_audio(tmp_path), settings=settings)

    assert artifacts.has_audio is False
    assert artifacts.normalized_path.exists()
    # "expected empty audio.wav": 44-byte header-only WAV, still mono 16 kHz
    assert artifacts.audio_path.exists()
    assert artifacts.audio_path.stat().st_size == 44
    audio_streams = _probe_streams(artifacts.audio_path)
    audio = next(s for s in audio_streams if s.get("codec_type") == "audio")
    assert audio["sample_rate"] == "16000"
    assert audio["channels"] == 1


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (make_corrupt_mp4, "undecodable"),
        (make_video_20s, "video_too_long"),
        (make_audio_only_m4a, "no_video_stream"),
    ],
)
def test_preprocess_rejects_with_stable_code(settings, tmp_path, factory, code):
    ver_id = new_verification_id()
    original = factory(tmp_path)

    with pytest.raises(PreprocessingError) as exc:
        preprocess(ver_id, original, settings=settings)

    assert exc.value.code == code
    # partial outputs cleaned; the T18-owned original is preserved
    work_dir = Path(settings.workdir) / ver_id
    assert not (work_dir / "normalized.mp4").exists()
    assert not (work_dir / "audio.wav").exists()


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "ver_id", "ver_", "ver_zzzz", "ver_" + "f" * 31, "VER_" + "f" * 32],
)
def test_preprocess_rejects_unsafe_ver_id_before_any_subprocess(settings, tmp_path, monkeypatch, bad_id):
    def _forbidden(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("unsafe ver_id must be rejected before any subprocess runs")

    # Build the fixture BEFORE patching: the factory itself runs subprocess.run.
    video = make_video(tmp_path)
    monkeypatch.setattr(subprocess, "run", _forbidden)

    with pytest.raises(PreprocessingError) as exc:
        preprocess(bad_id, video, settings=settings)

    assert exc.value.code == "unsafe_ver_id"


def test_failed_preprocess_removes_stale_partials_keeps_original(settings, tmp_path):
    ver_id = new_verification_id()
    work_dir = Path(settings.workdir) / ver_id
    work_dir.mkdir(parents=True)
    original = work_dir / "original.mp4"
    original.write_bytes(b"stale original placeholder")
    (work_dir / "normalized.mp4").write_bytes(b"stale partial")
    (work_dir / "audio.wav").write_bytes(b"stale partial")

    with pytest.raises(PreprocessingError) as exc:
        preprocess(ver_id, original, settings=settings)

    assert exc.value.code == "undecodable"
    assert original.exists()  # preprocess owns only its own artifacts
    assert not (work_dir / "normalized.mp4").exists()
    assert not (work_dir / "audio.wav").exists()


def test_all_subprocess_calls_use_fixed_argv_no_shell(settings, tmp_path, monkeypatch):
    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        calls.append((list(args), kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    ver_id = new_verification_id()
    artifacts = preprocess(ver_id, make_video(tmp_path), settings=settings)

    assert artifacts.normalized_path.exists() and artifacts.audio_path.exists()
    assert calls
    known_paths = {str(artifacts.original_path), str(artifacts.normalized_path), str(artifacts.audio_path)}
    for args, kwargs in calls:
        assert kwargs.get("shell") is not True
        for arg in args:
            assert isinstance(arg, str)
            # the only non-fixed tokens are the trusted paths; no user flags, no shell metachars
            assert arg in known_paths or not any(ch in arg for ch in (";", "&", "|", "$", "`", ".."))
