"""Video upload ingestion (T18): stream-to-disk, bounded size, ffprobe validation.

Uploads are untrusted data. ``save_upload``:

- never uses the user-supplied filename; the target is always
  ``WORKDIR/{ver_id}/original.mp4`` where ``ver_id`` must match the generated
  ``ver_<uuid4 hex>`` format (``new_verification_id()``) — user input can
  never become a path component;
- streams the upload in bounded chunks and rejects once the byte count
  exceeds ``max_video_size_mb`` instead of buffering the whole file;
- validates the MP4 container magic (``ftyp``) and then a fixed-argv
  ``ffprobe`` call (no shell, the path is a generated file we own): the file
  must have at least one video stream and stay within
  ``max_video_duration_sec``.

Every reject path removes the partial per-verification directory.
"""

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from backend.config import Settings

_VER_ID_RE = re.compile(r"ver_[0-9a-f]{32}\Z")
_READ_CHUNK_BYTES = 1024 * 1024  # bounded read per iteration
_PROBE_TIMEOUT_SEC = 15  # never hang a worker on a pathological container


class IngestionError(Exception):
    """Base class for deterministic upload failures."""


class UnsafeVerificationIdError(IngestionError):
    """``ver_id`` is not the generated ``ver_<uuid4 hex>`` format."""


class UploadTooLargeError(IngestionError):
    """Upload exceeded ``max_video_size_mb`` mid-stream."""


class InvalidVideoError(IngestionError):
    """Not an MP4 (magic), no video stream, or longer than the duration limit."""


class MediaProbeUnavailableError(IngestionError):
    """ffprobe is not installed; validation cannot run — fail closed."""


def new_verification_id() -> str:
    """Return a fresh generated identifier: ``ver_<uuid4 hex>``."""
    return f"ver_{uuid.uuid4().hex}"


def _probe_ffprobe(path: Path, settings: Settings) -> dict:
    """Run ffprobe with a fixed argv against ``path``; fail closed on any error."""
    if shutil.which("ffprobe") is None:
        raise MediaProbeUnavailableError("ffprobe not found on PATH; cannot validate video")
    result = subprocess.run(
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
        capture_output=True,
        timeout=_PROBE_TIMEOUT_SEC,
        check=False,
    )
    if result.returncode != 0:
        raise InvalidVideoError(
            f"ffprobe cannot read upload as media: {result.stderr.decode(errors='replace')[:300]}"
        )
    return json.loads(result.stdout)


def _validate_video(dest: Path, settings: Settings) -> None:
    """Container magic + ffprobe validation shared by upload and remote fetch."""
    with dest.open("rb") as handle:
        header = handle.read(12)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise InvalidVideoError("upload is not an MP4 container (missing ftyp magic)")

    probe = _probe_ffprobe(dest, settings)
    streams = probe.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise InvalidVideoError("upload has no video stream (audio-only file)")
    duration = float(probe.get("format", {}).get("duration") or 0.0)
    if duration > settings.max_video_duration_sec:
        raise InvalidVideoError(
            f"video duration {duration:.2f}s exceeds MAX_VIDEO_DURATION_SEC="
            f"{settings.max_video_duration_sec}"
        )


def save_upload(file, ver_id: str, settings: Settings | None = None) -> Path:
    """Stream ``file`` to ``WORKDIR/{ver_id}/original.mp4`` and validate it.

    ``file`` must be a synchronous file-like object exposing ``read(size) -> bytes``
    (e.g. ``SpooledTemporaryFile`` or an opened file handle); async uploads must
    be adapted by the caller. Returns the validated saved path.
    """
    if settings is None:
        settings = Settings()
    if _VER_ID_RE.fullmatch(ver_id) is None:
        raise UnsafeVerificationIdError(
            f"unsafe verification id {ver_id!r}; expected generated 'ver_<uuid4 hex>'"
        )

    dest_dir = Path(settings.workdir) / ver_id
    dest = dest_dir / "original.mp4"
    dest_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = settings.max_video_size_mb * 1024 * 1024
    total = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = file.read(_READ_CHUNK_BYTES)
                if chunk is None or chunk == b"":
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadTooLargeError(
                        f"upload exceeds MAX_VIDEO_SIZE_MB={settings.max_video_size_mb}"
                    )
                out.write(chunk)

        # Container magic: MP4 starts with a 4-byte big-endian size + "ftyp".
        # Read exactly the 12-byte header; never load the whole file.
        _validate_video(dest, settings)
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)  # ponytail: best-effort cleanup
        raise
    return dest


class RemoteVideoFetchError(IngestionError):
    """The remote URL failed to fetch or was not an MP4 video."""


async def save_remote_video(
    url: str,
    ver_id: str,
    settings: Settings | None = None,
    *,
    fetch=None,
    timeout_sec: float = 60.0,
) -> Path:
    """SSRF-guarded download of a direct video URL into ``WORKDIR/{ver_id}/original.mp4``.

    Same trust boundary as ``save_upload``: the client URL can never become a
    path or shell argument; the file is written to the generated per-verification
    directory, capped at ``max_video_size_mb``, then validated by the same
    magic + ffprobe checks. ``fetch`` defaults to ``utils.fetch.safe_fetch``
    (http/https only, public-IP only); tests inject a fake. YouTube/TikTok
    pages are HTML, not videos, so ffprobe rejects them — only direct media
    links (e.g. ``*.mp4``) work.
    """
    from backend.utils.fetch import safe_fetch  # local import: avoids import cycle
    from backend.utils.fetch import SafeFetchResult

    if settings is None:
        settings = Settings()
    if _VER_ID_RE.fullmatch(ver_id) is None:
        raise UnsafeVerificationIdError(
            f"unsafe verification id {ver_id!r}; expected generated 'ver_<uuid4 hex>'"
        )

    fetcher = fetch or safe_fetch
    max_bytes = settings.max_video_size_mb * 1024 * 1024

    dest_dir = Path(settings.workdir) / ver_id
    dest = dest_dir / "original.mp4"
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        result: SafeFetchResult = await fetcher(url, timeout=timeout_sec, max_bytes=max_bytes)
        if result.status < 200 or result.status >= 300:
            raise RemoteVideoFetchError(
                f"video URL returned HTTP {result.status} (expected 2xx)"
            )
        if result.truncated:
            raise RemoteVideoFetchError(
                f"video URL exceeds MAX_VIDEO_SIZE_MB={settings.max_video_size_mb}"
            )
        with dest.open("wb") as out:
            out.write(result.body)
        _validate_video(dest, settings)
    except IngestionError:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    except Exception as exc:  # UnsafeURLError, FetchError, httpx errors, etc.
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise RemoteVideoFetchError(f"could not fetch video URL: {exc}") from exc
    return dest
