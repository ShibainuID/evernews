"""T18 video ingestion: save_upload validation + reject paths (TDD red/green).

Uses synthetic ffmpeg fixtures from ``backend.tests.fixtures.video_factory``;
every test that needs real media skips (with a clear reason) when ffmpeg or
ffprobe are not installed. No test ever commits media: everything lives under
the pytest tmp dir.
"""

from pathlib import Path

import pytest

from backend.config import Settings
from backend.services.ingestion.video_ingestor import (
    IngestionError,
    InvalidVideoError,
    UploadTooLargeError,
    UnsafeVerificationIdError,
    new_verification_id,
    save_upload,
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
    """Isolated WORKDIR + tuned-down size limit; default settings otherwise."""
    monkeypatch.delenv("WORKDIR", raising=False)
    settings = Settings(workdir=str(tmp_path / "work"))
    settings.max_video_size_mb = 1  # keep the oversized test fast (1 MiB cap)
    return settings


class FakeUpload:
    """Minimal sync file-like upload (Starlette UploadFile exposes .read(size))."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            data, self._data = self._data, b""
            return data
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


def test_new_verification_id_matches_generated_format():
    assert new_verification_id() != new_verification_id()


def test_save_upload_valid_video_uses_generated_filename(settings, tmp_path):
    video = make_video(tmp_path)

    saved = save_upload(FakeUpload(video.read_bytes()), new_verification_id(), settings=settings)

    assert saved == Path(settings.workdir) / saved.parent.name / "original.mp4"
    assert saved.parent.name.startswith("ver_")
    assert len(saved.parent.name) == 4 + 32  # "ver_" + uuid4 hex
    assert saved.exists() and saved.stat().st_size > 0
    assert saved.read_bytes() == video.read_bytes()


def test_save_upload_never_uses_user_filename(settings, tmp_path):
    video = make_video(tmp_path)

    saved = save_upload(FakeUpload(video.read_bytes()), new_verification_id(), settings=settings)

    assert saved.name == "original.mp4"
    assert saved.parent.name.startswith("ver_")


def test_save_upload_rejects_unsafe_ver_id(settings):
    for bad in ("../../etc/passwd", "ver_id", "ver_", "ver_zzzz", "ver_" + "f" * 31, "VER_" + "f" * 32):
        with pytest.raises(UnsafeVerificationIdError):
            save_upload(FakeUpload(b"x" * 64), bad, settings=settings)


def test_save_upload_rejects_oversized_upload(settings):
    big = b"\x00\x00\x00\x18ftypisom" + b"x" * (settings.max_video_size_mb * 1024 * 1024)
    ver_id = new_verification_id()

    with pytest.raises(UploadTooLargeError):
        save_upload(FakeUpload(big), ver_id, settings=settings)

    # partial artifact removed
    assert not (Path(settings.workdir) / ver_id).exists()


def test_save_upload_rejects_corrupt_or_text_upload(settings, tmp_path):
    corrupt = make_corrupt_mp4(tmp_path)
    ver_id = new_verification_id()

    with pytest.raises(InvalidVideoError):
        save_upload(FakeUpload(corrupt.read_bytes()), ver_id, settings=settings)

    assert not (Path(settings.workdir) / ver_id).exists()


def test_save_upload_rejects_video_longer_than_limit(settings, tmp_path):
    video_20s = make_video_20s(tmp_path)
    ver_id = new_verification_id()

    with pytest.raises(IngestionError, match="15"):
        save_upload(FakeUpload(video_20s.read_bytes()), ver_id, settings=settings)

    assert not (Path(settings.workdir) / ver_id).exists()


def test_save_upload_rejects_audio_only_file(settings, tmp_path):
    m4a = make_audio_only_m4a(tmp_path)
    ver_id = new_verification_id()

    with pytest.raises(InvalidVideoError, match="video"):
        save_upload(FakeUpload(m4a.read_bytes()), ver_id, settings=settings)

    assert not (Path(settings.workdir) / ver_id).exists()


def test_save_upload_accepts_video_without_audio(settings, tmp_path):
    video_no_audio = make_video_no_audio(tmp_path)

    saved = save_upload(
        FakeUpload(video_no_audio.read_bytes()), new_verification_id(), settings=settings
    )

    assert saved.exists() and saved.stat().st_size > 0
