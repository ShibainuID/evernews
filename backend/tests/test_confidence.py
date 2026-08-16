"""T16 evidence confidence: internal components -> controlled ConfidenceLabel.

Never a probability: component scores stay internal, output is only
ConfidenceLabel. Thresholds (min heuristic): all >= 0.8 -> HIGH, any <= 0.5
-> LOW, else MEDIUM. Empty components -> LOW: no evidence cannot support
high confidence.
"""

import pytest

from backend.schemas.evidence import (
    ComparisonStatus,
    ConfidenceLabel,
    ResultClassification,
)
from backend.schemas.result import ContextComparison, DimensionComparison
from backend.services.evidence.confidence import evidence_confidence, hoax_confidence

COMPONENTS = {
    "context_extraction": 0.9,
    "web_event": 0.85,
    "visual_source_match": 0.95,
    "source_metadata": 0.8,
    "comparison": 0.88,
}


def test_all_high_components_yield_high():
    assert evidence_confidence(COMPONENTS) is ConfidenceLabel.HIGH


def test_high_boundary_exactly_0_8():
    assert (
        evidence_confidence({"context_extraction": 0.8, "web_event": 0.8})
        is ConfidenceLabel.HIGH
    )


@pytest.mark.parametrize("low_score", [0.5, 0.3, 0.0])
def test_any_component_at_or_below_0_5_yields_low(low_score):
    components = {
        "context_extraction": 0.9,
        "web_event": low_score,
        "visual_source_match": 0.95,
    }
    assert evidence_confidence(components) is ConfidenceLabel.LOW


def test_low_boundary_exactly_0_5():
    assert evidence_confidence({"comparison": 0.5}) is ConfidenceLabel.LOW


def test_between_thresholds_yields_medium():
    components = {
        "context_extraction": 0.6,
        "web_event": 0.79,
        "visual_source_match": 0.9,
    }
    assert evidence_confidence(components) is ConfidenceLabel.MEDIUM


def test_single_mixed_component_medium():
    assert evidence_confidence({"source_metadata": 0.75}) is ConfidenceLabel.MEDIUM


def test_empty_components_yield_low():
    assert evidence_confidence({}) is ConfidenceLabel.LOW


def test_output_is_controlled_label_not_probability():
    result = evidence_confidence(COMPONENTS)
    assert isinstance(result, ConfidenceLabel)
    assert result.value in ("low", "medium", "high")
    assert not isinstance(result, float)


def test_component_names_are_arbitrary_strings():
    assert evidence_confidence({"anything": 0.9, "x_y_z": 0.85}) is ConfidenceLabel.HIGH


# --- hoax confidence: deterministic 0-100 display figure ---


def _comparison(*dims: tuple[ComparisonStatus, float]) -> ContextComparison:
    return ContextComparison(
        **{
            dim: DimensionComparison(
                status=status, confidence=confidence, evidence_ids=[], explanation=""
            )
            for dim, (status, confidence) in zip(
                ("event", "location", "date"), dims, strict=False
            )
        }
    )


def test_possible_false_context_with_high_confidence_mismatch_is_high():
    comparison = _comparison(
        (ComparisonStatus.MISMATCH, 0.95),
        (ComparisonStatus.MISMATCH, 0.9),
        (ComparisonStatus.MISMATCH, 0.85),
    )
    assert hoax_confidence(ResultClassification.POSSIBLE_FALSE_CONTEXT, comparison) == 91


def test_conflict_only_without_mismatch_stays_at_base():
    comparison = _comparison(
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
    )
    assert hoax_confidence(ResultClassification.CLAIM_CONFLICT_FOUND, comparison) == 68


def test_contradicted_finding_and_fact_check_corroboration_raise_score():
    comparison = _comparison(
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
    )
    score = hoax_confidence(
        ResultClassification.CLAIM_CONFLICT_FOUND,
        comparison,
        event_web_finding="contradicted",
        existing_fact_checks_found=True,
    )
    assert score == 68 + 8 + 4


def test_mixed_finding_with_fact_check_clears_the_hoax_threshold():
    comparison = _comparison(
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
    )
    mixed = hoax_confidence(
        ResultClassification.CLAIM_CONFLICT_FOUND,
        comparison,
        event_web_finding="mixed",
        existing_fact_checks_found=True,
    )
    contradicted = hoax_confidence(
        ResultClassification.CLAIM_CONFLICT_FOUND,
        comparison,
        event_web_finding="contradicted",
        existing_fact_checks_found=True,
    )
    assert mixed == 76  # at/above 70: presented as LIKELY HOAX
    assert mixed < contradicted


def test_consistent_context_scores_low():
    comparison = _comparison(
        (ComparisonStatus.CONSISTENT, 0.9),
        (ComparisonStatus.CONSISTENT, 0.9),
        (ComparisonStatus.CONSISTENT, 0.9),
    )
    assert hoax_confidence(ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE, comparison) == 8


def test_insufficient_evidence_never_implies_hoax():
    comparison = _comparison(
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
        (ComparisonStatus.UNKNOWN, 0.0),
    )
    assert hoax_confidence(ResultClassification.INSUFFICIENT_EVIDENCE, comparison) == 22


def test_output_is_clamped_between_1_and_99():
    comparison = _comparison(
        (ComparisonStatus.MISMATCH, 1.0),
        (ComparisonStatus.MISMATCH, 1.0),
        (ComparisonStatus.MISMATCH, 1.0),
    )
    assert 1 <= hoax_confidence(ResultClassification.POSSIBLE_FALSE_CONTEXT, comparison) <= 99
