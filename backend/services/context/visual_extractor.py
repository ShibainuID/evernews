"""Visual context extraction (T22): keyframes -> merged VisualObservation (Luna).

One bounded Luna call per keyframe with ``image_paths=[keyframe.local_path]``.
A failing keyframe is retried once, then skipped; the remaining frames still
continue. If every frame fails, ``VisualExtractionError`` is raised so the
pipeline can mark the branch as failed instead of fabricating observations.

Hallucination safety: per-frame output is validated against a local strict
``VisualObservation`` subclass (``extra="forbid"``) before merging, so a
guessed extra field such as ``location`` is rejected by the provider's
structured-output repair discipline. The shared schema is untouched.
"""

from pathlib import Path

from pydantic import ConfigDict

from backend.providers.base import LunaProvider
from backend.schemas.context import VisualObservation
from backend.schemas.evidence import KeyframeRef

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "visual_context.txt"
PROMPT = PROMPT_PATH.read_text()

_MAX_ATTEMPTS_PER_FRAME = 2  # one initial call plus one retry


class VisualExtractionError(Exception):
    """No frame yielded a valid observation after bounded retries."""


class _StrictVisualObservation(VisualObservation):
    """Hallucination guard: reject any extra JSON field (e.g. a guessed ``location``)."""

    model_config = ConfigDict(extra="forbid")


def _dedup_ordered(items: list[str]) -> list[str]:
    """De-duplicate ``items`` preserving first occurrence order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _merge(pairs: list[tuple[KeyframeRef, VisualObservation]]) -> VisualObservation:
    """Deterministic merge across frames.

    First non-empty ``scene_type`` wins; every list field is the ordered,
    de-duplicated concatenation in frame order; ``evidence_frames`` keeps all
    returned frame IDs with the current keyframe ID guaranteed present.
    """
    merged = VisualObservation()
    for keyframe, obs in pairs:
        if merged.scene_type is None and obs.scene_type is not None:
            merged.scene_type = obs.scene_type
        for field in VisualObservation.model_fields:
            values = getattr(obs, field)
            if isinstance(values, list):
                setattr(merged, field, _dedup_ordered(getattr(merged, field) + values))
        for frame_id, notes in obs.evidence_frames.items():
            merged.evidence_frames[frame_id] = _dedup_ordered(
                merged.evidence_frames.get(frame_id, []) + notes
            )
        merged.evidence_frames.setdefault(keyframe.frame_id, [])
    return merged


async def extract(
    keyframes: list[KeyframeRef], luna_provider: LunaProvider
) -> VisualObservation:
    """Extract per-keyframe observations via Luna and merge them.

    Per keyframe: at most ``_MAX_ATTEMPTS_PER_FRAME`` calls (initial + one
    retry); a keyframe that still fails is skipped and remaining frames are
    processed. Raises ``VisualExtractionError`` if no frame succeeded.
    """
    extracted: list[tuple[KeyframeRef, VisualObservation]] = []
    for keyframe in keyframes:
        observation: VisualObservation | None = None
        for _ in range(_MAX_ATTEMPTS_PER_FRAME):
            try:
                observation = await luna_provider.structured(
                    PROMPT, _StrictVisualObservation, image_paths=[keyframe.local_path]
                )
                break
            except Exception:
                continue  # call failed; retry once, then skip the keyframe
        if observation is not None:
            extracted.append((keyframe, observation))
    if not extracted:
        raise VisualExtractionError(
            "every keyframe failed after bounded retries; no observations to merge"
        )
    return _merge(extracted)
