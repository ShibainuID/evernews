"""Evidence synthesizer (T33): bundle + ranked sources -> SynthesizedEvidence.

One text-only ``LunaProvider.structured`` call reasons over already-collected
evidence; this module has no web/search/fetch tool path (HANDOFF §16.2: NO
web tools). The model fills a strict claims schema and the provider owns its
single schema-repair attempt (HANDOFF §28) — the synthesizer never loops
repairs and never invents IDs.

``validate_synthesis`` enforces evidence grounding after the call: every
cited supporting/contradicting ID must exist in the bundle's known evidence
IDs, and a supported/contradicted/mixed finding must cite at least one ID
(design §5: the Synthesizer never produces a claim with zero evidence IDs).
Invalid output raises ``StructuredOutputError``.

``existing_fact_checks_found`` is derived from ``bundle.fact_checks`` only —
an empty fact-check result stays False and never becomes proof of truth.
``probable_source_context`` and ``visual_match`` are derived from the
selected ``best_visual_source_id`` in ``ranked_sources``; missing sources or
metadata stay null/unknown and are never fabricated (HANDOFF §16.3).
Coexisting supporting + contradicting evidence forces ``event_web_finding=
"mixed"`` with both ID lists and conflict descriptions retained.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.providers.base import LunaProvider
from backend.schemas.context import VideoContext
from backend.schemas.investigation import RawValidationBundle
from backend.schemas.result import (
    SourceCandidate,
    SourceContext,
    SynthesizedEvidence,
    VisualMatchLabel,
)
from backend.utils.llm import StructuredOutputError
from backend.utils.prompt_guard import wrap_untrusted

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "evidence_synthesizer.txt"
PROMPT = PROMPT_PATH.read_text()

# T13 candidate_type -> visual-match strength (same mapping as normalizer/ranker).
_MATCH_STRENGTH: dict[str, VisualMatchLabel] = {
    "full_image_match": "high",
    "partial_image_match": "medium",
    "page_match": "medium",
    "visually_similar": "low",
}

_WebFinding = Literal["supported", "contradicted", "mixed", "insufficient"]


class _SynthesisClaims(BaseModel):
    """Strict claims schema Luna fills in; SynthesizedEvidence is built locally."""

    model_config = ConfigDict(extra="forbid")

    event_web_finding: _WebFinding
    synthesis_summary: str
    existing_fact_checks_found: bool = False
    best_visual_source_id: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)


# --- deterministic prompt rendering ---


def _claim_line(name: str, claim: object) -> str:
    value = getattr(claim, "value", None)
    ids = ", ".join(getattr(claim, "evidence_ids", None) or []) or "none"
    return f"- {name}: {value or 'unknown'} (evidence: {ids})"


def _render_context(context: VideoContext) -> str:
    lines = [
        _claim_line("event", context.event),
        _claim_line("location", context.location),
        _claim_line("time", context.time),
    ]
    if context.visual_summary:
        lines.append(f"- visual summary: {context.visual_summary}")
    if context.unresolved:
        lines.append(f"- unresolved: {'; '.join(context.unresolved)}")
    return "\n".join(lines)


def _render_evidence(bundle: RawValidationBundle) -> str:
    lines = ["Fact checks:"]
    lines += (
        [
            f"- {e.evidence_id} [fact_check] publisher={e.publisher or 'none'} "
            f"title={e.review_title or 'none'} rating={e.textual_rating or 'none'} "
            f"claim={e.claim_text or 'none'} url={e.review_url}"
            for e in bundle.fact_checks
        ]
        if bundle.fact_checks
        else ["- (none found)"]
    )
    lines.append("Web research:")
    if not bundle.web_research:
        lines.append("- (none)")
    for result in bundle.web_research:
        lines.append(
            f"- task {result.task_id} status={result.status} finding={result.finding} "
            f"searches={result.searches_used} pages={result.pages_fetched}"
        )
        for e in result.evidence:
            lines.append(
                f"- {e.evidence_id} [web] publisher={e.publisher or 'none'} "
                f"title={e.title or 'none'} published_at={e.published_at or 'none'} "
                f"event={e.event or 'none'} location={e.location or 'none'} "
                f"date_context={e.date_context or 'none'} supports={e.supports_question} "
                f"contradicts={e.contradicts_question} "
                # page text is data, never a trusted instruction block (T40)
                f"excerpt={wrap_untrusted(e.relevant_excerpt) if e.relevant_excerpt else 'none'} "
                f"url={e.url}"
            )
        for note in result.unresolved:
            lines.append(f"- unresolved: {note}")
    lines.append("Visual candidates:")
    lines += (
        [
            f"- {c.candidate_id} [visual] type={c.candidate_type} frame={c.frame_id} "
            f"url={c.url} page_title={c.page_title or 'none'} provider={c.raw_provider_type} "
            f"score={c.provider_score or 'none'}"
            for c in bundle.visual_candidates
        ]
        if bundle.visual_candidates
        else ["- (none found)"]
    )
    return "\n".join(lines)


def _render_sources(sources: list[SourceCandidate]) -> str:
    if not sources:
        return "- (none)"
    return "\n".join(
        f"- {s.source_id} url={s.url} publisher={s.publisher or 'none'} "
        f"title={s.title or 'none'} published_at={s.published_at or 'none'} "
        f"event={s.event or 'none'} location={s.location or 'none'} "
        f"time_context={s.time_context or 'none'} match_types={s.match_types} "
        f"matched_frames={s.matched_frame_ids} evidence_ids={s.evidence_ids}"
        for s in sources
    )


def _known_evidence_ids(bundle: RawValidationBundle) -> set[str]:
    ids = [e.evidence_id for e in bundle.fact_checks]
    for result in bundle.web_research:
        ids.extend(e.evidence_id for e in result.evidence)
    ids.extend(c.candidate_id for c in bundle.visual_candidates)
    return set(ids)


# --- post-LLM validation (unit-testable without a provider) ---


def validate_synthesis(claims: _SynthesisClaims, known_evidence_ids: set[str]) -> None:
    """Reject claims that are not grounded in the bundle's known evidence IDs.

    Raises ``StructuredOutputError`` when a cited supporting/contradicting ID
    is unknown, or when a supported/contradicted/mixed finding cites zero
    IDs. Never repairs: the caller fails clearly instead of inventing IDs.
    """
    cited = [*claims.supporting_evidence_ids, *claims.contradicting_evidence_ids]
    unknown = sorted({eid for eid in cited if eid not in known_evidence_ids})
    if unknown:
        raise StructuredOutputError(
            "evidence synthesizer: cited unknown evidence IDs: " + ", ".join(unknown)
        )
    if claims.event_web_finding in ("supported", "contradicted", "mixed") and not cited:
        raise StructuredOutputError(
            f"evidence synthesizer: event_web_finding={claims.event_web_finding!r} "
            "requires at least one supporting or contradicting evidence ID"
        )


# --- local derivations (never trusted from the model) ---


def _find_source(
    sources: list[SourceCandidate], source_id: str | None
) -> SourceCandidate | None:
    if source_id is None:
        return None
    return next((s for s in sources if s.source_id == source_id), None)


def _visual_match(source: SourceCandidate | None) -> VisualMatchLabel:
    if source is None:
        return "unknown"
    labels = {_MATCH_STRENGTH[mt] for mt in source.match_types if mt in _MATCH_STRENGTH}
    if "high" in labels:
        return "high"
    if "medium" in labels:
        return "medium"
    if "low" in labels:
        return "low"
    return "unknown"


def _source_context(source: SourceCandidate | None) -> SourceContext | None:
    if source is None:
        return None
    return SourceContext(
        event=source.event,
        location=source.location,
        date=source.time_context,
        publisher=source.publisher,
        source_url=source.url,
        title=source.title,
    )


def _conflict_note(claims: _SynthesisClaims) -> str:
    supporting = ", ".join(claims.supporting_evidence_ids)
    contradicting = ", ".join(claims.contradicting_evidence_ids)
    return f"Conflict retained: supporting={supporting} vs contradicting={contradicting}; no side discarded."


# --- entry point ---


async def synthesize(
    context: VideoContext,
    bundle: RawValidationBundle,
    ranked_sources: list[SourceCandidate],
    luna_provider: LunaProvider | None = None,
) -> SynthesizedEvidence:
    """Bundle + ranked sources -> SynthesizedEvidence via one text-only Luna call.

    The provider owns its single schema-repair attempt; this function calls
    ``structured`` exactly once and fails clearly on invalid output rather
    than inventing IDs. Without a provider the result is honestly
    ``insufficient`` (plan interface: ``synthesize(context, bundle, ranked_sources)``).
    """
    known = _known_evidence_ids(bundle)

    if luna_provider is None:
        return SynthesizedEvidence(
            verification_id=context.verification_id,
            event_web_finding="insufficient",
            existing_fact_checks_found=bool(bundle.fact_checks),
            visual_match="unknown",
            supporting_evidence_ids=[],
            contradicting_evidence_ids=[],
            conflicts=[],
            unresolved=["evidence synthesizer: no provider available"],
            synthesis_summary="Synthesis could not be produced: no reasoning provider was available.",
        )

    prompt = PROMPT.format(
        context=_render_context(context),
        evidence=_render_evidence(bundle),
        sources=_render_sources(ranked_sources),
    )
    claims = await luna_provider.structured(prompt, _SynthesisClaims)
    validate_synthesis(claims, known)

    finding = claims.event_web_finding
    if claims.supporting_evidence_ids and claims.contradicting_evidence_ids:
        finding = "mixed"  # conflict preserved: never drop one side
    conflicts = list(claims.conflicts)
    if finding == "mixed" and not conflicts:
        conflicts.append(_conflict_note(claims))

    source = _find_source(ranked_sources, claims.best_visual_source_id)
    return SynthesizedEvidence(
        verification_id=context.verification_id,
        event_web_finding=finding,
        existing_fact_checks_found=bool(bundle.fact_checks),
        best_visual_source_id=claims.best_visual_source_id if source is not None else None,
        visual_match=_visual_match(source),
        probable_source_context=_source_context(source),
        supporting_evidence_ids=list(claims.supporting_evidence_ids),
        contradicting_evidence_ids=list(claims.contradicting_evidence_ids),
        conflicts=conflicts,
        unresolved=list(claims.unresolved),
        synthesis_summary=claims.synthesis_summary,
    )
