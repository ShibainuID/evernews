"""Deterministic VerificationResult builder (T34): HANDOFF §20, §15.2, §43.

Assembles context + synthesis + comparison + ranked sources into the final
``VerificationResult`` with zero provider/network calls and zero model
wording: headline/summary are built locally from ``RESULT_WORDING`` templates
(never from ``synthesis.synthesis_summary``), classification reuses T17,
evidence confidence reuses T16.

Provenance rules (design §5/§7): a ``SourceCandidate`` without ``evidence_ids``
is excluded and the gap is surfaced in ``unresolved``; ``strongest_evidence_ids``
only cites IDs that exist in the returned sources or in the synthesis's
supporting/contradicting lists. No evidence is ever invented.
"""

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ComparisonStatus, ResultClassification
from backend.schemas.result import (
    ContextComparison,
    SourceCandidate,
    SourceContext,
    SynthesizedEvidence,
    VerificationResult,
    VisualMatchLabel,
)
from backend.services.evidence.classification import classify, has_material_mismatch
from backend.services.evidence.confidence import evidence_confidence, hoax_confidence

# Single-module safe wording (§15.2/§43): never "Original source confirmed",
# "First upload on the internet", "hoax", "fake", or any percentage.
RESULT_WORDING: dict[str, str] = {
    "earliest_reliable_match": "Earliest reliable match found by this system",
    "headline_possible_false_context": "Possible False Context",
    "headline_context_consistent_with_source": "Context Consistent with Source",
    "headline_claim_conflict_found": "Claim Conflict Found",
    "headline_source_match_with_incomplete_context": "Source Match with Incomplete Context",
    "headline_insufficient_evidence": "Insufficient Evidence",
    "summary_possible_false_context": (
        "The uploaded footage has a {visual} visual correspondence with an earlier source. "
        "{match_line}The current video describes the footage as {current_desc}. "
        "{dimensions}This suggests previously published footage may have been reused "
        "with a different context."
    ),
    "summary_context_consistent_with_source": (
        "{match_line}The current context ({current_desc}) is consistent with the "
        "description of the earlier source."
    ),
    "summary_claim_conflict_found": (
        "No strong visual source was found, and textual evidence conflicts with the "
        "current context ({current_desc})."
    ),
    "summary_source_match_with_incomplete_context": (
        "{match_line}The source does not provide enough event, location, or date "
        "metadata to compare the current context."
    ),
    "summary_insufficient_evidence": (
        "{match_line}Not enough evidence was available to complete the context "
        "comparison, so no conclusion was drawn."
    ),
    "dimension_consistent": "{name} is consistent with the source.",
    "dimension_mismatch": "{name} differs: {current} vs {source}.",
    "dimension_unknown": "{name} could not be compared.",
}

_MANIPULATION_BY_DIMENSION = ("event_changed", "location_changed", "date_changed")

# Evidence-derived markers for "this media is reported as AI-generated";
# conservative on purpose: only exact known phrasings, never bare "AI".
_AI_GENERATED_MARKERS = (
    "ai-generated",
    "ai generated",
    "artificial intelligence",
    "deepfake",
    "buatan ai",
    "dibuat ai",
    "diduga ai",
    "pakai ai",
    "99% ai",
)

_VISUAL_CONFIDENCE: dict[VisualMatchLabel, float] = {
    "high": 0.95,
    "medium": 0.7,
    "low": 0.4,
    "unknown": 0.0,
}

_WEB_FINDING_CONFIDENCE: dict[str, float] = {
    "supported": 0.9,
    "contradicted": 0.9,
    "mixed": 0.5,
    "insufficient": 0.0,
}


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _claim_value(context: VideoContext, claim) -> str | None:
    value = claim.normalized_value if claim.normalized_value is not None else claim.value
    return value or None


def _current_desc(context: VideoContext) -> str:
    parts = []
    for label, claim in (
        ("event", context.event),
        ("location", context.location),
        ("time", context.time),
    ):
        value = _claim_value(context, claim)
        if value is not None:
            parts.append(f"{label} {value}")
    return "; ".join(parts) or "unknown"


def _match_line(source: SourceContext | None) -> str:
    """§15.2 safe phrase, never original-source confirmation wording."""
    if source is None:
        return ""
    described = ", ".join(
        part for part in (source.event, source.location, source.date) if part
    )
    suffix = f": {described}" if described else ""
    return f"{RESULT_WORDING['earliest_reliable_match']}{suffix}. "


def _dimension_notes(comparison: ContextComparison) -> str:
    notes = []
    for name, dimension in (
        ("Event", comparison.event),
        ("Location", comparison.location),
        ("Date", comparison.date),
    ):
        if dimension.status is ComparisonStatus.CONSISTENT:
            notes.append(
                RESULT_WORDING["dimension_consistent"].format(name=name)
            )
        elif dimension.status is ComparisonStatus.MISMATCH:
            notes.append(
                RESULT_WORDING["dimension_mismatch"].format(
                    name=name, current=dimension.current, source=dimension.source
                )
            )
        else:
            notes.append(RESULT_WORDING["dimension_unknown"].format(name=name))
    return " ".join(notes)


def _classification(synthesis: SynthesizedEvidence, comparison: ContextComparison) -> ResultClassification:
    source = synthesis.probable_source_context
    source_complete = source is not None and all(
        (source.event, source.location, source.date)
    )
    return classify(
        synthesis.visual_match,
        comparison,
        has_textual_conflict=bool(synthesis.conflicts)
        or synthesis.event_web_finding in ("contradicted", "mixed"),
        source_context_complete=source_complete,
    )


def _manipulation_types(
    comparison: ContextComparison, synthesis: SynthesizedEvidence
) -> list[str]:
    types = [
        kind
        for kind, dimension in zip(
            _MANIPULATION_BY_DIMENSION,
            (comparison.event, comparison.location, comparison.date),
        )
        if dimension.status is ComparisonStatus.MISMATCH
    ]
    # old footage is only claimed for a real selected earlier source (T33:
    # best_visual_source_id + derived context) whose reliable visual match
    # still mismatches the current context — never from a match label alone
    if (
        synthesis.visual_match in ("high", "medium")
        and synthesis.best_visual_source_id is not None
        and synthesis.probable_source_context is not None
        and has_material_mismatch(comparison)
    ):
        types.append("old_footage_reused")
    # AI-generation is only claimed when the collected evidence itself says
    # so (summary/conflicts/unresolved from the synthesizer), never guessed.
    if _ai_generated_reported(synthesis):
        types.append("ai_generated_media")
    return types


def _ai_generated_reported(synthesis: SynthesizedEvidence) -> bool:
    """Deterministic keyword check over evidence-derived synthesis text."""
    haystack = " ".join(
        [synthesis.synthesis_summary, *synthesis.conflicts, *synthesis.unresolved]
    ).casefold()
    return any(marker in haystack for marker in _AI_GENERATED_MARKERS)


def _confidence_components(
    context: VideoContext,
    synthesis: SynthesizedEvidence,
    comparison: ContextComparison,
) -> dict[str, float]:
    claims = (context.event, context.location, context.time)
    context_score = sum(claim.confidence for claim in claims) / len(claims)
    source = synthesis.probable_source_context
    metadata_fields = (
        (source.event, source.location, source.date, source.publisher, source.source_url, source.title)
        if source is not None
        else (None, None, None, None, None, None)
    )
    compared = [
        dimension
        for dimension in (comparison.event, comparison.location, comparison.date)
        if dimension.status is not ComparisonStatus.UNKNOWN
    ]
    return {
        "context_extraction_confidence": context_score,
        "web_event_evidence_confidence": _WEB_FINDING_CONFIDENCE[synthesis.event_web_finding],
        "visual_source_match_confidence": _VISUAL_CONFIDENCE[synthesis.visual_match],
        "source_metadata_confidence": 0.9 * sum(1 for value in metadata_fields if value) / 6,
        "comparison_confidence": (
            sum(dimension.confidence for dimension in compared) / len(compared)
            if compared
            else 0.0
        ),
    }


def _presented_sources(
    ranked_sources: list[SourceCandidate],
) -> tuple[list[SourceCandidate], list[str]]:
    """Keep only sources with evidence provenance; excluded gaps are surfaced."""
    presented, excluded = [], []
    for candidate in ranked_sources:
        if candidate.evidence_ids:
            presented.append(candidate)
        else:
            excluded.append(candidate.source_id)
    return presented, excluded


def _best_source(
    presented: list[SourceCandidate], synthesis: SynthesizedEvidence
) -> SourceCandidate | None:
    if synthesis.best_visual_source_id is not None:
        for candidate in presented:
            if candidate.source_id == synthesis.best_visual_source_id:
                return candidate
    return presented[0] if presented else None


def build(
    context: VideoContext,
    synthesis: SynthesizedEvidence,
    comparison: ContextComparison,
    ranked_sources: list[SourceCandidate],
) -> VerificationResult:
    """Assemble the final deterministic result; no provider/network calls."""
    classification = _classification(synthesis, comparison)
    headline = RESULT_WORDING[f"headline_{classification.value}"]
    summary = RESULT_WORDING[f"summary_{classification.value}"].format(
        visual=synthesis.visual_match,
        match_line=_match_line(synthesis.probable_source_context),
        current_desc=_current_desc(context),
        dimensions=_dimension_notes(comparison),
    )

    presented, excluded = _presented_sources(ranked_sources)
    unresolved = _dedupe(
        [*context.unresolved, *synthesis.unresolved]
        + (
            [
                "Sources excluded for missing evidence provenance: "
                + ", ".join(excluded)
            ]
            if excluded
            else []
        )
    )

    best = _best_source(presented, synthesis)
    strongest_evidence_ids = _dedupe(
        [
            *(best.evidence_ids if best is not None else []),
            *synthesis.supporting_evidence_ids,
            *synthesis.contradicting_evidence_ids,
        ]
    )

    return VerificationResult(
        verification_id=context.verification_id,
        classification=classification,
        evidence_confidence=evidence_confidence(
            _confidence_components(context, synthesis, comparison)
        ),
        confidence_score=hoax_confidence(
            classification,
            comparison,
            event_web_finding=synthesis.event_web_finding,
            existing_fact_checks_found=synthesis.existing_fact_checks_found,
        ),
        current_context=context,
        source_context=synthesis.probable_source_context,
        comparison=comparison,
        visual_match=synthesis.visual_match,
        headline=headline,
        summary=summary,
        manipulation_types=_manipulation_types(comparison, synthesis),
        strongest_evidence_ids=strongest_evidence_ids,
        sources=presented,
        unresolved=unresolved,
        warnings=[],
    )
