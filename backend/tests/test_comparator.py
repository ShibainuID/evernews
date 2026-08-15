"""T15 comparator tests: deterministic WHAT/WHERE/WHEN comparison (HANDOFF §17, §37).

Covers golden cases A-D statuses, unknown-never-mismatch guards, normalized
spelling/synonym/parent-child equality, and the semantic_equiv fallback
invocation rules. All behavior is exercised through the public
``compare(current, source, semantic_equiv=None)`` interface.
"""

from collections.abc import Callable

from backend.schemas.evidence import ComparisonStatus, ContextClaim
from backend.services.evidence.comparator import compare
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

_NO_SOURCE_EXPLANATION = "No reliable source found to compare against."


def _claim_none(evidence_ids: list[str]) -> ContextClaim:
    """A claim with no value at all (neither raw nor normalized)."""
    return ContextClaim(
        value=None,
        normalized_value=None,
        confidence=0.8,
        evidence_ids=evidence_ids,
        explicitly_claimed=True,
    )


# --- golden cases A-D (statuses locked by the T07 fixtures) ---


def test_case_a_location_and_date_mismatch_event_consistent():
    case = case_a()
    result = compare(case.video_context, case.source_context)
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.location.status is ComparisonStatus.MISMATCH
    assert result.date.status is ComparisonStatus.MISMATCH


def test_case_b_date_mismatch_only():
    case = case_b()
    result = compare(case.video_context, case.source_context)
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.location.status is ComparisonStatus.CONSISTENT
    assert result.date.status is ComparisonStatus.MISMATCH


def test_case_c_all_dimensions_consistent():
    case = case_c()
    result = compare(case.video_context, case.source_context)
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.location.status is ComparisonStatus.CONSISTENT
    assert result.date.status is ComparisonStatus.CONSISTENT


def test_case_d_missing_source_is_unknown_never_mismatch():
    case = case_d()
    result = compare(case.video_context, case.source_context)  # SourceContext() empty
    for dim in (result.event, result.location, result.date):
        assert dim.status is ComparisonStatus.UNKNOWN
        assert dim.confidence == 0.0
        assert dim.source is None
    assert result.event.explanation == _NO_SOURCE_EXPLANATION


def test_none_source_yields_three_unknown_dimensions():
    case = case_a()
    result = compare(case.video_context, None)
    for dim in (result.event, result.location, result.date):
        assert dim.status is ComparisonStatus.UNKNOWN
        assert dim.confidence == 0.0
        assert dim.explanation == _NO_SOURCE_EXPLANATION


# --- UNKNOWN propagation: never becomes MISMATCH ---


def test_unknown_source_date_is_unknown_not_mismatch():
    context = build_video_context("ver_ud")  # time normalized to 2026-08-15
    source = build_source_context(location="Jakarta", date=None)
    result = compare(context, source)
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.location.status is ComparisonStatus.CONSISTENT
    assert result.date.status is ComparisonStatus.UNKNOWN
    assert result.date.confidence == 0.0


def test_unresolvable_current_date_is_unknown_not_mismatch():
    context = build_video_context(
        "ver_uc",
        time=build_claim("sometime in autumn", None, 0.5, ["speech_03"]),
    )
    result = compare(context, build_source_context(date="2022-10-03"))
    assert result.date.status is ComparisonStatus.UNKNOWN
    assert result.date.confidence == 0.0


def test_missing_current_claim_is_unknown_not_mismatch():
    context = build_video_context("ver_mc", location=_claim_none(["speech_02"]))
    result = compare(context, build_source_context(location="Bangkok"))
    assert result.location.status is ComparisonStatus.UNKNOWN
    assert result.location.confidence == 0.0
    assert result.location.status is not ComparisonStatus.MISMATCH


def test_missing_source_dimension_is_unknown_not_mismatch():
    context = build_video_context("ver_ms")
    source = build_source_context(event="flood", location=None, date="2022-10-03")
    result = compare(context, source)
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.location.status is ComparisonStatus.UNKNOWN
    assert result.date.status is ComparisonStatus.MISMATCH


# --- normalized spelling / synonyms / parent-child ---


def test_normalized_spelling_equality_is_consistent():
    context = build_video_context(
        "ver_ns",
        location=build_claim("DKI Jakarta", "DKI Jakarta", 0.9, ["speech_02"]),
    )
    result = compare(context, build_source_context(location="Jakarta", date=None))
    assert result.location.status is ComparisonStatus.CONSISTENT


def test_parent_child_location_is_consistent():
    context = build_video_context(
        "ver_pc",
        location=build_claim("Jakarta", "Jakarta", 0.9, ["speech_02"]),
    )
    result = compare(context, build_source_context(location="Indonesia", date=None))
    assert result.location.status is ComparisonStatus.CONSISTENT


def test_event_synonym_is_consistent_without_fallback():
    context = build_video_context(
        "ver_sy",
        event=build_claim("banjir", "banjir", 0.9, ["speech_01"]),
    )
    result = compare(context, build_source_context(event="flood", date=None))
    assert result.event.status is ComparisonStatus.CONSISTENT


# --- semantic_equiv fallback invocation rules ---


def _recording_fallback(
    calls: list[tuple[str, str]], answer: bool | None
) -> Callable[[str, str], bool | None]:
    def fallback(a: str, b: str) -> bool | None:
        calls.append((a, b))
        return answer

    return fallback


def _failing_fallback(a: str, b: str) -> bool | None:
    raise AssertionError(f"fallback must not be called, got {(a, b)}")


def test_semantic_fallback_true_maps_to_consistent():
    context = build_video_context("ver_f1")
    calls = []
    result = compare(
        context,
        build_source_context(event="protest", location="Jakarta", date=None),
        semantic_equiv=_recording_fallback(calls, True),
    )
    assert calls == [("flood", "protest")]
    assert result.event.status is ComparisonStatus.CONSISTENT
    assert result.event.confidence == 1.0


def test_semantic_fallback_false_maps_to_mismatch():
    context = build_video_context("ver_f2")
    result = compare(
        context,
        build_source_context(event="protest", location="Jakarta", date=None),
        semantic_equiv=_recording_fallback([], False),
    )
    assert result.event.status is ComparisonStatus.MISMATCH
    assert result.event.confidence == 1.0


def test_semantic_fallback_none_maps_to_unknown():
    context = build_video_context("ver_f3")
    result = compare(
        context,
        build_source_context(event="protest", location="Jakarta", date=None),
        semantic_equiv=_recording_fallback([], None),
    )
    assert result.event.status is ComparisonStatus.UNKNOWN
    assert result.event.confidence == 0.0


def test_semantic_fallback_not_called_for_exact_match():
    context = build_video_context("ver_f4")
    result = compare(
        context,
        build_source_context(location="Jakarta", date=None),
        semantic_equiv=_failing_fallback,
    )
    assert result.event.status is ComparisonStatus.CONSISTENT


def test_semantic_fallback_not_called_for_parent_child():
    context = build_video_context(
        "ver_f5",
        location=build_claim("Jakarta", "Jakarta", 0.9, ["speech_02"]),
    )
    result = compare(
        context,
        build_source_context(location="Indonesia", date=None),
        semantic_equiv=_failing_fallback,
    )
    assert result.location.status is ComparisonStatus.CONSISTENT


def test_semantic_fallback_not_called_for_known_location_mismatch():
    # Jakarta and Bangkok are both recognized dictionary entries: the
    # mismatch is deterministic and must never consult the fallback (F15-1).
    context = build_video_context("ver_f10")  # location normalized "Jakarta, Indonesia"
    result = compare(
        context,
        build_source_context(location="Bangkok", date=None),
        semantic_equiv=_failing_fallback,
    )
    assert result.location.status is ComparisonStatus.MISMATCH
    assert result.location.confidence == 1.0


def test_semantic_fallback_not_called_for_known_parent_value_mismatch():
    # Indonesia and Thailand are both dictionary entries (parent values): the
    # mismatch is deterministic and must never consult the fallback (F15-2).
    context = build_video_context(
        "ver_f12",
        location=build_claim("Indonesia", "Indonesia", 0.9, ["speech_02"]),
    )
    result = compare(
        context,
        build_source_context(location="Thailand", date=None),
        semantic_equiv=_failing_fallback,
    )
    assert result.location.status is ComparisonStatus.MISMATCH
    assert result.location.confidence == 1.0


def test_semantic_fallback_called_for_unrecognized_location_pair():
    # "Bandung" is not a dictionary entry: the pair is unresolved, so the
    # fallback is consulted and its True maps to CONSISTENT.
    context = build_video_context(
        "ver_f11",
        location=build_claim("Bandung", None, 0.9, ["speech_02"]),
    )
    calls = []
    result = compare(
        context,
        build_source_context(location="Bangkok", date=None),
        semantic_equiv=_recording_fallback(calls, True),
    )
    assert calls == [("Bandung", "Bangkok")]
    assert result.location.status is ComparisonStatus.CONSISTENT


def test_semantic_fallback_not_called_when_side_missing():
    context = build_video_context("ver_f6", location=_claim_none(["speech_02"]))
    result = compare(
        context,
        build_source_context(location="Bangkok", date=None),
        semantic_equiv=_failing_fallback,
    )
    assert result.location.status is ComparisonStatus.UNKNOWN


def test_semantic_fallback_not_called_for_dates():
    context = build_video_context("ver_f7")  # time normalized 2026-08-15
    result = compare(
        context,
        build_source_context(location="Jakarta", date="2022-10-03"),
        semantic_equiv=_failing_fallback,
    )
    assert result.date.status is ComparisonStatus.MISMATCH


# --- reported values, confidence, evidence ids, explanations ---


def test_compared_values_use_normalized_when_present_else_raw():
    context = build_video_context(
        "ver_v",
        location=build_claim("Bandung", None, 0.9, ["speech_02"]),
    )
    result = compare(context, build_source_context(date=None))
    assert result.event.current == "flood"  # normalized == raw
    assert result.location.current == "Bandung"  # raw, no normalized value
    assert result.date.current == "2026-08-15"  # normalized ISO (raw "today")


def test_source_values_are_reported_directly():
    case = case_a()
    result = compare(case.video_context, case.source_context)
    assert result.location.source == "Bangkok"
    assert result.date.source == "2022-10-03"
    assert result.event.source == "flood"


def test_evidence_ids_come_from_current_claims():
    case = case_a()
    result = compare(case.video_context, case.source_context)
    assert result.event.evidence_ids == ["speech_01"]
    assert result.location.evidence_ids == ["speech_02"]
    assert result.date.evidence_ids == ["speech_03"]


def test_explanations_are_always_non_empty_and_include_values():
    for case in ALL_CASES:
        result = compare(case.video_context, case.source_context)
        for dim in (result.event, result.location, result.date):
            assert dim.explanation, f"{case.verification_id}: empty explanation"
            if dim.current is not None and dim.source is not None:
                assert dim.current in dim.explanation
                assert dim.source in dim.explanation


def test_unknown_statuses_keep_zero_confidence_never_mismatch():
    for case in ALL_CASES:
        result = compare(case.video_context, case.source_context)
        for dim in (result.event, result.location, result.date):
            if dim.status is ComparisonStatus.UNKNOWN:
                assert dim.confidence == 0.0
                assert dim.status is not ComparisonStatus.MISMATCH
            else:
                assert dim.confidence == 1.0
