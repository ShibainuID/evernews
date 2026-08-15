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


def save_upload(file, ver_id: str, settings: Settings | None = None) -> Path:
    """Stream ``file`` to ``WORKDIR/{ver_id}/original.mp4`` and validate it.

    ``file`` must expose ``read(size) -> bytes`` (sync or async; async reads
    are awaited via the running loop — do not pass a raw coroutine from an
    event loop you don't control). Returns the validated saved path.
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
        header = dest.read_bytes()[:12]
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
    except Exception:
        shutil.rmtree(dest_dir, ignore_errors=True)  # ponytail: best-effort cleanup
        raise
    return dest
