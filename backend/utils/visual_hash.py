"""Average-hash visual similarity (T27): ffmpeg 16x16 gray -> stdlib bits -> Hamming.

``frame_average_hash`` reads a frame as 16x16 8-bit gray via a fixed-argv
ffmpeg invocation (``-vf scale=16:16,format=gray -f rawvideo -``, no shell,
no user-derived flags) and thresholds each pixel against the mean to build a
256-bit integer hash. ``hamming_distance`` is the bit difference between two
hashes: 0 = identical, ~128 = unrelated.

No image libraries (Pillow/OpenCV/numpy) — ffmpeg is the repo's system media
tool and the mean/bit packing is pure stdlib.
"""

import subprocess
from pathlib import Path

_HASH_SIZE = 16
_BITS = _HASH_SIZE * _HASH_SIZE  # 256 gray pixels
_HASH_TIMEOUT_SEC = 30  # bounded: a pathological frame must not hang a worker


def frame_average_hash(path: str | Path, timeout_sec: int = _HASH_TIMEOUT_SEC) -> int:
    """Average hash of a frame: 256 bits, one per 16x16 gray pixel >= mean."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                f"scale={_HASH_SIZE}:{_HASH_SIZE},format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg hash timed out after {timeout_sec}s on {path}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace")[:300]
        raise RuntimeError(f"ffmpeg cannot read frame {path}: {detail}")
    pixels = result.stdout
    if len(pixels) != _BITS:
        raise RuntimeError(f"ffmpeg returned {len(pixels)} gray bytes for {path}, expected {_BITS}")
    mean = sum(pixels) / _BITS
    hash_value = 0
    for pixel in pixels:
        hash_value = (hash_value << 1) | (1 if pixel >= mean else 0)
    return hash_value


def hamming_distance(a: int, b: int) -> int:
    """Number of differing bits between two hashes (0 = same image)."""
    return (a ^ b).bit_count()
