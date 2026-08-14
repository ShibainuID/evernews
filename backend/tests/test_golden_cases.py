"""T07 golden cases A-D fixture tests: plan §7 table + HANDOFF §36.

Locks the expected comparison statuses and classifications of cases A-D and
enforces the fixture-wide invariants: every ContextClaim has non-empty
evidence_ids and every EvidenceAtom id is unique per case.
"""

from typing import Any

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ComparisonStatus, ResultClassification
from backend.schemas.investigation import RawValidationBundle
from backend.schemas.result import ContextComparison, SourceContext
from backend.tests.fixtures.golden_cases import (
    ALL_CASES,
    build_claim,
    build_source_context,
    build_video_context,
    case_a,
    case_b,
    case_c,
    case_d,
)


def _claims(case: Any) -> list[Any]:
    return [case.video_context.event, case.video_context.location, case.video_context.time]


# --- fixture-wide invariants (plan §7) ---


def test_each_case_carries_expected_artifacts():
    for case in ALL_CASES:
        assert isinstance(case.video_context, VideoContext)
        assert isinstance(case.bundle, RawValidationBundle)
        assert isinstance(case.source_context, SourceContext)
        assert isinstance(case.expected_comparison, ContextComparison)
        assert isinstance(case.expected_classification, ResultClassification)


def test_all_claims_have_non_empty_evidence_ids():
    for case in ALL_CASES:
        for claim in _claims(case):
            assert claim.evidence_ids, f"{case.verification_id}: claim {claim.value!r} has empty evidence_ids"


def test_evidence_atom_ids_are_unique_and_referenced():
    for case in ALL_CASES:
        atom_ids = [atom.evidence_id for atom in case.video_context.evidence]
        assert len(atom_ids) == len(set(atom_ids)), f"{case.verification_id}: duplicate evidence atom ids"
        for claim in _claims(case):
            for evidence_id in claim.evidence_ids:
                assert evidence_id in atom_ids, f"{case.verification_id}: claim references unknown atom {evidence_id}"


# --- locked expectations per case (brief + HANDOFF §36) ---


def test_case_a_locked_expectations():
    case = case_a()
    assert case.video_context.location.value == "Jakarta"
    assert case.video_context.time.normalized_value == "2026-08-15"
    assert case.source_context.location == "Bangkok"
    assert case.source_context.date == "2022-10-03"
    assert case.visual_match == "high"
    assert case.expected_comparison.event.status is ComparisonStatus.CONSISTENT
    assert case.expected_comparison.location.status is ComparisonStatus.MISMATCH
    assert case.expected_comparison.date.status is ComparisonStatus.MISMATCH
    assert case.expected_classification is ResultClassification.POSSIBLE_FALSE_CONTEXT


def test_case_b_locked_expectations():
    case = case_b()
    assert case.video_context.event.value == "protest"
    assert case.video_context.location.value == "Jakarta"
    assert case.video_context.time.normalized_value == "2026-08-15"
    assert case.source_context.date == "2023-06-05"
    assert case.visual_match == "high"
    assert case.expected_comparison.event.status is ComparisonStatus.CONSISTENT
    assert case.expected_comparison.location.status is ComparisonStatus.CONSISTENT
    assert case.expected_comparison.date.status is ComparisonStatus.MISMATCH
    assert case.expected_classification is ResultClassification.POSSIBLE_FALSE_CONTEXT


def test_case_c_locked_expectations():
    case = case_c()
    assert case.visual_match == "high"
    assert case.expected_comparison.event.status is ComparisonStatus.CONSISTENT
    assert case.expected_comparison.location.status is ComparisonStatus.CONSISTENT
    assert case.expected_comparison.date.status is ComparisonStatus.CONSISTENT
    assert case.expected_classification is ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE


def test_case_d_no_source_never_implies_fake():
    case = case_d()
    assert case.bundle.visual_candidates == []
    assert case.bundle.fact_checks == []
    assert case.bundle.web_research == []
    assert case.source_context == SourceContext()
    assert case.visual_match == "unknown"
    assert case.expected_classification is ResultClassification.INSUFFICIENT_EVIDENCE
    assert case.expected_classification is not ResultClassification.POSSIBLE_FALSE_CONTEXT
    for dim in (
        case.expected_comparison.event,
        case.expected_comparison.location,
        case.expected_comparison.date,
    ):
        assert dim.status is ComparisonStatus.UNKNOWN


def test_cases_abc_bundles_carry_retrieval_evidence():
    for case in (case_a(), case_b(), case_c()):
        assert case.bundle.fact_checks
        assert case.bundle.web_research
        assert case.bundle.visual_candidates


# --- factory overrides (brief refactor step) ---


def test_factories_allow_overriding_one_dimension():
    case = case_b(source_context=build_source_context(location="Bandung", date="2026-08-15"))
    assert case.source_context.location == "Bandung"
    assert case.source_context.date == "2026-08-15"
    assert case.expected_comparison.date.status is ComparisonStatus.MISMATCH
    assert case.expected_classification is ResultClassification.POSSIBLE_FALSE_CONTEXT

    context = build_video_context("ver_custom", time=build_claim("kemarin", "2026-08-14", 0.8, ["speech_03"]))
    assert context.time.value == "kemarin"
