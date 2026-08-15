"""T14 source ranker: deterministic heuristic scoring + descending stable sort.

Covers: visual max-over-match-types, strict-before precedence (never inferred
from missing dates), metadata/context/quality/cross-frame components, exact
weighted formula, breakdown keys, field mutation, and stable ordering.
"""

from datetime import date
from typing import Any

import pytest

from backend.schemas.result import SourceCandidate
from backend.services.evidence.source_ranker import candidate_score, rank

CURRENT = date(2026, 8, 15)


def make_candidate(source_id: str = "src_test", **overrides: Any) -> SourceCandidate:
    base: dict[str, Any] = dict(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        canonical_url=f"https://example.com/{source_id}",
    )
    base.update(overrides)
    return SourceCandidate(**base)


# --- visual component -----------------------------------------------------


@pytest.mark.parametrize(
    ("match_types", "visual", "total"),
    [
        (["full_image_match"], 1.0, 0.45),
        (["partial_image_match"], 0.8, 0.36),
        (["page_match"], 0.6, 0.27),
        (["visually_similar"], 0.3, 0.135),
    ],
)
def test_visual_score_by_match_type(match_types, visual, total):
    candidate = make_candidate(match_types=match_types)
    score, breakdown = candidate_score(candidate, CURRENT)

    assert breakdown["visual"] == visual
    assert score == pytest.approx(total)
    assert candidate.rank_score == pytest.approx(total)


def test_visual_zero_when_no_match_type():
    score, breakdown = candidate_score(make_candidate(), CURRENT)
    assert breakdown["visual"] == 0.0
    assert score == 0.0


def test_visual_takes_max_of_present_match_types():
    candidate = make_candidate(match_types=["visually_similar", "full_image_match"])
    _, breakdown = candidate_score(candidate, CURRENT)
    assert breakdown["visual"] == 1.0

    candidate = make_candidate(match_types=["partial_image_match", "page_match"])
    _, breakdown = candidate_score(candidate, CURRENT)
    assert breakdown["visual"] == 0.8


def test_visual_ignores_non_visual_match_types():
    candidate = make_candidate(match_types=["high", "frame:kf_01", "provider:google_vision"])
    _, breakdown = candidate_score(candidate, CURRENT)
    assert breakdown["visual"] == 0.0


# --- precedence component --------------------------------------------------


@pytest.mark.parametrize(
    "published_at",
    [None, "2026-08-15", "2026-08-16", "not a date", ""],
)
def test_precedence_zero_unless_strictly_before(published_at):
    _, breakdown = candidate_score(make_candidate(published_at=published_at), CURRENT)
    assert breakdown["precedence"] == 0.0


def test_precedence_one_when_strictly_before():
    _, breakdown = candidate_score(make_candidate(published_at="2026-08-14"), CURRENT)
    assert breakdown["precedence"] == 1.0


@pytest.mark.parametrize(
    ("published_at", "expected"),
    [("yesterday", 1.0), ("today", 0.0)],
)
def test_precedence_resolves_relative_dates(published_at, expected):
    _, breakdown = candidate_score(make_candidate(published_at=published_at), CURRENT)
    assert breakdown["precedence"] == expected


# --- metadata / context / quality / cross-frame components ----------------


@pytest.mark.parametrize("completeness", [0.0, 0.5, 1.0])
def test_metadata_component_uses_completeness(completeness):
    _, breakdown = candidate_score(
        make_candidate(metadata_completeness=completeness), CURRENT
    )
    assert breakdown["metadata"] == completeness


def test_metadata_component_none_is_zero():
    _, breakdown = candidate_score(make_candidate(), CURRENT)
    assert breakdown["metadata"] == 0.0


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (dict(event="flood", location="Jakarta", time_context="2026"), 1.0),
        (dict(event="flood", location="Jakarta"), 0.5),
        (dict(event="flood"), 0.5),
        (dict(), 0.0),
    ],
    ids=["three", "two", "one", "none"],
)
def test_context_component_counts_present_fields(overrides, expected):
    _, breakdown = candidate_score(make_candidate(**overrides), CURRENT)
    assert breakdown["context"] == expected


def test_source_quality_component():
    _, breakdown = candidate_score(make_candidate(source_quality=0.9), CURRENT)
    assert breakdown["source_quality"] == 0.9

    _, breakdown = candidate_score(make_candidate(), CURRENT)
    assert breakdown["source_quality"] == 0.0


@pytest.mark.parametrize(
    ("frame_ids", "expected"),
    [([], 0.0), (["kf_01"], 1 / 3), (["kf_01", "kf_02"], 2 / 3), (["kf_01", "kf_02", "kf_03"], 1.0), (["kf_01", "kf_02", "kf_03", "kf_04", "kf_05", "kf_06"], 1.0)],
)
def test_cross_frame_component(frame_ids, expected):
    _, breakdown = candidate_score(make_candidate(matched_frame_ids=frame_ids), CURRENT)
    assert breakdown["cross_frame"] == pytest.approx(expected)


# --- exact formula and breakdown ------------------------------------------


def test_exact_weighted_formula_and_breakdown_keys():
    candidate = make_candidate(
        match_types=["partial_image_match"],
        published_at="2026-08-14",
        metadata_completeness=0.5,
        event="flood",
        location="Jakarta",
        time_context="2026",
        source_quality=0.9,
        matched_frame_ids=["kf_01", "kf_02"],
    )
    score, breakdown = candidate_score(candidate, CURRENT)

    expected_breakdown = {
        "visual": 0.8,
        "precedence": 1.0,
        "metadata": 0.5,
        "context": 1.0,
        "source_quality": 0.9,
        "cross_frame": 2 / 3,
    }
    expected_total = (
        0.45 * 0.8 + 0.20 * 1.0 + 0.10 * 0.5 + 0.10 * 1.0 + 0.10 * 0.9 + 0.05 * (2 / 3)
    )
    assert breakdown == pytest.approx(expected_breakdown)
    assert list(breakdown) == ["visual", "precedence", "metadata", "context", "source_quality", "cross_frame"]
    assert score == pytest.approx(expected_total)
    assert isinstance(score, float)
    assert all(isinstance(v, float) for v in breakdown.values())


def test_candidate_score_mutates_candidate_fields():
    candidate = make_candidate(match_types=["full_image_match"], published_at="2026-08-14")
    score, breakdown = candidate_score(candidate, CURRENT)

    assert candidate.rank_score == score
    assert candidate.score_breakdown == breakdown
    assert isinstance(candidate.rank_score, float)


# --- rank(): mutation + descending stable sort -----------------------------


def test_rank_scores_mutates_and_sorts_descending():
    candidates = [
        make_candidate("c_low", match_types=["visually_similar"]),
        make_candidate("c_high", match_types=["full_image_match"], published_at="2026-08-14"),
        make_candidate("c_mid", match_types=["page_match"], metadata_completeness=0.5),
    ]
    ranked = rank(candidates, CURRENT)

    assert [c.source_id for c in ranked] == ["c_high", "c_mid", "c_low"]
    assert all(c.rank_score is not None and c.score_breakdown for c in ranked)
    assert [c.rank_score for c in ranked] == sorted(
        (c.rank_score for c in ranked), reverse=True
    )


def test_rank_stable_for_ties():
    first = make_candidate("first", match_types=["full_image_match"])
    second = make_candidate("second", match_types=["full_image_match"])
    ranked = rank([first, second], CURRENT)

    assert [c.source_id for c in ranked] == ["first", "second"]
    assert ranked[0].rank_score == ranked[1].rank_score


def test_rank_empty_list():
    assert rank([], CURRENT) == []


# --- brief-specified orderings ---------------------------------------------


def test_visual_high_beats_full_metadata_without_visual():
    visual_only = make_candidate("v", match_types=["full_image_match"])
    metadata_only = make_candidate(
        "m",
        metadata_completeness=1.0,
        event="flood",
        location="Jakarta",
        time_context="2026",
        source_quality=1.0,
        matched_frame_ids=["kf_01", "kf_02", "kf_03"],
    )
    ranked = rank([metadata_only, visual_only], CURRENT)

    assert [c.source_id for c in ranked] == ["v", "m"]


def test_cross_frame_raises_score():
    one_frame = make_candidate("one", match_types=["full_image_match"], matched_frame_ids=["kf_01"])
    two_frame = make_candidate("two", match_types=["full_image_match"], matched_frame_ids=["kf_01", "kf_02"])
    ranked = rank([one_frame, two_frame], CURRENT)

    assert [c.source_id for c in ranked] == ["two", "one"]
    assert ranked[0].rank_score > ranked[1].rank_score
