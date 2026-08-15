"""Synthetic media fixtures (T18) — generated with the system ffmpeg/ffprobe.

All files are written into a caller-provided tmp dir (pytest tmp_path);
nothing is committed. Tests skip with a clear reason when ffmpeg/ffprobe are
not installed; validation itself is never faked.
"""

import shutil
import subprocess
from pathlib import Path

VIDEO_ARGS = (
    "-f",
    "lavfi",
    "-i",
    "testsrc=size=640x360:rate=24:duration=6",
    "-f",
    "lavfi",
    "-i",
    "sine=frequency=440:duration=6",
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-shortest",
)


def require_ffmpeg() -> None:
    """Skip the calling test module if ffmpeg/ffprobe are unavailable."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        import pytest

        pytest.skip(f"cannot generate fixtures: missing system tools: {', '.join(missing)}")


def _run(args: list[str], out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args, str(out)],
        check=True,
        capture_output=True,
    )


def make_video(tmp_path: Path) -> Path:
    """6s 640x360 24fps MP4 with audio (baseline fixture)."""
    out = tmp_path / "video_6s.mp4"
    _run(list(VIDEO_ARGS), out)
    return out


def make_video_no_audio(tmp_path: Path) -> Path:
    """6s 640x360 24fps MP4 without any audio stream."""
    out = tmp_path / "video_no_audio.mp4"
    _run(list(VIDEO_ARGS) + ["-an"], out)
    return out


def make_video_20s(tmp_path: Path) -> Path:
    """20s video — exceeds the 15s limit (reject path)."""
    out = tmp_path / "video_20s.mp4"
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=24:duration=20",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=20",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
        ],
        out,
    )
    return out


def make_audio_only_m4a(tmp_path: Path) -> Path:
    """Audio-only MP4/M4A — no video stream (reject path)."""
    out = tmp_path / "audio_only.m4a"
    _run(
        ["-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:a", "aac"],
        out,
    )
    return out


def make_corrupt_mp4(tmp_path: Path) -> Path:
    """File named .mp4 whose bytes are not an MP4 (reject path)."""
    out = tmp_path / "corrupt.mp4"
    out.write_bytes(b"this is definitely not an mp4 container\x00\xff")
    return out
