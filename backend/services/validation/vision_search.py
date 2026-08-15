"""Visual search task runner (T30): ``VisualSearchTask`` + keyframes -> candidates.

Per frame (in ``task.frame_ids`` order): reads ``KeyframeRef.local_path``
bytes, runs the sync ``VisionProvider`` in a worker thread under an 18s
per-frame bound (design §7: 15-20s/frame, mirroring the provider's API
timeout). A frame that has no ref, cannot be read, times out, or fails at
the provider is skipped so sibling frames still produce results; only when
*every* requested frame fails does the branch raise (T32 turns that into
``branch_status`` + demo-index fallback). A successful zero-match frame
yields ``[]`` — origin unknown, never a fake-footage verdict
(``VisualWebCandidate`` has no truth/falsity fields).

Candidates are merged across frames and deduplicated by canonical URL
(``utils/urls.py``), keeping the strongest category:
full > partial > page > visually_similar; equal strength keeps the first
occurrence. ``visually_similar`` is discovery only, not proof of same
footage.
"""

import asyncio
from hashlib import sha256

from backend.providers.base import VisionProvider
from backend.providers.google_vision import GoogleVisionProvider
from backend.schemas.evidence import KeyframeRef
from backend.schemas.investigation import VisualSearchTask, VisualWebCandidate
from backend.utils.urls import canonicalize

# design §7: 15-20s per frame; must match the provider's API timeout.
_FRAME_TIMEOUT_SEC = 18.0

# normalized candidate_type -> strength (higher wins the dedupe)
_STRENGTH = {
    "full_image_match": 3,
    "partial_image_match": 2,
    "page_match": 1,
    "visually_similar": 0,
}


def _candidate_id(url: str) -> str:
    return "vw_" + sha256(canonicalize(url).encode()).hexdigest()[:12]


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _dedupe(candidates: list[VisualWebCandidate]) -> list[VisualWebCandidate]:
    """Merge by canonical URL, keeping the strongest category per URL."""
    best: dict[str, VisualWebCandidate] = {}
    for candidate in candidates:
        key = canonicalize(candidate.url)
        current = best.get(key)
        if current is None or _STRENGTH[candidate.candidate_type] > _STRENGTH[
            current.candidate_type
        ]:
            best[key] = candidate
    return [
        candidate.model_copy(update={"candidate_id": _candidate_id(candidate.url)})
        for candidate in best.values()
    ]


async def run_visual_task(
    task: VisualSearchTask,
    keyframes: list[KeyframeRef],
    provider: VisionProvider | None = None,
) -> list[VisualWebCandidate]:
    """Search every requested frame; merge and dedupe by canonical URL.

    ``provider`` defaults to a lazily-created ``GoogleVisionProvider``; the
    seam exists only for deterministic fake-client tests (no ADC/network).
    """
    vision = provider if provider is not None else GoogleVisionProvider()
    by_id = {keyframe.frame_id: keyframe for keyframe in keyframes}
    merged: list[VisualWebCandidate] = []
    errors: list[Exception] = []
    succeeded = 0
    for frame_id in task.frame_ids:
        keyframe = by_id.get(frame_id)
        if keyframe is None:
            errors.append(KeyError(f"no KeyframeRef for frame {frame_id}"))
            continue
        try:
            content = await asyncio.to_thread(_read_bytes, keyframe.local_path)
            candidates = await asyncio.wait_for(
                asyncio.to_thread(vision.web_detection, content),
                timeout=_FRAME_TIMEOUT_SEC,
            )
        except Exception as exc:  # per-frame failure: skip, keep going
            errors.append(exc)
            continue
        succeeded += 1
        for candidate in candidates[: task.max_candidates_per_frame]:
            merged.append(
                candidate.model_copy(update={"frame_id": frame_id})
            )
    if succeeded == 0 and errors:  # every requested frame failed: branch error
        raise errors[0]
    return _dedupe(merged)
