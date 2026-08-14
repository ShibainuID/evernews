"""Stage 4-7 result schema tests: HANDOFF §13.1, §14, §16.4, §17.2, §20 + field kontrak §6."""

from typing import Any, cast

import pytest
from pydantic import ValidationError

from backend.schemas.context import VideoContext
from backend.schemas.evidence import (
    ComparisonStatus,
    ConfidenceLabel,
    ContextClaim,
    EvidenceAtom,
    EvidenceType,
    KeyframeRef,
    ResultClassification,
)
from backend.schemas.result import (
    ContextComparison,
    DimensionComparison,
    SourceCandidate,
    SourceContext,
    SynthesizedEvidence,
    VerificationResult,
    VisualMatchAssessment,
)


def _source_candidate() -> SourceCandidate:
    return SourceCandidate(
        source_id="src_01",
        url="https://example.com/article?utm_source=x",
        canonical_url="https://example.com/article",
        publisher="Example News",
        title="Flooding in Bangkok",
        published_at="2022-10-03",
        event="flood",
        location="Bangkok",
        time_context="2022-10-03",
        description="Flooding in Bangkok in October 2022",
        matched_frame_ids=["kf_01", "kf_03"],
        match_types=["page_match", "full_image_match"],
        provider_scores=[0.9, 0.95],
        earliest_known_date="2022-10-03",
        source_quality=0.9,
        metadata_completeness=0.8,
        evidence_ids=["vision_01", "meta_01"],
    )


def _visual_match(**overrides: Any) -> VisualMatchAssessment:
    base: dict[str, Any] = dict(
        frame_id="kf_01",
        source_id="src_01",
        provider_match_types=["full_image_match"],
        label="high",
        rationale=["full image match on provider"],
    )
    base.update(overrides)
    return VisualMatchAssessment(**base)


def _source_context() -> SourceContext:
    return SourceContext(
        event="flood",
        location="Bangkok",
        date="2022-10-03",
        publisher="Example News",
        source_url="https://example.com/article",
        title="Flooding in Bangkok",
    )


def _synthesized_evidence(**overrides: Any) -> SynthesizedEvidence:
    base: dict[str, Any] = dict(
        verification_id="ver_123",
        event_web_finding="supported",
        existing_fact_checks_found=False,
        best_visual_source_id="src_01",
        visual_match="high",
        probable_source_context=_source_context(),
        supporting_evidence_ids=["vision_01"],
        contradicting_evidence_ids=[],
        conflicts=[],
        unresolved=[],
        synthesis_summary=(
            "The uploaded footage strongly matches an earlier source describing "
            "flooding in Bangkok in October 2022."
        ),
    )
    base.update(overrides)
    return SynthesizedEvidence(**base)


def _dimension_comparison(status: str = "consistent") -> DimensionComparison:
    return DimensionComparison(
        current="flood",
        source="flood",
        status=cast(ComparisonStatus, status),
        confidence=0.95,
        evidence_ids=["vision_01"],
        explanation="Same event type.",
    )


def _context_comparison() -> ContextComparison:
    return ContextComparison(
        event=_dimension_comparison(),
        location=DimensionComparison(
            current="Jakarta",
            source="Bangkok",
            status=cast(ComparisonStatus, "mismatch"),
            confidence=0.9,
            evidence_ids=["vision_01"],
            explanation="Current claim city differs from source city.",
        ),
        date=DimensionComparison(
            current="2026-08-15",
            source="2022-10-03",
            status=cast(ComparisonStatus, "mismatch"),
            confidence=0.9,
            evidence_ids=["vision_01"],
            explanation="Current claim date differs from source date.",
        ),
    )


def _video_context() -> VideoContext:
    return VideoContext(
        verification_id="ver_123",
        event=ContextClaim(
            value="flood",
            normalized_value="flood",
            confidence=0.96,
            evidence_ids=["speech_01"],
            explicitly_claimed=True,
        ),
        location=ContextClaim(
            value="Jakarta",
            normalized_value="Jakarta, Indonesia",
            confidence=0.92,
            evidence_ids=["speech_02"],
            explicitly_claimed=True,
        ),
        time=ContextClaim(
            value="today",
            normalized_value="2026-08-15",
            confidence=0.87,
            evidence_ids=["speech_03"],
            explicitly_claimed=True,
        ),
        evidence=[
            EvidenceAtom(
                evidence_id="speech_01", type=EvidenceType.SPEECH, value="banjir Jakarta"
            )
        ],
        keyframes=[
            KeyframeRef(
                frame_id="kf_01", timestamp_sec=3.5, local_path="work/kf_01.jpg"
            )
        ],
    )


def _result(**overrides: Any) -> VerificationResult:
    base: dict[str, Any] = dict(
        verification_id="ver_123",
        classification="possible_false_context",
        evidence_confidence="high",
        current_context=_video_context(),
        source_context=_source_context(),
        comparison=_context_comparison(),
        visual_match="high",
        headline="Possible False Context",
        summary=(
            "The uploaded footage strongly matches an earlier source describing "
            "flooding in Bangkok in October 2022."
        ),
        manipulation_types=["location_changed", "old_footage_reused"],
        strongest_evidence_ids=["vision_01"],
        unresolved=["The system cannot prove that the 2022 page is the first-ever upload."],
        warnings=[],
    )
    base.update(overrides)
    return VerificationResult(**base)


# --- SourceCandidate (HANDOFF §13.1 + field kontrak §6) ---


def test_source_candidate_valid_from_handoff_131():
    c = _source_candidate()
    assert c.source_id == "src_01"
    assert c.canonical_url == "https://example.com/article"
    assert c.matched_frame_ids == ["kf_01", "kf_03"]
    assert c.match_types == ["page_match", "full_image_match"]
    assert c.provider_scores == [0.9, 0.95]
    assert c.metadata_completeness == 0.8
    assert c.evidence_ids == ["vision_01", "meta_01"]


def test_source_candidate_new_optional_fields_default_unfilled():
    c = _source_candidate()
    assert c.origin is None
    assert c.rank_score is None
    assert c.score_breakdown == {}


def test_source_candidate_new_optional_fields_valid_when_filled():
    c = SourceCandidate(
        source_id="src_01",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
        origin="demo_index",
        rank_score=0.91,
        score_breakdown={"visual": 0.45, "precedence": 0.2},
    )
    assert c.origin == "demo_index"
    assert c.rank_score == 0.91
    assert c.score_breakdown == {"visual": 0.45, "precedence": 0.2}


def test_source_candidate_requires_url_and_canonical_url():
    with pytest.raises(ValidationError):
        SourceCandidate(source_id="src_01", url="https://example.com/article")


def test_source_candidate_list_defaults_are_per_instance():
    a = _source_candidate()
    b = _source_candidate()
    a.matched_frame_ids.append("kf_05")
    a.score_breakdown["visual"] = 0.45
    assert b.matched_frame_ids == ["kf_01", "kf_03"]
    assert b.score_breakdown == {}


# --- VisualMatchAssessment (HANDOFF §14) ---


def test_visual_match_assessment_label_allows_four_values():
    for label in ["high", "medium", "low", "unknown"]:
        assert _visual_match(label=label).label == label


def test_visual_match_assessment_rejects_invalid_label():
    with pytest.raises(ValidationError):
        _visual_match(label="certain")


# --- SynthesizedEvidence + SourceContext (HANDOFF §16.4) ---


def test_synthesized_evidence_valid_from_handoff_164():
    s = _synthesized_evidence()
    assert s.verification_id == "ver_123"
    assert s.event_web_finding == "supported"
    assert s.existing_fact_checks_found is False
    assert s.best_visual_source_id == "src_01"
    assert s.visual_match == "high"
    assert s.probable_source_context.location == "Bangkok"


def test_synthesized_evidence_best_visual_source_id_none_valid():
    s = _synthesized_evidence(
        best_visual_source_id=None,
        visual_match="unknown",
        probable_source_context=None,
    )
    assert s.best_visual_source_id is None
    assert s.probable_source_context is None


def test_synthesized_evidence_event_web_finding_allows_four_values():
    for finding in ["supported", "contradicted", "mixed", "insufficient"]:
        assert _synthesized_evidence(event_web_finding=finding).event_web_finding == finding


def test_synthesized_evidence_rejects_invalid_event_web_finding():
    with pytest.raises(ValidationError):
        _synthesized_evidence(event_web_finding="maybe")


def test_synthesized_evidence_rejects_invalid_visual_match():
    with pytest.raises(ValidationError):
        _synthesized_evidence(visual_match="certain")


# --- DimensionComparison + ContextComparison (HANDOFF §17.2) ---


def test_dimension_comparison_status_allows_three_values():
    for status in ["consistent", "mismatch", "unknown"]:
        d = _dimension_comparison(status=status)
        assert d.status is ComparisonStatus(status)


def test_dimension_comparison_rejects_invalid_status():
    with pytest.raises(ValidationError):
        _dimension_comparison(status="maybe")


def test_dimension_comparison_requires_explanation():
    with pytest.raises(ValidationError):
        DimensionComparison(
            current="flood",
            source="flood",
            status="consistent",
            confidence=0.9,
            evidence_ids=[],
        )


def test_context_comparison_has_event_location_date():
    c = _context_comparison()
    assert c.event.status is ComparisonStatus.CONSISTENT
    assert c.location.status is ComparisonStatus.MISMATCH
    assert c.date.status is ComparisonStatus.MISMATCH
    assert c.location.current == "Jakarta"
    assert c.location.source == "Bangkok"


# --- VerificationResult (HANDOFF §20) ---


def test_verification_result_valid_from_handoff_20():
    r = _result()
    assert r.classification is ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert r.evidence_confidence is ConfidenceLabel.HIGH
    assert r.visual_match == "high"
    assert r.comparison.location.status is ComparisonStatus.MISMATCH
    assert r.manipulation_types == ["location_changed", "old_footage_reused"]
    assert r.strongest_evidence_ids == ["vision_01"]


def test_verification_result_accepts_all_five_classifications():
    for classification in ResultClassification:
        r = _result(classification=classification)
        assert r.classification is classification


def test_verification_result_accepts_all_three_evidence_confidences():
    for label in ConfidenceLabel:
        r = _result(evidence_confidence=label)
        assert r.evidence_confidence is label


def test_verification_result_comparison_required():
    with pytest.raises(ValidationError):
        _result(comparison=None)


def test_verification_result_source_context_none_valid():
    r = _result(source_context=None)
    assert r.source_context is None


def test_verification_result_sources_default_empty():
    r = _result()
    assert r.sources == []


def test_verification_result_sources_accepts_candidates():
    r = _result(sources=[_source_candidate()])
    assert r.sources[0].source_id == "src_01"
    assert r.sources[0].origin is None


def test_verification_result_sources_default_is_per_instance():
    a = _result()
    b = _result()
    a.sources.append(_source_candidate())
    assert b.sources == []
