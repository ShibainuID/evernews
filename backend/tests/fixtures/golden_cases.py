"""T07 golden cases A-D fixtures: plan §7 table, HANDOFF §36.

Reusable, Pydantic-validated scenario builders for every downstream test
(comparator T15, ranker T14, classification T17, result T34, pipeline T35,
api T36). Every ContextClaim carries non-empty evidence_ids and every
EvidenceAtom id is unique per case.

Builders take **overrides so a test can modify exactly one dimension.
"""

from dataclasses import dataclass
from typing import Any, cast

from backend.schemas.context import VideoContext
from backend.schemas.evidence import (
    ComparisonStatus,
    ContextClaim,
    EvidenceAtom,
    EvidenceType,
    KeyframeRef,
    ResultClassification,
)
from backend.schemas.investigation import (
    FactCheckEvidence,
    InvestigationPlan,
    RawValidationBundle,
    VisualWebCandidate,
    WebResearchResult,
    WebSourceEvidence,
)
from backend.schemas.result import (
    ContextComparison,
    DimensionComparison,
    SourceContext,
    VisualMatchLabel,
)


@dataclass(frozen=True)
class GoldenCase:
    """One golden scenario: current video context + retrieval bundle + source
    context + the expected comparison and classification it must produce."""

    verification_id: str
    video_context: VideoContext
    bundle: RawValidationBundle
    source_context: SourceContext
    visual_match: VisualMatchLabel
    expected_comparison: ContextComparison
    expected_classification: ResultClassification


# --- small builders ---


def build_claim(
    value: str,
    normalized_value: str | None,
    confidence: float,
    evidence_ids: list[str],
) -> ContextClaim:
    return ContextClaim(
        value=value,
        normalized_value=normalized_value,
        confidence=confidence,
        evidence_ids=evidence_ids,
        explicitly_claimed=True,
    )


def build_atom(
    evidence_id: str,
    type: EvidenceType,
    value: str,
    frame_id: str | None = None,
    timestamp_sec: float | None = None,
    **overrides: Any,
) -> EvidenceAtom:
    base: dict[str, Any] = dict(type=type, value=value)
    if frame_id is not None:
        base["frame_id"] = frame_id
    if timestamp_sec is not None:
        base["timestamp_sec"] = timestamp_sec
    base.update(overrides)
    return EvidenceAtom(evidence_id=evidence_id, **base)


def build_keyframe(frame_id: str, timestamp_sec: float = 3.5, local_path: str = "") -> KeyframeRef:
    return KeyframeRef(
        frame_id=frame_id,
        timestamp_sec=timestamp_sec,
        local_path=local_path or f"work/{frame_id}.jpg",
    )


def build_visual_candidate(
    candidate_id: str,
    frame_id: str,
    candidate_type: str = "full_image_match",
    url: str = "https://example.com/article",
    provider_score: float = 0.95,
    **overrides: Any,
) -> VisualWebCandidate:
    base: dict[str, Any] = dict(
        candidate_id=candidate_id,
        frame_id=frame_id,
        candidate_type=candidate_type,
        url=url,
        provider_score=provider_score,
        raw_provider_type="google_vision",
    )
    base.update(overrides)
    return VisualWebCandidate(**base)


def build_fact_check(
    evidence_id: str,
    query: str,
    review_url: str = "https://example.com/factcheck",
    **overrides: Any,
) -> FactCheckEvidence:
    base: dict[str, Any] = dict(
        evidence_id=evidence_id,
        query=query,
        review_url=review_url,
        raw={"rating": "old footage"},
    )
    base.update(overrides)
    return FactCheckEvidence(**base)


def build_web_research(
    task_id: str,
    question: str,
    finding: str,
    evidence: list[WebSourceEvidence],
    **overrides: Any,
) -> WebResearchResult:
    base: dict[str, Any] = dict(
        task_id=task_id,
        question=question,
        status="supported",
        finding=finding,
        evidence=evidence,
        unresolved=[],
        searches_used=2,
        pages_fetched=1,
    )
    base.update(overrides)
    return WebResearchResult(**base)


def build_web_source(
    evidence_id: str,
    url: str,
    retrieved_at: str = "2026-08-15T00:00:00Z",
    **overrides: Any,
) -> WebSourceEvidence:
    base: dict[str, Any] = dict(evidence_id=evidence_id, url=url, retrieved_at=retrieved_at)
    base.update(overrides)
    return WebSourceEvidence(**base)


def build_empty_plan(verification_id: str) -> InvestigationPlan:
    return InvestigationPlan(
        verification_id=verification_id,
        fact_check_tasks=[],
        web_research_tasks=[],
        visual_search_tasks=[],
        investigation_questions=[],
        stop_conditions=[],
    )


def build_bundle(
    verification_id: str,
    *,
    fact_checks: list[FactCheckEvidence] | None = None,
    web_research: list[WebResearchResult] | None = None,
    visual_candidates: list[VisualWebCandidate] | None = None,
    **overrides: Any,
) -> RawValidationBundle:
    base: dict[str, Any] = dict(
        verification_id=verification_id,
        plan=build_empty_plan(verification_id),
        fact_checks=fact_checks or [],
        web_research=web_research or [],
        visual_candidates=visual_candidates or [],
    )
    base.update(overrides)
    return RawValidationBundle(**base)


def build_video_context(
    verification_id: str,
    *,
    event: ContextClaim | None = None,
    location: ContextClaim | None = None,
    time: ContextClaim | None = None,
    evidence: list[EvidenceAtom] | None = None,
    keyframes: list[KeyframeRef] | None = None,
    **overrides: Any,
) -> VideoContext:
    """Default = Case A shape (Jakarta flood 2026); override one claim/evidence set at a time."""
    base: dict[str, Any] = dict(
        verification_id=verification_id,
        event=event
        or build_claim(
            value="flood",
            normalized_value="flood",
            confidence=0.96,
            evidence_ids=["speech_01"],
        ),
        location=location
        or build_claim(
            value="Jakarta",
            normalized_value="Jakarta, Indonesia",
            confidence=0.92,
            evidence_ids=["speech_02"],
        ),
        time=time
        or build_claim(
            value="today",
            normalized_value="2026-08-15",
            confidence=0.87,
            evidence_ids=["speech_03"],
        ),
        evidence=evidence
        or [
            build_atom("speech_01", EvidenceType.SPEECH, "banjir Jakarta"),
            build_atom("speech_02", EvidenceType.SPEECH, "di Jakarta"),
            build_atom("speech_03", EvidenceType.SPEECH, "hari ini"),
            build_atom(
                "visual_01",
                EvidenceType.VISUAL,
                "flooded street with people on rooftops",
                frame_id="kf_01",
            ),
        ],
        keyframes=keyframes or [build_keyframe("kf_01")],
    )
    base.update(overrides)
    return VideoContext(**base)


def build_source_context(**overrides: Any) -> SourceContext:
    """Default = Case A source (Bangkok flood 2022)."""
    base: dict[str, Any] = dict(
        event="flood",
        location="Bangkok",
        date="2022-10-03",
        publisher="Example News",
        source_url="https://example.com/article/bangkok-flood",
        title="Flooding in Bangkok",
    )
    base.update(overrides)
    return SourceContext(**base)


def build_dimension_comparison(
    status: str,
    *,
    current: str | None = None,
    source: str | None = None,
    confidence: float = 0.9,
    evidence_ids: list[str] | None = None,
    explanation: str = "",
    **overrides: Any,
) -> DimensionComparison:
    base: dict[str, Any] = dict(
        current=current,
        source=source,
        status=cast(ComparisonStatus, status),
        confidence=confidence,
        evidence_ids=evidence_ids or [],
        explanation=explanation,
    )
    base.update(overrides)
    return DimensionComparison(**base)


def build_comparison(
    event: DimensionComparison | None = None,
    location: DimensionComparison | None = None,
    date: DimensionComparison | None = None,
) -> ContextComparison:
    return ContextComparison(
        event=event or build_dimension_comparison("consistent", explanation="Same event."),
        location=location or build_dimension_comparison("unknown", explanation="No source location."),
        date=date or build_dimension_comparison("unknown", explanation="No source date."),
    )


# --- the four golden cases (plan §7 table / HANDOFF §36) ---


def case_a(**overrides: Any) -> GoldenCase:
    """False location + old footage: Jakarta flood 2026 vs Bangkok flood 2022, visual high."""
    video = build_video_context("ver_a")
    source = build_source_context()
    bundle = build_bundle(
        "ver_a",
        fact_checks=[
            build_fact_check(
                "fc_a_01",
                "Jakarta banjir 2026",
                review_url="https://example.com/factcheck/bangkok-2022",
                publisher="Example Fact Check",
                review_date="2022-10-05",
                textual_rating="old footage",
                relevance_score=0.9,
            )
        ],
        web_research=[
            build_web_research(
                "web_a_01",
                "When was this flood footage first published?",
                "The footage matches Bangkok flooding from October 2022.",
                [
                    build_web_source(
                        "web_a_01",
                        "https://example.com/article/bangkok-flood",
                        publisher="Example News",
                        title="Flooding in Bangkok",
                        published_at="2022-10-03",
                        event="flood",
                        location="Bangkok",
                        date_context="2022-10-03",
                        supports_question=True,
                        relevance_score=0.95,
                    )
                ],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_a_01", "kf_01", url="https://example.com/article/bangkok-flood")],
    )
    comparison = build_comparison(
        location=build_dimension_comparison(
            "mismatch",
            current="Jakarta",
            source="Bangkok",
            explanation="Claim city differs from source city.",
        ),
        date=build_dimension_comparison(
            "mismatch",
            current="2026-08-15",
            source="2022-10-03",
            explanation="Claim date differs from source date.",
        ),
    )
    base: dict[str, Any] = dict(
        verification_id="ver_a",
        video_context=video,
        bundle=bundle,
        source_context=source,
        visual_match="high",
        expected_comparison=comparison,
        expected_classification=ResultClassification.POSSIBLE_FALSE_CONTEXT,
    )
    base.update(overrides)
    return GoldenCase(**base)


def case_b(**overrides: Any) -> GoldenCase:
    """False time only: protest today (2026) vs same location/event protest 2023, visual high."""
    video = build_video_context(
        "ver_b",
        event=build_claim("protest", "protest", 0.95, ["speech_01"]),
        location=build_claim("Jakarta", "Jakarta, Indonesia", 0.93, ["speech_02"]),
        time=build_claim("today", "2026-08-15", 0.9, ["speech_03"]),
        evidence=[
            build_atom("speech_01", EvidenceType.SPEECH, "demonstrasi di Jakarta"),
            build_atom("speech_02", EvidenceType.SPEECH, "di Jakarta"),
            build_atom("speech_03", EvidenceType.SPEECH, "hari ini"),
            build_atom(
                "visual_01",
                EvidenceType.VISUAL,
                "large crowd with banners on a main street",
                frame_id="kf_01",
            ),
        ],
    )
    source = build_source_context(
        event="protest",
        location="Jakarta",
        date="2023-06-05",
        publisher="Example News",
        source_url="https://example.com/article/jakarta-protest-2023",
        title="Protest in Jakarta in 2023",
    )
    bundle = build_bundle(
        "ver_b",
        fact_checks=[
            build_fact_check(
                "fc_b_01",
                "Jakarta demo hari ini 2026",
                review_url="https://example.com/factcheck/jakarta-2023",
                publisher="Example Fact Check",
                review_date="2023-06-07",
                textual_rating="old footage",
                relevance_score=0.88,
            )
        ],
        web_research=[
            build_web_research(
                "web_b_01",
                "When was this protest footage first published?",
                "The footage matches a Jakarta protest from June 2023.",
                [
                    build_web_source(
                        "web_b_01",
                        "https://example.com/article/jakarta-protest-2023",
                        publisher="Example News",
                        title="Protest in Jakarta in 2023",
                        published_at="2023-06-05",
                        event="protest",
                        location="Jakarta",
                        date_context="2023-06-05",
                        supports_question=True,
                        relevance_score=0.93,
                    )
                ],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_b_01", "kf_01", url="https://example.com/article/jakarta-protest-2023")],
    )
    comparison = build_comparison(
        location=build_dimension_comparison(
            "consistent",
            current="Jakarta",
            source="Jakarta",
            explanation="Same city.",
        ),
        date=build_dimension_comparison(
            "mismatch",
            current="2026-08-15",
            source="2023-06-05",
            explanation="Claim date differs from source date.",
        ),
    )
    base: dict[str, Any] = dict(
        verification_id="ver_b",
        video_context=video,
        bundle=bundle,
        source_context=source,
        visual_match="high",
        expected_comparison=comparison,
        expected_classification=ResultClassification.POSSIBLE_FALSE_CONTEXT,
    )
    base.update(overrides)
    return GoldenCase(**base)


def case_c(**overrides: Any) -> GoldenCase:
    """Matching context: event/location/date all consistent with source, visual high."""
    video = build_video_context(
        "ver_c",
        event=build_claim("flood", "flood", 0.96, ["speech_01"]),
        location=build_claim("Jakarta", "Jakarta, Indonesia", 0.94, ["speech_02"]),
        time=build_claim("3 Oktober 2022", "2022-10-03", 0.91, ["speech_03"]),
        evidence=[
            build_atom("speech_01", EvidenceType.SPEECH, "banjir Jakarta"),
            build_atom("speech_02", EvidenceType.SPEECH, "di Jakarta"),
            build_atom("speech_03", EvidenceType.SPEECH, "3 Oktober 2022"),
            build_atom(
                "visual_01",
                EvidenceType.VISUAL,
                "flooded street with people on rooftops",
                frame_id="kf_01",
            ),
        ],
    )
    source = build_source_context(
        event="flood",
        location="Jakarta",
        date="2022-10-03",
        publisher="Example News",
        source_url="https://example.com/article/jakarta-flood-2022",
        title="Flooding in Jakarta",
    )
    bundle = build_bundle(
        "ver_c",
        fact_checks=[
            build_fact_check(
                "fc_c_01",
                "Jakarta banjir 3 Oktober 2022",
                review_url="https://example.com/factcheck/jakarta-2022",
                publisher="Example Fact Check",
                review_date="2022-10-04",
                textual_rating="accurate",
                relevance_score=0.95,
            )
        ],
        web_research=[
            build_web_research(
                "web_c_01",
                "Does this flood footage match Jakarta, October 2022?",
                "The footage and metadata consistently describe Jakarta flooding in October 2022.",
                [
                    build_web_source(
                        "web_c_01",
                        "https://example.com/article/jakarta-flood-2022",
                        publisher="Example News",
                        title="Flooding in Jakarta",
                        published_at="2022-10-03",
                        event="flood",
                        location="Jakarta",
                        date_context="2022-10-03",
                        supports_question=True,
                        relevance_score=0.96,
                    )
                ],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_c_01", "kf_01", url="https://example.com/article/jakarta-flood-2022")],
    )
    comparison = build_comparison(
        location=build_dimension_comparison(
            "consistent",
            current="Jakarta",
            source="Jakarta",
            explanation="Same city.",
        ),
        date=build_dimension_comparison(
            "consistent",
            current="2022-10-03",
            source="2022-10-03",
            explanation="Same date.",
        ),
    )
    base: dict[str, Any] = dict(
        verification_id="ver_c",
        video_context=video,
        bundle=bundle,
        source_context=source,
        visual_match="high",
        expected_comparison=comparison,
        expected_classification=ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE,
    )
    base.update(overrides)
    return GoldenCase(**base)


def case_d(**overrides: Any) -> GoldenCase:
    """No source found: empty bundle (vision and fact check empty), never implies fake."""
    video = build_video_context(
        "ver_d",
        event=build_claim("earthquake", "earthquake", 0.7, ["speech_01"]),
        location=build_claim("Yogyakarta", "Yogyakarta, Indonesia", 0.8, ["speech_02"]),
        time=build_claim("today", "2026-08-15", 0.75, ["speech_03"]),
        evidence=[
            build_atom("speech_01", EvidenceType.SPEECH, "gempa di Yogyakarta"),
            build_atom("speech_02", EvidenceType.SPEECH, "Yogyakarta"),
            build_atom("speech_03", EvidenceType.SPEECH, "hari ini"),
            build_atom(
                "visual_01",
                EvidenceType.VISUAL,
                "shaky footage of a cracked road",
                frame_id="kf_01",
            ),
        ],
    )
    bundle = build_bundle(
        "ver_d",
        fact_checks=[],
        web_research=[],
        visual_candidates=[],
        errors=["vision: no candidates", "fact_check: no results"],
        branch_status={"vision": "empty", "fact_check": "empty", "investigator": "empty"},
    )
    comparison = build_comparison(
        event=build_dimension_comparison(
            "unknown",
            current="earthquake",
            source=None,
            confidence=0.0,
            explanation="No reliable source found to compare against.",
        ),
        location=build_dimension_comparison(
            "unknown",
            current="Yogyakarta",
            source=None,
            confidence=0.0,
            explanation="No reliable source found to compare against.",
        ),
        date=build_dimension_comparison(
            "unknown",
            current="2026-08-15",
            source=None,
            confidence=0.0,
            explanation="No reliable source found to compare against.",
        ),
    )
    base: dict[str, Any] = dict(
        verification_id="ver_d",
        video_context=video,
        bundle=bundle,
        source_context=SourceContext(),
        visual_match="unknown",
        expected_comparison=comparison,
        expected_classification=ResultClassification.INSUFFICIENT_EVIDENCE,
    )
    base.update(overrides)
    return GoldenCase(**base)


ALL_CASES: tuple[GoldenCase, ...] = (case_a(), case_b(), case_c(), case_d())
