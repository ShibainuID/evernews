"""Aggregate internal evidence component scores into ConfidenceLabel (T16).

Inputs are internal per-component scores in [0, 1]; output is only the
controlled ``ConfidenceLabel``, never a probability. Min heuristic: any
component <= 0.5 -> LOW, all >= 0.8 -> HIGH, else MEDIUM. Empty components
-> LOW: no evidence cannot support high confidence.
"""

from backend.schemas.evidence import ConfidenceLabel


def evidence_confidence(components: dict[str, float]) -> ConfidenceLabel:
    """Classify evidence confidence from internal component scores."""
    lowest = min(components.values(), default=0.0)
    if lowest <= 0.5:
        return ConfidenceLabel.LOW
    if lowest >= 0.8:
        return ConfidenceLabel.HIGH
    return ConfidenceLabel.MEDIUM
