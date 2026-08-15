"""T16 evidence confidence: internal components -> controlled ConfidenceLabel.

Never a probability: component scores stay internal, output is only
ConfidenceLabel. Thresholds (min heuristic): all >= 0.8 -> HIGH, any <= 0.5
-> LOW, else MEDIUM. Empty components -> LOW: no evidence cannot support
high confidence.
"""

import pytest

from backend.schemas.evidence import ConfidenceLabel
from backend.services.evidence.confidence import evidence_confidence

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
