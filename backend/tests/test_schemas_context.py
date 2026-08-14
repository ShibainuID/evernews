"""Context extraction schema tests: HANDOFF §3.5, §5.1, §5.2, §5.3."""

import pytest
from pydantic import ValidationError

from backend.schemas.context import OCRHit, SpeechExtraction, VideoContext, VisualObservation
from backend.schemas.evidence import ContextClaim, EvidenceAtom, EvidenceType, KeyframeRef


def _claim(value: str = "flood") -> ContextClaim:
    return ContextClaim(
        value=value,
        normalized_value=value,
        confidence=0.9,
        evidence_ids=["speech_01"],
        explicitly_claimed=True,
    )


def _minimal_video_context() -> VideoContext:
    return VideoContext(
        verification_id="ver_123",
        event=_claim("flood"),
        location=_claim("Jakarta"),
        time=_claim("today"),
        evidence=[
            EvidenceAtom(evidence_id="speech_01", type=EvidenceType.SPEECH, value="banjir")
        ],
        keyframes=[
            KeyframeRef(frame_id="kf_01", timestamp_sec=3.5, local_path="work/kf_01.jpg")
        ],
    )


def test_speech_extraction_valid():
    s = SpeechExtraction(transcript="banjir Jakarta hari ini", confidence=0.9)
    assert s.language is None
    assert s.segments == []


def test_speech_extraction_no_audio_empty_is_valid():
    s = SpeechExtraction(transcript="", segments=[], confidence=None)
    assert s.transcript == ""
    assert s.segments == []
    assert s.confidence is None


def test_ocr_hit_valid():
    hit = OCRHit(frame_id="ocr_01", timestamp_sec=3.0, text="JAKARTA", confidence=0.95)
    assert hit.bbox is None


def test_ocr_hit_with_bbox():
    hit = OCRHit(
        frame_id="ocr_01",
        timestamp_sec=3.0,
        text="JAKARTA",
        confidence=0.95,
        bbox=[[10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]],
    )
    assert hit.bbox == [[10.0, 20.0], [110.0, 20.0], [110.0, 60.0], [10.0, 60.0]]


def test_visual_observation_valid_from_handoff_53():
    obs = VisualObservation(
        scene_type="urban_flood",
        events_visible=["flood"],
        objects=["car", "water"],
        actions=["drifting"],
        visible_text_candidates=[],
        landmarks=["Monas"],
        location_clues=["Jakarta"],
        environmental_clues=["heavy_rain"],
        anomalies=[],
        uncertain_observations=["scale of flooding unclear"],
        evidence_frames={"flood": ["kf_01", "kf_02"]},
    )
    assert obs.evidence_frames == {"flood": ["kf_01", "kf_02"]}


def test_video_context_valid_from_handoff_35_example():
    ctx = _minimal_video_context()
    assert ctx.verification_id == "ver_123"
    assert ctx.event.value == "flood"
    assert ctx.people_or_orgs == []
    assert ctx.entities == []
    assert ctx.keywords == []
    assert ctx.transcript is None
    assert ctx.ocr_texts == []
    assert ctx.visual_summary is None
    assert ctx.visual_observations == []
    assert ctx.visual_location_clues == []
    assert ctx.unresolved == []
    assert ctx.evidence[0].type is EvidenceType.SPEECH
    assert ctx.keyframes[0].frame_id == "kf_01"


def test_video_context_with_full_handoff_35_example():
    ctx = VideoContext(
        verification_id="ver_123",
        event=ContextClaim(
            value="flood",
            normalized_value="flood",
            confidence=0.96,
            evidence_ids=["speech_01", "visual_01"],
            explicitly_claimed=True,
        ),
        location=ContextClaim(
            value="Jakarta",
            normalized_value="Jakarta, Indonesia",
            confidence=0.92,
            evidence_ids=["ocr_02", "speech_02", "caption_01"],
            explicitly_claimed=True,
        ),
        time=ContextClaim(
            value="today",
            normalized_value="2026-08-15",
            confidence=0.87,
            evidence_ids=["caption_01", "speech_03"],
            explicitly_claimed=True,
        ),
        keywords=["flood", "Jakarta", "banjir"],
        unresolved=[],
        evidence=[],
        keyframes=[],
    )
    assert ctx.location.normalized_value == "Jakarta, Indonesia"
    assert ctx.keywords == ["flood", "Jakarta", "banjir"]


def test_video_context_requires_evidence():
    with pytest.raises(ValidationError):
        VideoContext(
            verification_id="ver_123",
            event=_claim(),
            location=_claim(),
            time=_claim(),
            keyframes=[],
        )


def test_video_context_requires_keyframes():
    with pytest.raises(ValidationError):
        VideoContext(
            verification_id="ver_123",
            event=_claim(),
            location=_claim(),
            time=_claim(),
            evidence=[],
        )
