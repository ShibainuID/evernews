"""Stage 4-7 result contracts: HANDOFF §13.1 (SourceCandidate), §14 (VisualMatchAssessment),
§16.4 (SourceContext/SynthesizedEvidence), §17.2 (comparison), §20 (VerificationResult)."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ComparisonStatus, ConfidenceLabel, ResultClassification

VisualMatchLabel = Literal["high", "medium", "low", "unknown"]


class SourceCandidate(BaseModel):
    source_id: str

    url: str
    canonical_url: str
    publisher: str | None = None
    title: str | None = None
    published_at: str | None = None

    event: str | None = None
    location: str | None = None
    time_context: str | None = None
    description: str | None = None

    matched_frame_ids: list[str] = Field(default_factory=list)
    match_types: list[str] = Field(default_factory=list)
    provider_scores: list[float] = Field(default_factory=list)

    earliest_known_date: str | None = None
    source_quality: float | None = None
    metadata_completeness: float | None = None

    evidence_ids: list[str] = Field(default_factory=list)

    # field kontrak §6 — dipakai normalizer/ranker/demo index/orchestrator (T13/T14/T27/T32)
    origin: str | None = None
    rank_score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)


class VisualMatchAssessment(BaseModel):
    frame_id: str
    source_id: str

    provider_match_types: list[str]
    embedding_similarity: float | None = None
    local_feature_score: float | None = None

    label: VisualMatchLabel
    rationale: list[str]


class SourceContext(BaseModel):
    event: str | None = None
    location: str | None = None
    date: str | None = None
    publisher: str | None = None
    source_url: str | None = None
    title: str | None = None


class SynthesizedEvidence(BaseModel):
    verification_id: str

    event_web_finding: Literal["supported", "contradicted", "mixed", "insufficient"]
    existing_fact_checks_found: bool

    best_visual_source_id: str | None = None
    visual_match: VisualMatchLabel

    probable_source_context: SourceContext | None = None

    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]

    conflicts: list[str]
    unresolved: list[str]

    synthesis_summary: str


class DimensionComparison(BaseModel):
    current: str | None = None
    source: str | None = None
    status: ComparisonStatus
    confidence: float
    evidence_ids: list[str]
    explanation: str


class ContextComparison(BaseModel):
    event: DimensionComparison
    location: DimensionComparison
    date: DimensionComparison


class VerificationResult(BaseModel):
    verification_id: str
    classification: ResultClassification

    evidence_confidence: ConfidenceLabel
    # 0-100 display figure derived from the same internal component scores
    # that produce `evidence_confidence`; presentation only, never a substitute
    # for the controlled label
    confidence_score: int | None = None

    current_context: VideoContext
    source_context: SourceContext | None = None
    comparison: ContextComparison

    visual_match: VisualMatchLabel

    headline: str
    summary: str
    manipulation_types: list[str]

    strongest_evidence_ids: list[str]
    sources: list[SourceCandidate] = Field(default_factory=list)

    unresolved: list[str]
    warnings: list[str]
