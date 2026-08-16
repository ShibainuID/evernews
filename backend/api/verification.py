"""Verification endpoint: upload + caption -> VerificationResult.

Demo-scope pipeline wired from already-tested building blocks: ingest (T18),
keyframes (T20), the committed demo source index (T27), the deterministic
comparator/classifier/confidence (T15-T17). Claim extraction uses
``caption_claims`` (keyword-based) rather than the full Luna context fuser —
see that module's docstring for the upgrade path. Runs synchronously: every
step here is local and fast enough that the frontend's "analyzing" animation
covers the real latency without a job-status endpoint.
"""

import mimetypes
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile

from backend.config import Settings
from backend.schemas.evidence import ComparisonStatus, KeyframeRef
from backend.schemas.result import SourceContext, VerificationResult
from backend.services.context.caption_claims import extract_claims
from backend.services.evidence.classification import classify
from backend.services.evidence.comparator import compare
from backend.services.evidence.confidence import evidence_confidence
from backend.services.evidence.demo_index import DemoIndex
from backend.services.evidence.normalizer import match_strength
from backend.services.evidence.source_ranker import rank
from backend.services.ingestion.video_ingestor import (
    InvalidVideoError,
    IngestionError,
    UploadTooLargeError,
    new_verification_id,
    save_upload,
)
from backend.services.preprocessing.ffmpeg import PreprocessingError
from backend.services.preprocessing.keyframes import select_keyframes

router = APIRouter(prefix="/api/v1")

_MANIPULATION_LABEL = "False Context"

# Friendly, non-technical copy for the reject paths the brief calls out
# explicitly ("without it feeling like an error message from a machine").
_FRIENDLY_UPLOAD_ERRORS: dict[type[Exception], str] = {
    InvalidVideoError: "That doesn't look like a clip we can read — try a short MP4 video, or a photo, instead.",
    UploadTooLargeError: "That's a bit too big for us to check right now — try a shorter clip or a smaller photo.",
}


def _friendly_upload_error(exc: Exception) -> str:
    for exc_type, message in _FRIENDLY_UPLOAD_ERRORS.items():
        if isinstance(exc, exc_type):
            return message
    return "We couldn't process that upload — mind trying again?"


def _save_image(file: UploadFile, ver_id: str, settings: Settings) -> KeyframeRef:
    """A single uploaded photo, treated as its own one-frame "clip".

    No ffmpeg/ffprobe involved: a still image needs no scene detection, it
    already is the one keyframe. Same bounded-write and size-limit discipline
    as ``video_ingestor.save_upload``.
    """
    ext = mimetypes.guess_extension(file.content_type or "") or ".jpg"
    dest_dir = Path(settings.workdir) / ver_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"image{ext}"
    max_bytes = settings.max_video_size_mb * 1024 * 1024
    total = 0
    with dest.open("wb") as out:
        while chunk := file.file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                dest.unlink(missing_ok=True)
                raise UploadTooLargeError(f"upload exceeds MAX_VIDEO_SIZE_MB={settings.max_video_size_mb}")
            out.write(chunk)
    return KeyframeRef(
        frame_id=f"{ver_id}_kf000",
        timestamp_sec=0.0,
        local_path=str(dest),
        selection_reason="single uploaded photo, no keyframe extraction needed",
    )


def _source_context(candidate) -> SourceContext:
    return SourceContext(
        event=candidate.event,
        location=candidate.location,
        date=candidate.time_context or candidate.published_at,
        publisher=candidate.publisher,
        source_url=candidate.url,
        title=candidate.title,
    )


def _headline(classification: str) -> str:
    return {
        "possible_false_context": "Possible Context Change",
        "context_consistent_with_source": "Context Checks Out",
        "claim_conflict_found": "Claim Doesn't Match What We Found",
        "source_match_with_incomplete_context": "Footage Match Found, Context Incomplete",
        "insufficient_evidence": "We're Not Sure Yet",
    }[classification]


def _summary(classification: str, comparison, source: SourceContext | None) -> str:
    if source is None:
        return "We couldn't find a confident earlier match for this footage, so there's nothing to compare it against yet."
    mismatches = [
        dim for dim, comp in (("event", comparison.event), ("location", comparison.location), ("date", comparison.date))
        if comp.status is ComparisonStatus.MISMATCH
    ]
    if mismatches:
        return (
            f"The footage matches a source published by {source.publisher or 'an earlier source'} "
            f"about {source.event or 'a different event'} in {source.location or 'a different place'} "
            f"({source.date or 'an earlier date'}). The current clip's {', '.join(mismatches)} "
            "doesn't line up with that earlier version."
        )
    return "The footage matches an earlier source and the event, location, and date all line up."


def _manipulation_types(classification: str) -> list[str]:
    return [_MANIPULATION_LABEL] if classification == "possible_false_context" else []


@router.post("/verifications", response_model=VerificationResult)
async def create_verification(video: UploadFile, caption: str = Form(default="")) -> VerificationResult:
    settings = Settings()
    ver_id = new_verification_id()
    now = datetime.now(timezone.utc)

    is_image = (video.content_type or "").startswith("image/")
    try:
        if is_image:
            keyframes = [_save_image(video, ver_id, settings)]
        else:
            video_path = save_upload(video.file, ver_id, settings)
            keyframes = select_keyframes(video_path, ver_id, settings)
    except (IngestionError, PreprocessingError) as exc:
        raise HTTPException(status_code=422, detail=_friendly_upload_error(exc)) from exc

    context = extract_claims(ver_id, caption, keyframes, now)

    try:
        candidates = DemoIndex().search([kf.local_path for kf in keyframes])
    except FileNotFoundError:
        candidates = []  # ponytail: index not built yet (`python -m backend.scripts.index_demo_sources`)
    ranked = rank(candidates, now) if candidates else []
    top = ranked[0] if ranked else None

    source_context = _source_context(top) if top is not None else None
    comparison = compare(context, source_context)
    visual_match = match_strength(top.match_types[0]) if top is not None and top.match_types else "unknown"

    source_complete = bool(top and top.event and top.location and (top.time_context or top.published_at))
    classification = classify(
        visual_match=visual_match,
        comparison=comparison,
        has_textual_conflict=False,
        source_context_complete=source_complete,
    )

    confidence_components = {
        "visual": {"high": 1.0, "medium": 0.7, "low": 0.4, "unknown": 0.0}[visual_match],
        "metadata": top.metadata_completeness if top else 0.0,
        "source_quality": top.source_quality if top else 0.0,
    }

    strongest_ids = [
        *context.event.evidence_ids, *context.location.evidence_ids, *context.time.evidence_ids,
    ]

    return VerificationResult(
        verification_id=ver_id,
        classification=classification,
        evidence_confidence=evidence_confidence(confidence_components),
        confidence_score=round(sum(confidence_components.values()) / len(confidence_components) * 100),
        current_context=context,
        source_context=source_context,
        comparison=comparison,
        visual_match=visual_match,
        headline=_headline(classification.value),
        summary=_summary(classification.value, comparison, source_context),
        manipulation_types=_manipulation_types(classification.value),
        strongest_evidence_ids=list(dict.fromkeys(strongest_ids)),
        sources=ranked,
        unresolved=context.unresolved,
        warnings=[],
    )
