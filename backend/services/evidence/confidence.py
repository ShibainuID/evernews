"""Aggregate internal evidence component scores into ConfidenceLabel (T16).

Inputs are internal per-component scores in [0, 1]; output is only the
controlled ``ConfidenceLabel``, never a probability. Min heuristic: any
component <= 0.5 -> LOW, all >= 0.8 -> HIGH, else MEDIUM. Empty components
-> LOW: no evidence cannot support high confidence.
"""

from backend.schemas.evidence import (
    ComparisonStatus,
    ConfidenceLabel,
    ResultClassification,
)
from backend.schemas.result import ContextComparison


def evidence_confidence(components: dict[str, float]) -> ConfidenceLabel:
    """Classify evidence confidence from internal component scores."""
    lowest = min(components.values(), default=0.0)
    if lowest <= 0.5:
        return ConfidenceLabel.LOW
    if lowest >= 0.8:
        return ConfidenceLabel.HIGH
    return ConfidenceLabel.MEDIUM


# Base "likely misleading" figure per controlled classification; the honest
# range (unknown evidence never implies a hoax), refined by comparison detail.
_BASE_BY_CLASSIFICATION = {
    ResultClassification.POSSIBLE_FALSE_CONTEXT: 80,
    # conflict found with any finding lands at 68-80: 68 + (4 mixed | 8 contradicted)
    # + 4 fact-check; 68 alone stays below the 70 presentation threshold
    ResultClassification.CLAIM_CONFLICT_FOUND: 68,
    ResultClassification.SOURCE_MATCH_WITH_INCOMPLETE_CONTEXT: 38,
    ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE: 8,
    ResultClassification.INSUFFICIENT_EVIDENCE: 22,
}
_MISMATCH_BOOST_MAX = 12.0  # applied at most once, from matched-source confidence
_WEB_FINDING_BOOST = {"contradicted": 8, "mixed": 4, "supported": 0, "insufficient": 0}
_FACT_CHECK_BOOST = 4  # a located fact-check corroborates the finding


def hoax_confidence(
    classification: ResultClassification,
    comparison: ContextComparison,
    event_web_finding: str = "insufficient",
    existing_fact_checks_found: bool = False,
) -> int:
    """0-100 likelihood the current claim is misleading; deterministic and
    evidence-grounded, presentation only (never the controlled label).

    A matched source that mismatches on event/location/date raises the figure
    via the dimension confidences; a web finding that directly contradicts or
    a located fact-check corroborates it further. UNKNOWN dimensions and
    missing evidence never count either way.
    """
    mismatches = [
        dim.confidence
        for dim in (comparison.event, comparison.location, comparison.date)
        if dim.status is ComparisonStatus.MISMATCH
    ]
    boost = (
        round(_MISMATCH_BOOST_MAX * (sum(mismatches) / len(mismatches)))
        if mismatches
        else 0
    )
    textual = _WEB_FINDING_BOOST.get(event_web_finding, 0)
    fact = _FACT_CHECK_BOOST if existing_fact_checks_found else 0
    return max(1, min(99, _BASE_BY_CLASSIFICATION[classification] + boost + textual + fact))
