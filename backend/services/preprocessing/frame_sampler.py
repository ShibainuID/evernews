"""OCR frame sampling (T20): deterministic ~1 fps frame set for OCR extraction.

``sample_ocr_frames`` writes at most 15 jpgs (``-frames:v 15`` cap) under
``WORKDIR/{ver_id}/ocr_frames/`` using the fps filter (1/s, aligned at t=0),
so paths are deterministic and bounded for a given input. Same trust boundary
as keyframes.py: safe ``ver_id`` first, fixed argv, dir wiped on failure.
"""

import shutil
from pathlib import Path

from backend.config import Settings
from backend.schemas.evidence import OCRFrameRef
from backend.services.preprocessing.ffmpeg import run_ffmpeg
from backend.services.preprocessing.keyframes import _require_tools, _safe_ver_id

_MAX_OCR_FRAMES = 15
_OCR_RATE = 1


def sample_ocr_frames(path: Path | str, ver_id: str, settings: Settings | None = None) -> list[str]:
    """Sample ~1 fps OCR frames into ``WORKDIR/{ver_id}/ocr_frames/``.

    Returns the frame paths in increasing timestamp order; on any failure the
    ``ocr_frames/`` dir is removed and the error propagates.
    """
    if settings is None:
        settings = Settings()
    _safe_ver_id(ver_id)
    _require_tools()

    ocr_dir = Path(settings.workdir) / ver_id / "ocr_frames"
    try:
        shutil.rmtree(ocr_dir, ignore_errors=True)  # wipe stale partials before writing
        ocr_dir.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                f"fps={_OCR_RATE},scale=720:-2",
                "-fps_mode",
                "vfr",
                "-q:v",
                "3",
                "-frames:v",
                str(_MAX_OCR_FRAMES),
                "-f",
                "image2",
                str(ocr_dir / "frame_%02d.jpg"),
            ]
        )
        return [str(p) for p in sorted(ocr_dir.glob("frame_*.jpg"))]
    except Exception:
        shutil.rmtree(ocr_dir, ignore_errors=True)
        raise


def ocr_frame_refs(path: Path | str, ver_id: str, settings: Settings | None = None) -> list[OCRFrameRef]:
    """Sample OCR frames and bind each to its contract timestamp.

    The fps=1 filter emits frame i at t = i / rate from t=0, so the sample
    time is derived from the same ``_OCR_RATE`` constant the ffmpeg filter
    uses — never from the visual-keyframe list or a bare list index.
    """
    return [
        OCRFrameRef(local_path=frame_path, timestamp_sec=round(i / _OCR_RATE, 3))
        for i, frame_path in enumerate(sample_ocr_frames(path, ver_id, settings))
    ]
