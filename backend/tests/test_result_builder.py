"""T34 result builder tests: HANDOFF §20/§43, golden cases A/C/D, wording guard.

The builder is deterministic and makes no provider/network calls: every test
feeds a ``VideoContext`` + ``SynthesizedEvidence`` + ``ContextComparison`` +
ranked ``SourceCandidate``s and asserts the result shape, the safe wording
(§15.2/§43), and the provenance invariants (every returned source carries
``evidence_ids``; ``strongest_evidence_ids`` never cites invented IDs).
"""

import re

import pytest

from backend.schemas.evidence import (
    ConfidenceLabel,
    ResultClassification,
)
from backend.schemas.result import SourceCandidate, SourceContext, SynthesizedEvidence
from backend.services.result.result_builder import build
from backend.tests.fixtures.golden_cases import (
    build_dimension_comparison,
    case_a,
    case_c,
    case_d,
)

SAFE_SOURCE_PHRASE = "Earliest reliable match found by this system"
FORBIDDEN = (
    "original source confirmed",
    "first upload on the internet",
    "hoax",
    "fake",
)
PERCENT = re.compile(r"\d+(\.\d+)?\s?%")


def _synthesis(**overrides):
    base = dict(
        verification_id="ver_x",
        event_web_finding="supported",
        existing_fact_checks_found=True,
        best_visual_source_id="src_bangkok",
        visual_match="high",
        probable_source_context=SourceContext(
            event="flood",
            location="Bangkok",
            date="2022-10-03",
            publisher="Example News",
            source_url="https://example.com/article/bangkok-flood",
            title="Flooding in Bangkok",
        ),
        supporting_evidence_ids=["web_01"],
        contradicting_evidence_ids=[],
        conflicts=[],
        unresolved=[],
        # model wording that must never leak into result headline/summary
        synthesis_summary="MODEL-ONLY wording 93% fake hoax confirmed",
    )
    base.update(overrides)
    return SynthesizedEvidence(**base)


def _source(source_id, *, evidence_ids, **overrides):
    base = dict(
        source_id=source_id,
        url=f"https://example.com/article/{source_id}",
        canonical_url=f"https://example.com/article/{source_id}",
        publisher="Example News",
        title="Flooding in Bangkok",
        published_at="2022-10-03",
        event="flood",
        location="Bangkok",
        time_context="2022-10-03",
        match_types=["full_image_match"],
        matched_frame_ids=["kf_01"],
        origin="web_research",
        rank_score=0.95,
        evidence_ids=evidence_ids,
    )
    base.update(overrides)
    return SourceCandidate(**base)


def _build_case_a():
    golden = case_a()
    return (
        golden.video_context,
        _synthesis(
            verification_id="ver_a",
            best_visual_source_id="src_bangkok",
            supporting_evidence_ids=["web_01"],
            probable_source_context=SourceContext(
                event="flood",
                location="Bangkok",
                date="2022-10-03",
                publisher="Example News",
                source_url="https://example.com/article/bangkok-flood",
                title="Flooding in Bangkok",
            ),
        ),
        golden.expected_comparison,
        [_source("src_bangkok", evidence_ids=["web_01"])],
    )


def _build_case_c():
    golden = case_c()
    return (
        golden.video_context,
        _synthesis(
            verification_id="ver_c",
            best_visual_source_id="src_jakarta",
            supporting_evidence_ids=["web_01"],
            probable_source_context=SourceContext(
                event="flood",
                location="Jakarta",
                date="2022-10-03",
                publisher="Example News",
                source_url="https://example.com/article/jakarta-flood-2022",
                title="Flooding in Jakarta",
            ),
        ),
        golden.expected_comparison,
        [
            _source(
                "src_jakarta",
                evidence_ids=["web_01"],
                location="Jakarta",
                time_context="2022-10-03",
                title="Flooding in Jakarta",
            )
        ],
    )


def _build_case_d():
    golden = case_d()
    return (
        golden.video_context,
        _synthesis(
            verification_id="ver_d",
            event_web_finding="insufficient",
            existing_fact_checks_found=False,
            best_visual_source_id=None,
            visual_match="unknown",
            probable_source_context=None,
            supporting_evidence_ids=[],
            unresolved=["No reliable source found to compare against."],
        ),
        golden.expected_comparison,
        [],
    )


def _assert_safe_wording(result) -> None:
    for field in ("headline", "summary"):
        text = getattr(result, field)
        lowered = text.lower()
        for phrase in FORBIDDEN:
            assert phrase not in lowered, f"{field} uses forbidden wording {phrase!r}: {text!r}"
        assert PERCENT.search(text) is None, f"{field} contains a percentage: {text!r}"


# --- golden cases (HANDOFF §36/§43) ---


def test_case_a_false_context_shape():
    context, synthesis, comparison, sources = _build_case_a()
    result = build(context, synthesis, comparison, sources)
    assert result.classification is ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert result.evidence_confidence is ConfidenceLabel.HIGH
    assert result.visual_match == "high"
    assert "location_changed" in result.manipulation_types
    assert "old_footage_reused" in result.manipulation_types
    assert "date_changed" in result.manipulation_types
    assert result.source_context is not None
    assert [s.source_id for s in result.sources] == ["src_bangkok"]


def test_case_c_consistent_shape():
    context, synthesis, comparison, sources = _build_case_c()
    result = build(context, synthesis, comparison, sources)
    assert result.classification is ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    assert result.manipulation_types == []
    assert result.source_context is not None


def test_case_d_insufficient_never_false_context():
    context, synthesis, comparison, sources = _build_case_d()
    result = build(context, synthesis, comparison, sources)
    assert result.classification is ResultClassification.INSUFFICIENT_EVIDENCE
    assert result.classification is not ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert result.evidence_confidence is ConfidenceLabel.LOW
    assert result.source_context is None
    assert result.sources == []
    assert result.strongest_evidence_ids == []
    assert result.unresolved


# --- mismatch -> manipulation_types mapping ---


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        ("event", "event_changed"),
        ("location", "location_changed"),
        ("date", "date_changed"),
    ],
)
def test_mismatch_dimension_maps_to_manipulation_type(dimension, expected):
    golden = case_c()  # all-consistent baseline
    comparison = golden.expected_comparison.model_copy(deep=True)
    setattr(
        comparison,
        dimension,
        build_dimension_comparison(
            "mismatch",
            current=f"cur_{dimension}",
            source=f"src_{dimension}",
            explanation=f"{dimension} differs.",
        ),
    )
    context, synthesis, _, _ = _build_case_c()
    result = build(context, synthesis, comparison, [])
    assert expected in result.manipulation_types
    assert result.classification is ResultClassification.POSSIBLE_FALSE_CONTEXT


def test_all_consistent_has_no_manipulation_types():
    context, synthesis, comparison, sources = _build_case_c()
    result = build(context, synthesis, comparison, sources)
    assert result.manipulation_types == []


def test_old_footage_reused_requires_mismatch_and_reliable_visual():
    golden = case_a()
    # weak visual: mismatches still map, but no old-footage claim
    result = build(
        golden.video_context,
        _synthesis(
            verification_id="ver_a",
            visual_match="low",
            best_visual_source_id=None,
            probable_source_context=None,
        ),
        golden.expected_comparison,
        [],
    )
    assert "location_changed" in result.manipulation_types
    assert "old_footage_reused" not in result.manipulation_types


# --- wording guard (§15.2/§43) ---


@pytest.mark.parametrize("case_builder", ["_build_case_a", "_build_case_c", "_build_case_d"])
def test_wording_guard_no_forbidden_phrases_or_percentages(case_builder):
    context, synthesis, comparison, sources = globals()[case_builder]()
    result = build(context, synthesis, comparison, sources)
    _assert_safe_wording(result)


def test_model_wording_never_leaks_into_result_fields():
    context, synthesis, comparison, sources = _build_case_a()
    result = build(context, synthesis, comparison, sources)
    assert "MODEL-ONLY" not in result.summary
    assert "MODEL-ONLY" not in result.headline


def test_safe_source_phrase_when_source_presented():
    context, synthesis, comparison, sources = _build_case_a()
    result = build(context, synthesis, comparison, sources)
    assert SAFE_SOURCE_PHRASE in result.summary
    assert "Original source" not in result.summary


def test_no_source_phrase_without_source():
    context, synthesis, comparison, sources = _build_case_d()
    result = build(context, synthesis, comparison, sources)
    assert SAFE_SOURCE_PHRASE not in result.summary


# --- provenance invariants ---


def test_sources_without_evidence_ids_are_excluded():
    context, synthesis, comparison, _ = _build_case_a()
    result = build(
        context,
        synthesis,
        comparison,
        [
            _source("src_with_evidence", evidence_ids=["web_01"]),
            _source(
                "src_demo",
                evidence_ids=[],
                origin="demo_index",
                match_types=["visually_similar"],
            ),
        ],
    )
    assert [s.source_id for s in result.sources] == ["src_with_evidence"]
    assert all(s.evidence_ids for s in result.sources)
    assert any("src_demo" in note for note in result.unresolved)


def test_strongest_evidence_ids_subset_of_known_pool():
    context, synthesis, comparison, sources = _build_case_a()
    synthesis = synthesis.model_copy(
        update={"contradicting_evidence_ids": ["fc_01"], "supporting_evidence_ids": ["web_01"]}
    )
    result = build(context, synthesis, comparison, sources)
    assert result.strongest_evidence_ids
    pool = {eid for s in result.sources for eid in s.evidence_ids}
    pool |= set(synthesis.supporting_evidence_ids) | set(synthesis.contradicting_evidence_ids)
    assert set(result.strongest_evidence_ids) <= pool


@pytest.mark.parametrize("case_builder", ["_build_case_a", "_build_case_c", "_build_case_d"])
def test_returned_sources_always_carry_evidence_ids(case_builder):
    context, synthesis, comparison, sources = globals()[case_builder]()
    result = build(context, synthesis, comparison, sources)
    for source in result.sources:
        assert source.evidence_ids, f"{source.source_id} returned without evidence provenance"
