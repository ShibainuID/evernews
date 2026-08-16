"""Stage 2 validation/retrieval contracts: HANDOFF §7.2 (plan), §8.4, §9.4, §10.4, §12."""

from typing import Literal

from pydantic import BaseModel, Field


class FactCheckTask(BaseModel):
    task_id: str
    queries: list[str]
    language_codes: list[str] = []
    goal: str


class WebResearchTask(BaseModel):
    task_id: str
    question: str
    queries: list[str]
    preferred_source_types: list[str]
    max_searches: int = 2  # speed knob: 2 searches / 3 pages keeps one agent run ~40-60s
    max_pages: int = 3


class VisualSearchTask(BaseModel):
    task_id: str
    frame_ids: list[str]
    goal: str
    max_candidates_per_frame: int = 10


class InvestigationPlan(BaseModel):
    verification_id: str
    fact_check_tasks: list[FactCheckTask]
    web_research_tasks: list[WebResearchTask]
    visual_search_tasks: list[VisualSearchTask]
    investigation_questions: list[str]
    stop_conditions: list[str]


class FactCheckEvidence(BaseModel):
    evidence_id: str
    query: str
    claim_text: str | None = None
    claimant: str | None = None
    publisher: str | None = None
    review_url: str
    review_title: str | None = None
    review_date: str | None = None
    textual_rating: str | None = None
    relevance_score: float | None = None
    raw: dict


class WebSourceEvidence(BaseModel):
    evidence_id: str
    url: str
    publisher: str | None = None
    title: str | None = None
    published_at: str | None = None
    retrieved_at: str
    source_type: str | None = None
    relevant_excerpt: str | None = None
    event: str | None = None
    location: str | None = None
    date_context: str | None = None
    supports_question: bool | None = None
    contradicts_question: bool | None = None
    relevance_score: float | None = None


class WebResearchResult(BaseModel):
    task_id: str
    question: str
    status: Literal["supported", "contradicted", "mixed", "insufficient"]
    finding: str
    evidence: list[WebSourceEvidence]
    unresolved: list[str]
    searches_used: int
    pages_fetched: int


class VisualWebCandidate(BaseModel):
    candidate_id: str
    frame_id: str
    candidate_type: Literal[
        "page_match", "full_image_match", "partial_image_match", "visually_similar"
    ]
    url: str
    page_url: str | None = None
    page_title: str | None = None
    provider_score: float | None = None
    raw_provider_type: str


class RawValidationBundle(BaseModel):
    verification_id: str
    plan: InvestigationPlan

    fact_checks: list[FactCheckEvidence]
    web_research: list[WebResearchResult]
    visual_candidates: list[VisualWebCandidate]

    errors: list[str] = Field(default_factory=list)
    branch_status: dict[str, str] = Field(default_factory=dict)
