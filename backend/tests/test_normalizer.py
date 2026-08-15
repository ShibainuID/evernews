"""T13 normalizer: RawValidationBundle -> deduplicated list[SourceCandidate].

Covers: evidence_ids per origin, canonical-URL dedupe with merge, source
quality heuristic (HANDOFF 13.4), metadata_completeness fraction, missing
dates, page-match fetcher injection, and visual match_types.
"""

from typing import Any

import pytest

from backend.schemas.result import SourceCandidate
from backend.services.evidence.normalizer import (
    build_source_candidates,
    match_strength,
    metadata_completeness,
    source_quality,
)
from backend.tests.fixtures.golden_cases import (
    build_bundle,
    build_fact_check,
    build_visual_candidate,
    build_video_context,
    build_web_research,
    build_web_source,
)
from backend.utils.fetch import SafeFetchResult


class RecordingFetcher:
    """Injected fetcher boundary: records URLs, never touches the network."""

    def __init__(self, *, fail: bool = False):
        self.calls: list[str] = []
        self._fail = fail

    async def __call__(self, url: str) -> SafeFetchResult:
        self.calls.append(url)
        if self._fail:
            raise RuntimeError("network down")
        return SafeFetchResult(url=url, status=200, body=b"<html>ok</html>", truncated=False)


def make_context(verification_id: str = "ver_t13"):
    return build_video_context(verification_id)


# --- empty bundle ---------------------------------------------------------


async def test_empty_bundle_yields_no_candidates():
    bundle = build_bundle("ver_t13")
    assert await build_source_candidates(make_context(), bundle, RecordingFetcher()) == []


# --- all three origins, evidence_ids, URL fields --------------------------


async def test_candidates_built_from_all_three_origins_with_evidence_ids():
    bundle = build_bundle(
        "ver_t13",
        fact_checks=[
            build_fact_check("fc_01", "Jakarta banjir 2026", review_url="https://a.example/fc")
        ],
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [build_web_source("web_01", "https://b.example/article")],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_01", "kf_01", url="https://c.example/img")],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert [c.origin for c in candidates] == ["fact_check", "web_research", "web"]
    assert [c.evidence_ids for c in candidates] == [["fc_01"], ["web_01"], ["vis_01"]]
    assert [c.url for c in candidates] == [
        "https://a.example/fc",
        "https://b.example/article",
        "https://c.example/img",
    ]
    assert [c.canonical_url for c in candidates] == [
        "https://a.example/fc",
        "https://b.example/article",
        "https://c.example/img",
    ]
    assert all(c.source_id.startswith("src_") for c in candidates)
    # deterministic ids: same bundle in, same ids out
    again = await build_source_candidates(make_context(), bundle, RecordingFetcher())
    assert [c.source_id for c in again] == [c.source_id for c in candidates]


# --- dedupe by canonical URL ----------------------------------------------


async def test_visual_duplicates_with_same_canonical_url_dedupe_to_one():
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                url="https://example.com/article?utm_source=twitter&utm_campaign=x",
            ),
            build_visual_candidate("vis_02", "kf_02", url="https://example.com/article#section"),
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.canonical_url == "https://example.com/article"
    assert candidate.url == "https://example.com/article?utm_source=twitter&utm_campaign=x"
    assert candidate.evidence_ids == ["vis_01", "vis_02"]
    assert candidate.matched_frame_ids == ["kf_01", "kf_02"]


async def test_duplicate_merge_dedupes_match_lists_and_scores():
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[
            build_visual_candidate("vis_01", "kf_01", provider_score=0.95),
            build_visual_candidate("vis_02", "kf_02", provider_score=0.95),
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.match_types == [
        "full_image_match",
        "high",
        "frame:kf_01",
        "provider:google_vision",
        "provider_score:0.95",
        "frame:kf_02",
    ]
    assert candidate.provider_scores == [0.95]
    assert candidate.matched_frame_ids == ["kf_01", "kf_02"]


async def test_merge_preserves_first_url_and_fills_missing_metadata_from_later_duplicate():
    bundle = build_bundle(
        "ver_t13",
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [
                    build_web_source(
                        "web_01",
                        "https://example.com/article/bangkok-flood",
                        publisher="Example News",
                        title="Flooding in Bangkok",
                        published_at="2022-10-03",
                        event="flood",
                        location="Bangkok",
                        date_context="2022-10-03",
                        relevant_excerpt="Heavy rain flooded Bangkok streets.",
                    )
                ],
            )
        ],
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                url="https://example.com/article/bangkok-flood",
                page_title="Flooding in Bangkok",
            )
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.evidence_ids == ["web_01", "vis_01"]
    assert candidate.title == "Flooding in Bangkok"
    assert candidate.publisher == "Example News"
    assert candidate.published_at == "2022-10-03"
    assert candidate.metadata_completeness == pytest.approx(1.0)
    assert candidate.matched_frame_ids == ["kf_01"]
    assert "full_image_match" in candidate.match_types


# --- earliest_known_date --------------------------------------------------


async def test_earliest_known_date_comes_from_publication_date():
    bundle = build_bundle(
        "ver_t13",
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [build_web_source("web_01", "https://b.example/article", published_at="2022-10-03")],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_01", "kf_01", url="https://c.example/img")],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    by_origin = {c.origin: c for c in candidates}
    assert by_origin["web_research"].earliest_known_date == "2022-10-03"
    assert by_origin["web"].earliest_known_date is None


async def test_merged_duplicates_take_the_earliest_date():
    bundle = build_bundle(
        "ver_t13",
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [
                    build_web_source("web_01", "https://example.com/article", published_at="2022-10-03"),
                    build_web_source("web_02", "https://example.com/article?utm_source=x", published_at="2022-10-01"),
                ],
            )
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert len(candidates) == 1
    assert candidates[0].earliest_known_date == "2022-10-01"


# --- source quality heuristic (HANDOFF 13.4) ------------------------------


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        ("government", 1.0),
        ("official", 1.0),
        ("news", 0.9),
        ("fact_check", 0.9),
        ("blog", 0.5),
        ("community", 0.5),
        ("aggregator", 0.3),
        ("repost", 0.3),
        (None, 0.3),
        ("", 0.3),
    ],
)
def test_source_quality_heuristic_values(source_type, expected):
    assert source_quality(source_type=source_type) == expected


@pytest.mark.parametrize(
    ("publisher", "domain", "expected"),
    [
        (None, "kominfo.go.id", 1.0),
        (None, "whitehouse.gov", 1.0),
        (None, "example.com", 0.3),
        ("Kominfo Official Site", "example.com", 1.0),
        ("Tempo Cek Fakta", "tempo.co", 0.9),
        ("Example Fact Check", "example.com", 0.9),
    ],
)
def test_source_quality_domain_and_publisher_signals(publisher, domain, expected):
    assert source_quality(source_type=None, publisher=publisher, domain=domain) == expected


async def test_source_quality_applied_to_candidates():
    bundle = build_bundle(
        "ver_t13",
        fact_checks=[
            build_fact_check(
                "fc_01",
                "Jakarta banjir 2026",
                review_url="https://a.example/fc",
                publisher="Example Fact Check",
            )
        ],
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [
                    build_web_source(
                        "web_01",
                        "https://publik.go.id/berita",
                        publisher="Kominfo",
                        source_type="government",
                    )
                ],
            )
        ],
        visual_candidates=[build_visual_candidate("vis_01", "kf_01", url="https://c.example/img")],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    by_origin = {c.origin: c for c in candidates}
    assert by_origin["fact_check"].source_quality == 0.9
    assert by_origin["web_research"].source_quality == 1.0
    assert by_origin["web"].source_quality == 0.3


# --- metadata_completeness ------------------------------------------------


def _candidate(**overrides: Any) -> SourceCandidate:
    base: dict[str, Any] = dict(
        source_id="src_test",
        url="https://example.com/article",
        canonical_url="https://example.com/article",
        publisher="Example News",
        title="Flooding in Bangkok",
        published_at="2022-10-03",
        event="flood",
        location="Bangkok",
        time_context="2022-10-03",
        description="Heavy rain flooded Bangkok streets.",
    )
    base.update(overrides)
    return SourceCandidate(**base)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (dict(), 1.0),
        (dict(event=None, location=None, time_context=None, description=None), 3 / 7),
        (dict(publisher=None, title=None, published_at=None, event=None, location=None, time_context=None, description=None), 0.0),
    ],
)
def test_metadata_completeness_fraction(overrides, expected):
    assert metadata_completeness(_candidate(**overrides)) == pytest.approx(expected)


async def test_metadata_completeness_on_sparse_web_source():
    bundle = build_bundle(
        "ver_t13",
        web_research=[
            build_web_research(
                "web_task_01",
                "When was this footage first published?",
                "Bangkok flooding from October 2022.",
                [
                    build_web_source(
                        "web_01",
                        "https://b.example/article",
                        publisher="Example News",
                        title="Flooding in Bangkok",
                        published_at="2022-10-03",
                    )
                ],
            )
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert candidates[0].metadata_completeness == pytest.approx(3 / 7)


# --- page-match fetch injection -------------------------------------------


async def test_page_match_invokes_injected_fetcher_with_page_url():
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                candidate_type="page_match",
                url="https://img.example.com/photo.jpg",
                page_url="https://example.com/album/photo",
            ),
            build_visual_candidate("vis_02", "kf_02", url="https://img.example.com/other.jpg"),
        ],
    )
    fetcher = RecordingFetcher()
    await build_source_candidates(make_context(), bundle, fetcher)

    assert fetcher.calls == ["https://example.com/album/photo"]


async def test_page_match_without_page_url_fetches_the_candidate_url():
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                candidate_type="page_match",
                url="https://example.com/gallery/photo",
            )
        ],
    )
    fetcher = RecordingFetcher()
    await build_source_candidates(make_context(), bundle, fetcher)

    assert fetcher.calls == ["https://example.com/gallery/photo"]


async def test_fetch_failure_does_not_abort_normalization():
    bundle = build_bundle(
        "ver_t13",
        fact_checks=[
            build_fact_check("fc_01", "Jakarta banjir 2026", review_url="https://a.example/fc")
        ],
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                candidate_type="page_match",
                url="https://img.example.com/photo.jpg",
                page_url="https://example.com/album/photo",
            )
        ],
    )
    fetcher = RecordingFetcher(fail=True)
    candidates = await build_source_candidates(make_context(), bundle, fetcher)

    assert {c.origin for c in candidates} == {"fact_check", "web"}
    assert "page_match" in candidates[1].match_types


# --- visual match_types ---------------------------------------------------


@pytest.mark.parametrize(
    ("candidate_type", "strength"),
    [
        ("full_image_match", "high"),
        ("partial_image_match", "medium"),
        ("page_match", "medium"),
        ("visually_similar", "low"),
    ],
)
def test_match_strength_mapping(candidate_type, strength):
    assert match_strength(candidate_type) == strength


@pytest.mark.parametrize(
    ("candidate_type", "strength"),
    [
        ("full_image_match", "high"),
        ("partial_image_match", "medium"),
        ("page_match", "medium"),
        ("visually_similar", "low"),
    ],
)
async def test_visual_match_types_retain_type_strength_frame_and_provider(candidate_type, strength):
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[
            build_visual_candidate(
                "vis_01",
                "kf_01",
                candidate_type=candidate_type,
                provider_score=0.95,
            )
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert candidates[0].match_types == [
        candidate_type,
        strength,
        "frame:kf_01",
        "provider:google_vision",
        "provider_score:0.95",
    ]


async def test_visual_candidate_without_provider_score_has_no_score_entry():
    bundle = build_bundle(
        "ver_t13",
        visual_candidates=[build_visual_candidate("vis_01", "kf_01", provider_score=None)],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert candidates[0].match_types == ["full_image_match", "high", "frame:kf_01", "provider:google_vision"]
    assert candidates[0].provider_scores == []


# --- stable input order ---------------------------------------------------


async def test_candidate_order_follows_bundle_input_order():
    bundle = build_bundle(
        "ver_t13",
        fact_checks=[
            build_fact_check("fc_01", "q", review_url="https://a.example/fc1"),
            build_fact_check("fc_02", "q", review_url="https://a.example/fc2"),
        ],
        web_research=[
            build_web_research(
                "web_task_01",
                "q",
                "finding",
                [
                    build_web_source("web_01", "https://b.example/1"),
                    build_web_source("web_02", "https://b.example/2"),
                ],
            )
        ],
        visual_candidates=[
            build_visual_candidate("vis_01", "kf_01", url="https://c.example/1"),
            build_visual_candidate("vis_02", "kf_02", url="https://c.example/2"),
        ],
    )
    candidates = await build_source_candidates(make_context(), bundle, RecordingFetcher())

    assert [c.evidence_ids[0] for c in candidates] == [
        "fc_01",
        "fc_02",
        "web_01",
        "web_02",
        "vis_01",
        "vis_02",
    ]
