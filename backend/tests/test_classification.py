"""T17 result classification: 5-rule deterministic state machine (design §9).

Precedence (explicit, in order):
1. strong visual (high/medium) + material mismatch -> possible_false_context
2. strong visual + no mismatch + incomplete source context -> source_match_with_incomplete_context
3. strong visual + no mismatch + all dims consistent + complete context -> context_consistent_with_source
4. textual conflict without strong visual -> claim_conflict_found
5. otherwise -> insufficient_evidence

UNKNOWN dimensions never count as mismatch, and an unknown comparison can
never be called consistent: incomplete/unknown comparison despite a true
completeness flag is the safe insufficient_evidence.
"""

from backend.schemas.evidence import ResultClassification
from backend.schemas.result import ContextComparison
from backend.services.evidence.classification import classify, has_material_mismatch
from backend.tests.fixtures.golden_cases import (
    build_dimension_comparison,
    case_a,
    case_b,
    case_c,
    case_d,
)


def _comparison(*statuses: str) -> ContextComparison:
    """Comparison with the given event/location/date statuses (defaults consistent)."""
    dims = [build_dimension_comparison(s, explanation="test") for s in statuses]
    return ContextComparison(
        event=dims[0], location=dims[1], date=dims[2],
    )


def _strong_consistent_comparison() -> ContextComparison:
    return _comparison("consistent", "consistent", "consistent")


# --- golden cases A-D through the classifier ---


def test_case_a_classifies_possible_false_context():
    case = case_a()
    assert has_material_mismatch(case.expected_comparison)
    assert (
        classify(case.visual_match, case.expected_comparison, False, True)
        is ResultClassification.POSSIBLE_FALSE_CONTEXT
    )


def test_case_b_classifies_possible_false_context():
    case = case_b()
    assert has_material_mismatch(case.expected_comparison)
    assert (
        classify(case.visual_match, case.expected_comparison, False, True)
        is ResultClassification.POSSIBLE_FALSE_CONTEXT
    )


def test_case_c_classifies_context_consistent_with_source():
    case = case_c()
    assert not has_material_mismatch(case.expected_comparison)
    assert (
        classify(case.visual_match, case.expected_comparison, False, True)
        is ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    )


def test_case_d_classifies_insufficient_evidence():
    case = case_d()
    assert not has_material_mismatch(case.expected_comparison)
    assert (
        classify(case.visual_match, case.expected_comparison, False, False)
        is ResultClassification.INSUFFICIENT_EVIDENCE
    )
    assert (
        classify(case.visual_match, case.expected_comparison, False, False)
        is not ResultClassification.POSSIBLE_FALSE_CONTEXT
    )


# --- branch coverage ---


def test_medium_visual_with_mismatch_is_possible_false_context():
    comparison = _comparison("consistent", "mismatch", "consistent")
    assert has_material_mismatch(comparison)
    assert (
        classify("medium", comparison, False, True)
        is ResultClassification.POSSIBLE_FALSE_CONTEXT
    )


def test_strong_visual_with_incomplete_source_context():
    comparison = _comparison("consistent", "unknown", "unknown")
    assert not has_material_mismatch(comparison)
    assert (
        classify("high", comparison, False, False)
        is ResultClassification.SOURCE_MATCH_WITH_INCOMPLETE_CONTEXT
    )


def test_medium_visual_with_incomplete_source_context():
    assert (
        classify("medium", _strong_consistent_comparison(), False, False)
        is ResultClassification.SOURCE_MATCH_WITH_INCOMPLETE_CONTEXT
    )


def test_strong_visual_with_complete_context_and_unknown_dimensions_is_insufficient():
    comparison = _comparison("consistent", "consistent", "unknown")
    assert not has_material_mismatch(comparison)
    assert (
        classify("high", comparison, False, True)
        is ResultClassification.INSUFFICIENT_EVIDENCE
    )


def test_textual_conflict_without_strong_visual_is_claim_conflict_found():
    assert (
        classify("low", _strong_consistent_comparison(), True, True)
        is ResultClassification.CLAIM_CONFLICT_FOUND
    )
    assert (
        classify("unknown", _strong_consistent_comparison(), True, True)
        is ResultClassification.CLAIM_CONFLICT_FOUND
    )


def test_strong_visual_with_unknown_comparison_and_conflict_is_insufficient():
    # F17-1: claim_conflict_found requires textual conflict AND no strong
    # visual match; a strong visual that cannot support rules 1-3 is the
    # safe insufficient_evidence, never claim_conflict_found.
    all_unknown = _comparison("unknown", "unknown", "unknown")
    for visual in ("high", "medium"):
        assert (
            classify(visual, all_unknown, True, True)
            is ResultClassification.INSUFFICIENT_EVIDENCE
        )


def test_visual_unknown_never_possible_false_context():
    mismatch = _comparison("consistent", "mismatch", "mismatch")
    assert has_material_mismatch(mismatch)
    for visual in ("unknown", "low"):
        result = classify(visual, mismatch, False, True)
        assert result is not ResultClassification.POSSIBLE_FALSE_CONTEXT
        assert result is ResultClassification.INSUFFICIENT_EVIDENCE


def test_unknown_dimensions_never_produce_possible_false_context():
    # A material mismatch exists elsewhere, but the only strong conclusion
    # would need the UNKNOWN dimension; safe path applies instead.
    comparison = _comparison("consistent", "mismatch", "unknown")
    assert has_material_mismatch(comparison)
    assert (
        classify("high", comparison, False, True)
        is ResultClassification.POSSIBLE_FALSE_CONTEXT
    )

    all_unknown = _comparison("unknown", "unknown", "unknown")
    assert not has_material_mismatch(all_unknown)
    assert (
        classify("high", all_unknown, False, True)
        is ResultClassification.INSUFFICIENT_EVIDENCE
    )


def test_unknown_comparison_never_consistent_even_with_complete_flag():
    all_unknown = _comparison("unknown", "unknown", "unknown")
    result = classify("high", all_unknown, False, True)
    assert result is not ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    assert result is ResultClassification.INSUFFICIENT_EVIDENCE


# --- exact precedence ---


def test_explicit_precedence_order():
    # Rule 4 fires only when visual is not strong.
    assert (
        classify("low", _strong_consistent_comparison(), True, True)
        is ResultClassification.CLAIM_CONFLICT_FOUND
    )
    # Strong visual + conflict: rule 1 beats rule 4 when there is a mismatch...
    mismatch = _comparison("consistent", "mismatch", "consistent")
    assert (
        classify("high", mismatch, True, True)
        is ResultClassification.POSSIBLE_FALSE_CONTEXT
    )
    # ...and rule 3 beats rule 4 when everything is consistent and complete.
    assert (
        classify("high", _strong_consistent_comparison(), True, True)
        is ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    )
    # Rule 5 catches the remainder.
    assert (
        classify("low", _strong_consistent_comparison(), False, False)
        is ResultClassification.INSUFFICIENT_EVIDENCE
    )


def test_classify_returns_controlled_enum():
    result = classify("high", _strong_consistent_comparison(), False, True)
    assert isinstance(result, ResultClassification)
    assert result.value in ("possible_false_context", "context_consistent_with_source")
