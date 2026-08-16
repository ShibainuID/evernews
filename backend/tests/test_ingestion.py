"""T18 video ingestion: save_upload validation + reject paths (TDD red/green).

Uses synthetic ffmpeg fixtures from ``backend.tests.fixtures.video_factory``;
every test that needs real media skips (with a clear reason) when ffmpeg or
ffprobe are not installed. No test ever commits media: everything lives under
the pytest tmp dir.
"""

from pathlib import Path

import asyncio
import types

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


def test_magic_validation_reads_only_header_not_whole_file(settings, tmp_path, monkeypatch):
    """F18-1 regression: magic check must open the file and read 12 bytes, never read_bytes()."""
    from backend.services.ingestion import video_ingestor

    video = make_video(tmp_path)
    # Read the fixture bytes BEFORE patching: the patch applies to every Path.
    payload = video.read_bytes()

    # Any full-file read (the old dest.read_bytes()) fails this test loudly.
    def _no_full_read(self):
        raise AssertionError("magic validation must not read_bytes() the whole upload")

    monkeypatch.setattr(video_ingestor.Path, "read_bytes", _no_full_read)

    saved = save_upload(FakeUpload(payload), new_verification_id(), settings=settings)

    assert saved.exists() and saved.stat().st_size > 0


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


def test_save_upload_accepts_jpeg_image_without_ffprobe(settings):
    payload = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 128
    ver_id = new_verification_id()

    saved = save_upload(FakeUpload(payload), ver_id, settings=settings)

    assert saved.name == "original.jpg"
    assert saved.parent.name == ver_id
    assert saved.read_bytes() == payload


def test_save_upload_accepts_png_image_without_ffprobe(settings):
    payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128

    saved = save_upload(FakeUpload(payload), new_verification_id(), settings=settings)

    assert saved.name == "original.png"
    assert saved.exists() and saved.stat().st_size > 0


def test_save_upload_accepts_webp_image(settings):
    payload = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 128

    saved = save_upload(FakeUpload(payload), new_verification_id(), settings=settings)

    assert saved.name == "original.webp"
    assert saved.exists()


def test_save_upload_rejects_text_not_matching_image_or_video_magic(settings):
    ver_id = new_verification_id()

    with pytest.raises(InvalidVideoError):
        save_upload(FakeUpload(b"just some plain text here"), ver_id, settings=settings)

    assert not (Path(settings.workdir) / ver_id).exists()


async def _fake_fetch(result):
    async def fetcher(url, *, timeout, max_bytes):
        return result
    return fetcher


def test_save_remote_video_writes_and_validates(settings, tmp_path):
    from backend.services.ingestion.video_ingestor import save_remote_video

    video = make_video(tmp_path)
    ver_id = new_verification_id()
    fetcher = asyncio.run(_fake_fetch(types.SimpleNamespace(status=200, truncated=False, body=video.read_bytes())))

    saved = asyncio.run(save_remote_video("https://example.com/clip.mp4", ver_id, settings=settings, fetch=fetcher))

    assert saved == Path(settings.workdir) / ver_id / "original.mp4"
    assert saved.read_bytes() == video.read_bytes()


def test_save_remote_video_rejects_http_error(settings):
    from backend.services.ingestion.video_ingestor import RemoteVideoFetchError, save_remote_video

    fetcher = asyncio.run(_fake_fetch(types.SimpleNamespace(status=404, truncated=False, body=b"")))

    with pytest.raises(RemoteVideoFetchError, match="404"):
        asyncio.run(save_remote_video("https://example.com/missing.mp4", new_verification_id(), settings=settings, fetch=fetcher))


def test_save_remote_video_rejects_truncated_body(settings):
    from backend.services.ingestion.video_ingestor import RemoteVideoFetchError, save_remote_video

    fetcher = asyncio.run(_fake_fetch(types.SimpleNamespace(status=200, truncated=True, body=b"x")))

    with pytest.raises(RemoteVideoFetchError, match="MAX_VIDEO_SIZE_MB"):
        asyncio.run(save_remote_video("https://example.com/big.mp4", new_verification_id(), settings=settings, fetch=fetcher))


def test_save_remote_video_rejects_non_video_body(settings, tmp_path):
    from backend.services.ingestion.video_ingestor import save_remote_video

    corrupt = make_corrupt_mp4(tmp_path)
    ver_id = new_verification_id()
    fetcher = asyncio.run(_fake_fetch(types.SimpleNamespace(status=200, truncated=False, body=corrupt.read_bytes())))

    with pytest.raises(InvalidVideoError):
        asyncio.run(save_remote_video("https://example.com/not-video.mp4", ver_id, settings=settings, fetch=fetcher))

    assert not (Path(settings.workdir) / ver_id).exists()


def test_save_remote_video_wraps_fetch_exception(settings):
    from backend.services.ingestion.video_ingestor import RemoteVideoFetchError, save_remote_video

    async def boom(url, *, timeout, max_bytes):
        raise RuntimeError("connection refused")

    with pytest.raises(RemoteVideoFetchError, match="connection refused"):
        asyncio.run(save_remote_video("https://example.com/x.mp4", new_verification_id(), settings=settings, fetch=boom))
