"""Visual keyframe selection (T20): scene-change keyframes + uniform fallback.

``select_keyframes`` is the trusted boundary between T19's ``normalized.mp4``
and the visual/OCR extractors (T22+). Same safety rules as T19: ``ver_id``
must fullmatch T18's generated-id rule before any filesystem/subprocess work,
every call is fixed-argv (no shell, no user-derived flags), and the
``keyframes/`` dir is wiped on failure (T19-owned artifacts are never
touched). Near-duplicate filtering runs behind an injected helper boundary
(``is_duplicate``); the default is a file-size proxy — T27's
``backend/utils/visual_hash.py`` replaces it later.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from backend.config import Settings
from backend.schemas.evidence import KeyframeRef
from backend.services.ingestion.video_ingestor import _VER_ID_RE
from backend.services.preprocessing.ffmpeg import PreprocessingError, run_ffmpeg

_PROBE_TIMEOUT_SEC = 15
_MIN_KEYFRAMES = 3
_MAX_KEYFRAMES = 6
SCENE_THRESHOLD = 0.3
SCENE_SELECT = f"select='gt(scene,{SCENE_THRESHOLD})'"
SCENE_REASON = "scene change detected (score > 0.3)"
FALLBACK_REASON = "uniform fallback sample (scene detection yielded < 3 frames)"
_PTS_TIME_RE = re.compile(r"pts_time:([0-9.]+)")


def _safe_ver_id(ver_id: str) -> None:
    """Reject anything that is not a generated ``ver_<uuid4 hex>`` id."""
    if _VER_ID_RE.fullmatch(ver_id) is None:
        raise PreprocessingError(
            "unsafe_ver_id", f"unsafe verification id {ver_id!r}; expected generated 'ver_<uuid4 hex>'"
        )


def _require_tools() -> None:
    missing = [tool for tool in ("ffprobe", "ffmpeg") if shutil.which(tool) is None]
    if missing:
        raise PreprocessingError(
            "media_tool_unavailable", f"media tools not found on PATH: {', '.join(missing)}"
        )


def _probe_duration(path: Path) -> float:
    """ffprobe duration in seconds; any failure maps to ``undecodable``."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_entries",
                "format=duration",
                str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreprocessingError("undecodable", "ffprobe timed out reading input as media") from exc
    if result.returncode != 0:
        raise PreprocessingError("undecodable", "ffprobe cannot read input as media")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise PreprocessingError("undecodable", "ffprobe returned no usable duration") from exc


def _default_is_duplicate(candidate: Path, selected: list[Path]) -> bool:
    # ponytail: file-size proxy — byte-identical encodes share size. Ceiling: a
    # size collision can hide a real difference; T27's visual_hash replaces it.
    return any(candidate.stat().st_size == other.stat().st_size for other in selected)


def _scene_timestamps(path: Path) -> list[float]:
    """Selected scene frames' presentation times, in order, from showinfo."""
    result = run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "info",
            "-i",
            str(path),
            "-vf",
            f"{SCENE_SELECT},showinfo",
            "-f",
            "null",
            "-",
        ]
    )
    timestamps: list[float] = []
    for line in result.stderr.decode(errors="replace").splitlines():
        if "Parsed_showinfo" in line and "pts_time:" in line:
            match = _PTS_TIME_RE.search(line)
            if match is not None:
                timestamps.append(float(match.group(1)))
    return timestamps


def _extract_scene_frames(path: Path, out_dir: Path) -> list[Path]:
    """Second pass with the identical select filter; frame i maps to pts i."""
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"{SCENE_SELECT},scale=720:-2",
            "-fps_mode",
            "vfr",
            "-q:v",
            "3",
            "-f",
            "image2",
            str(out_dir / "frame_%03d.jpg"),
        ]
    )
    return sorted(out_dir.glob("frame_*.jpg"))


def _uniform_timestamps(duration: float, count: int) -> list[float]:
    return [i * duration / count for i in range(count)]


def _extract_uniform_frames(path: Path, out_dir: Path, duration: float, count: int) -> list[Path]:
    """fps = count/duration emits exactly count evenly spaced frames from t=0."""
    rate = f"{count / duration:.6f}"
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            f"fps={rate},scale=720:-2",
            "-fps_mode",
            "vfr",
            "-q:v",
            "3",
            "-f",
            "image2",
            str(out_dir / "frame_%03d.jpg"),
        ]
    )
    return sorted(out_dir.glob("frame_*.jpg"))


def _to_refs(ver_id: str, timestamps: list[float], frames: list[Path], reason: str) -> list[KeyframeRef]:
    return [
        KeyframeRef(
            frame_id=f"{ver_id}_kf{i:03d}",
            timestamp_sec=round(ts, 3),
            local_path=str(frame),
            selection_reason=reason,
        )
        for i, (ts, frame) in enumerate(zip(timestamps, frames))
    ]


def select_keyframes(
    path: Path | str,
    ver_id: str,
    settings: Settings | None = None,
    is_duplicate: Callable[[Path, list[Path]], bool] | None = None,
) -> list[KeyframeRef]:
    """Pick 3–6 visual keyframes into ``WORKDIR/{ver_id}/keyframes/``.

    Scene detection (``select='gt(scene,0.3)'``) is used when it yields at
    least 3 candidates after near-duplicate filtering; otherwise a
    deterministic uniform sample (capped at 6) is written instead. Frames are
    returned in increasing timestamp order; on any failure the ``keyframes/``
    dir is removed and the error propagates.
    """
    if settings is None:
        settings = Settings()
    _safe_ver_id(ver_id)
    _require_tools()
    if is_duplicate is None:
        is_duplicate = _default_is_duplicate

    src = Path(path)
    keyframe_dir = Path(settings.workdir) / ver_id / "keyframes"
    try:
        duration = _probe_duration(src)
        # wipe stale partials from a previous failed run before writing
        shutil.rmtree(keyframe_dir, ignore_errors=True)
        keyframe_dir.mkdir(parents=True, exist_ok=True)

        candidates = _scene_timestamps(src)
        if len(candidates) >= _MIN_KEYFRAMES:
            frames = _extract_scene_frames(src, keyframe_dir)
            if len(frames) != len(candidates):
                raise PreprocessingError(
                    "transcode_failed", "scene detection and extraction disagreed on frame count"
                )
            kept_ts: list[float] = []
            kept_frames: list[Path] = []
            for ts, frame in zip(candidates, frames):
                if not is_duplicate(frame, kept_frames):
                    kept_ts.append(ts)
                    kept_frames.append(frame)
            if len(kept_ts) >= _MIN_KEYFRAMES:
                return _to_refs(ver_id, kept_ts, kept_frames, SCENE_REASON)

        # fallback: scene detection yielded no/too few frames (or all were
        # near-duplicates) — deterministic uniform sampling
        count = min(_MAX_KEYFRAMES, max(_MIN_KEYFRAMES, round(duration)))
        shutil.rmtree(keyframe_dir, ignore_errors=True)  # drop scene-pass partials
        keyframe_dir.mkdir(parents=True, exist_ok=True)
        timestamps = _uniform_timestamps(duration, count)
        frames = _extract_uniform_frames(src, keyframe_dir, duration, count)
        return _to_refs(ver_id, timestamps, frames, FALLBACK_REASON)
    except Exception:
        shutil.rmtree(keyframe_dir, ignore_errors=True)
        raise
