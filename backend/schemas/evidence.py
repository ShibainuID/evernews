"""Shared evidence contract: HANDOFF §3.1-3.4 enums/models, §19 result classification."""

from enum import Enum

from pydantic import BaseModel


class ConfidenceLabel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ComparisonStatus(str, Enum):
    CONSISTENT = "consistent"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    USER_CAPTION = "user_caption"
    SPEECH = "speech"
    OCR = "ocr"
    VISUAL = "visual"
    FACT_CHECK = "fact_check"
    WEB_ARTICLE = "web_article"
    VISUAL_WEB_MATCH = "visual_web_match"


class ResultClassification(str, Enum):
    POSSIBLE_FALSE_CONTEXT = "possible_false_context"
    CONTEXT_CONSISTENT_WITH_SOURCE = "context_consistent_with_source"
    CLAIM_CONFLICT_FOUND = "claim_conflict_found"
    SOURCE_MATCH_WITH_INCOMPLETE_CONTEXT = "source_match_with_incomplete_context"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class KeyframeRef(BaseModel):
    frame_id: str
    timestamp_sec: float
    local_path: str
    public_url: str | None = None
    selection_reason: str | None = None


class OCRFrameRef(BaseModel):
    """One OCR sample from the dedicated ~1 fps set (HANDOFF §4.4): the frame
    file plus its sampling-contract timestamp (fps=1 aligned at t=0), so OCR
    hits never inherit visual-keyframe times."""

    local_path: str
    timestamp_sec: float


class EvidenceAtom(BaseModel):
    evidence_id: str
    type: EvidenceType

    value: str
    confidence: float | None = None

    frame_id: str | None = None
    timestamp_sec: float | None = None

    source_url: str | None = None
    publisher: str | None = None
    published_at: str | None = None

    raw_excerpt: str | None = None
    notes: list[str] = []


class ContextClaim(BaseModel):
    value: str | None
    normalized_value: str | None = None
    confidence: float
    evidence_ids: list[str]
    # Decided locally by the fuser from supporting atom types — the model is
    # never asked to emit it, so it defaults here for schema-valid LLM output.
    explicitly_claimed: bool = False
