"""Deterministic result classification (T17): HANDOFF §19, design §9.

Five controlled outcomes decided by a fixed if-order. UNKNOWN comparison
dimensions are never a mismatch and never support a "consistent" conclusion:
an incomplete/unknown comparison is the safe INSUFFICIENT_EVIDENCE even when
the source-context completeness flag is true.
"""

from backend.schemas.evidence import ComparisonStatus, ResultClassification
from backend.schemas.result import ContextComparison, VisualMatchLabel


def has_material_mismatch(comparison: ContextComparison) -> bool:
    """True when any compared dimension is MISMATCH; UNKNOWN never counts."""
    return any(
        dim.status is ComparisonStatus.MISMATCH
        for dim in (comparison.event, comparison.location, comparison.date)
    )


def classify(
    visual_match: VisualMatchLabel,
    comparison: ContextComparison,
    has_textual_conflict: bool,
    source_context_complete: bool,
) -> ResultClassification:
    """Classify the controlled result label by design §9 precedence."""
    strong_visual = visual_match in ("high", "medium")
    if strong_visual and has_material_mismatch(comparison):
        return ResultClassification.POSSIBLE_FALSE_CONTEXT
    if strong_visual and not source_context_complete:
        return ResultClassification.SOURCE_MATCH_WITH_INCOMPLETE_CONTEXT
    if strong_visual and not has_material_mismatch(comparison) and all(
        dim.status is ComparisonStatus.CONSISTENT
        for dim in (comparison.event, comparison.location, comparison.date)
    ):
        return ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    # F17-1: textual conflict alone is not enough; a strong visual match
    # blocks claim_conflict_found (it resolves via rules 1-3 or falls
    # through to the safe insufficient_evidence).
    if has_textual_conflict and not strong_visual:
        return ResultClassification.CLAIM_CONFLICT_FOUND
    return ResultClassification.INSUFFICIENT_EVIDENCE
