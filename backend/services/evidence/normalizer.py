"""Evidence source candidate normalization (T13): bundle -> deduplicated candidates.

Pure helpers (source_quality / metadata_completeness / match_strength) are
separated so the ranker (T14) never reads normalizer internals.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from urllib.parse import urlsplit

from backend.schemas.context import VideoContext
from backend.schemas.investigation import (
    FactCheckEvidence,
    RawValidationBundle,
    VisualWebCandidate,
    WebSourceEvidence,
)
from backend.schemas.result import SourceCandidate
from backend.services.evidence.page_metadata import PageMetadata, parse_page_metadata
from backend.utils.fetch import SafeFetchResult
from backend.utils.urls import canonicalize

# SourceCandidate metadata fields that count toward metadata_completeness
# (HANDOFF 13.3). Identifiers, URLs, and match arrays never count.
_METADATA_FIELDS = (
    "publisher",
    "title",
    "published_at",
    "event",
    "location",
    "time_context",
    "description",
)

# Handoff 13.4 heuristic, exact values mandated by the T13 brief.
_QUALITY_BY_SOURCE_TYPE = {
    "government": 1.0,
    "official": 1.0,
    "news": 0.9,
    "fact_check": 0.9,
    "fact-check": 0.9,
    "factcheck": 0.9,
    "blog": 0.5,
    "community": 0.5,
}
_GOVERNMENT_TLD_SUFFIXES = (".gov", ".go.id")

# Vision candidate type -> visual-match strength (T13 brief step 2).
_STRENGTH_BY_CANDIDATE_TYPE = {
    "full_image_match": "high",
    "partial_image_match": "medium",
    "page_match": "medium",
    "visually_similar": "low",
}

_ORIGIN_FACT_CHECK = "fact_check"
_ORIGIN_WEB_RESEARCH = "web_research"
_ORIGIN_WEB = "web"  # contract section 6: visual web candidates are origin "web"


def source_quality(
    *,
    source_type: str | None = None,
    publisher: str | None = None,
    domain: str | None = None,
) -> float:
    """Deterministic ranking heuristic, not a truth label (HANDOFF 13.4).

    Signals, in priority order: government domain, explicit source type,
    publisher keyword. Everything else defaults to the unknown 0.3 bucket.
    """
    if domain and domain.lower().endswith(_GOVERNMENT_TLD_SUFFIXES):
        return 1.0
    stype = (source_type or "").strip().lower()
    if stype in _QUALITY_BY_SOURCE_TYPE:
        return _QUALITY_BY_SOURCE_TYPE[stype]
    if publisher:
        lower = publisher.lower()
        if "government" in lower or "official" in lower:
            return 1.0
        if "fact check" in lower or "cek fakta" in lower or "factcheck" in lower:
            return 0.9
    return 0.3


def metadata_completeness(candidate: SourceCandidate) -> float:
    """Fraction of the seven source-context metadata fields that are populated."""
    present = sum(1 for name in _METADATA_FIELDS if getattr(candidate, name) is not None)
    return present / len(_METADATA_FIELDS)


def match_strength(candidate_type: str) -> str:
    """Vision candidate type -> high/medium/low. Schema Literal guarantees totality."""
    return _STRENGTH_BY_CANDIDATE_TYPE[candidate_type]


def _domain(url: str) -> str | None:
    return urlsplit(url).hostname


@dataclass
class _Raw:
    """One evidence record, pre-merge."""

    url: str
    origin: str
    evidence_id: str
    publisher: str | None = None
    title: str | None = None
    published_at: str | None = None
    event: str | None = None
    location: str | None = None
    time_context: str | None = None
    description: str | None = None
    source_type: str | None = None
    frame_id: str | None = None
    match_types: tuple[str, ...] = ()
    provider_score: float | None = None


@dataclass
class _Merged:
    """Accumulator for one canonical URL, in first-seen input order."""

    url: str
    canonical_url: str
    origin: str
    evidence_ids: list[str] = field(default_factory=list)
    matched_frame_ids: list[str] = field(default_factory=list)
    match_types: list[str] = field(default_factory=list)
    provider_scores: list[float] = field(default_factory=list)
    publisher: str | None = None
    title: str | None = None
    published_at: str | None = None
    event: str | None = None
    location: str | None = None
    time_context: str | None = None
    description: str | None = None
    source_type: str | None = None
    earliest_known_date: str | None = None

    @classmethod
    def from_raw(cls, raw: _Raw) -> "_Merged":
        return cls(
            url=raw.url,
            canonical_url=canonicalize(raw.url),
            origin=raw.origin,
            evidence_ids=[raw.evidence_id],
            matched_frame_ids=[raw.frame_id] if raw.frame_id is not None else [],
            match_types=list(raw.match_types),
            provider_scores=[raw.provider_score] if raw.provider_score is not None else [],
            publisher=raw.publisher,
            title=raw.title,
            published_at=raw.published_at,
            event=raw.event,
            location=raw.location,
            time_context=raw.time_context,
            description=raw.description,
            source_type=raw.source_type,
            earliest_known_date=raw.published_at,
        )


def _merge(merged: _Merged, raw: _Raw) -> None:
    """First record wins; later duplicates fill missing metadata and append id/match lists."""
    for name in _METADATA_FIELDS:
        value = getattr(raw, name)
        if getattr(merged, name) is None and value is not None:
            setattr(merged, name, value)
    if raw.source_type is not None and merged.source_type is None:
        merged.source_type = raw.source_type
    if raw.evidence_id not in merged.evidence_ids:
        merged.evidence_ids.append(raw.evidence_id)
    if raw.frame_id is not None and raw.frame_id not in merged.matched_frame_ids:
        merged.matched_frame_ids.append(raw.frame_id)
    for match_type in raw.match_types:
        if match_type not in merged.match_types:
            merged.match_types.append(match_type)
    if raw.provider_score is not None and raw.provider_score not in merged.provider_scores:
        merged.provider_scores.append(raw.provider_score)
    if raw.published_at is not None:
        current = merged.earliest_known_date
        # ponytail: min over ISO-ish date strings; YYYY-MM-DD sorts lexicographically
        merged.earliest_known_date = (
            raw.published_at if current is None else min(current, raw.published_at)
        )


def _fact_check_record(evidence: FactCheckEvidence) -> _Raw:
    return _Raw(
        url=evidence.review_url,
        origin=_ORIGIN_FACT_CHECK,
        evidence_id=evidence.evidence_id,
        publisher=evidence.publisher,
        title=evidence.review_title,
        published_at=evidence.review_date,
        source_type="fact_check",
    )


def _web_record(evidence: WebSourceEvidence) -> _Raw:
    return _Raw(
        url=evidence.url,
        origin=_ORIGIN_WEB_RESEARCH,
        evidence_id=evidence.evidence_id,
        publisher=evidence.publisher,
        title=evidence.title,
        published_at=evidence.published_at,
        event=evidence.event,
        location=evidence.location,
        time_context=evidence.date_context,
        description=evidence.relevant_excerpt,
        source_type=evidence.source_type,
    )


def _visual_record(candidate: VisualWebCandidate, meta: PageMetadata | None = None) -> _Raw:
    """Page-parsed metadata wins; agent fields are the fallback (HANDOFF 13.3)."""
    meta = meta or PageMetadata()
    score_entry = (
        (f"provider_score:{candidate.provider_score}",)
        if candidate.provider_score is not None
        else ()
    )
    return _Raw(
        url=candidate.url,
        origin=_ORIGIN_WEB,
        evidence_id=candidate.candidate_id,
        publisher=meta.publisher,
        title=meta.title or candidate.page_title,
        published_at=meta.published_at,
        event=meta.event,
        location=meta.location,
        time_context=meta.time_context,
        description=meta.description,
        frame_id=candidate.frame_id,
        match_types=(
            candidate.candidate_type,
            match_strength(candidate.candidate_type),
            f"frame:{candidate.frame_id}",
            f"provider:{candidate.raw_provider_type}",
            *score_entry,
        ),
        provider_score=candidate.provider_score,
    )


def _to_candidate(merged: _Merged) -> SourceCandidate:
    candidate = SourceCandidate(
        source_id="src_" + sha256(merged.canonical_url.encode()).hexdigest()[:12],
        url=merged.url,
        canonical_url=merged.canonical_url,
        publisher=merged.publisher,
        title=merged.title,
        published_at=merged.published_at,
        event=merged.event,
        location=merged.location,
        time_context=merged.time_context,
        description=merged.description,
        matched_frame_ids=list(merged.matched_frame_ids),
        match_types=list(merged.match_types),
        provider_scores=list(merged.provider_scores),
        earliest_known_date=merged.earliest_known_date,
        evidence_ids=list(merged.evidence_ids),
        origin=merged.origin,
    )
    candidate.source_quality = source_quality(
        source_type=merged.source_type,
        publisher=candidate.publisher,
        domain=_domain(candidate.url),
    )
    candidate.metadata_completeness = metadata_completeness(candidate)
    return candidate


async def _fetch_page(candidate: VisualWebCandidate, fetcher: Callable[[str], Awaitable[SafeFetchResult]]) -> PageMetadata:
    """Fetch the matched page and extract deterministic metadata (HANDOFF 13.3).

    Failure-tolerant: a blocked, empty, or malformed page yields empty
    metadata and the page_match must remain a candidate either way.
    """
    try:
        result = await fetcher(candidate.page_url or candidate.url)
    except Exception:
        return PageMetadata()
    return parse_page_metadata(result.body)


async def build_source_candidates(
    context: VideoContext,
    bundle: RawValidationBundle,
    fetcher: Callable[[str], Awaitable[SafeFetchResult]],
) -> list[SourceCandidate]:
    """Normalize a raw validation bundle into deduplicated source candidates.

    Input order is stable: fact checks, then web-research evidence, then
    visual candidates, each in list order. Records sharing a canonical URL
    merge into one candidate (first URL wins, later metadata fills gaps).
    ``context`` is part of the public contract for downstream consumers;
    the current normalization is per-bundle and does not read it.
    """
    merged: dict[str, _Merged] = {}
    for fact_evidence in bundle.fact_checks:
        _merge_or_create(merged, _fact_check_record(fact_evidence))
    for result in bundle.web_research:
        for source_evidence in result.evidence:
            _merge_or_create(merged, _web_record(source_evidence))
    for candidate in bundle.visual_candidates:
        meta = await _fetch_page(candidate, fetcher) if candidate.candidate_type == "page_match" else None
        _merge_or_create(merged, _visual_record(candidate, meta))
    return [_to_candidate(m) for m in merged.values()]


def _merge_or_create(merged: dict[str, _Merged], record: _Raw) -> None:
    key = canonicalize(record.url)
    existing = merged.get(key)
    if existing is None:
        merged[key] = _Merged.from_raw(record)
    else:
        _merge(existing, record)
