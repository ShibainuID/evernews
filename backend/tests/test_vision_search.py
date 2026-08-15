"""T30: Google Vision Web Detection branch tests (TDD red-green).

Fake ``google.cloud.vision`` client only — no network, no ADC credentials.
Pins: category normalization (full / partial / page / visually-similar),
zero matches (valid no-match, never a fake-footage verdict, no truth/falsity
flags anywhere), canonical-URL dedupe keeping the strongest category
(full > partial > page > similar), multi-frame continuation after one frame
failure, all-frames-fail branch error propagation, local-byte request
behavior (never a public URL), 15-20s per-frame timeout wiring (client +
service), lazy client creation, per-frame candidate cap, and deterministic
frame/candidate ids.
"""

import time

import pytest
from google.cloud.vision_v1 import types

from backend.providers.google_vision import GoogleVisionProvider
from backend.schemas.evidence import KeyframeRef
from backend.schemas.investigation import VisualSearchTask, VisualWebCandidate
from backend.services.validation import vision_search
from backend.services.validation.vision_search import run_visual_task


# --- fakes ---


class FakeVisionClient:
    """Scripted ``AnnotateImageResponse``/``Exception``; records (content, timeout)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[tuple[bytes, float | None]] = []

    def web_detection(self, image, timeout=None):
        self.calls.append((image.content, timeout))
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _provider(script):
    client = FakeVisionClient(script)
    return GoogleVisionProvider(client_factory=lambda: client), client


def _resp(**web_fields):
    return types.AnnotateImageResponse(web_detection=types.WebDetection(**web_fields))


def _img(url, score=None):
    kwargs = {"url": url}
    if score is not None:
        kwargs["score"] = score
    return types.WebDetection.WebImage(**kwargs)


def _page(url, title=None):
    kwargs = {"url": url}
    if title is not None:
        kwargs["page_title"] = title
    return types.WebDetection.WebPage(**kwargs)


def _err_resp(message="vision api error"):
    resp = types.AnnotateImageResponse()
    resp.error.message = message
    return resp


class ScriptedVisionProvider:
    """Scripted candidate lists per frame; records each image_bytes call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls: list[bytes] = []

    def web_detection(self, image_bytes):
        self.calls.append(image_bytes)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _cand(url, candidate_type, score=None, title=None):
    return VisualWebCandidate(
        candidate_id="",
        frame_id="",
        candidate_type=candidate_type,
        url=url,
        provider_score=score,
        page_title=title,
        raw_provider_type="RAW_TYPE",
    )


def _task(frame_ids=("f1", "f2"), max_candidates=10):
    return VisualSearchTask(
        task_id="vs_01",
        frame_ids=list(frame_ids),
        goal="trace footage",
        max_candidates_per_frame=max_candidates,
    )


def _keyframes(tmp_path, frame_ids=("f1", "f2")):
    refs = []
    for i, frame_id in enumerate(frame_ids):
        path = tmp_path / f"{frame_id}.jpg"
        path.write_bytes(f"bytes-{frame_id}".encode())
        refs.append(
            KeyframeRef(frame_id=frame_id, timestamp_sec=float(i), local_path=str(path))
        )
    return refs


# --- provider: category normalization (HANDOFF §10.4) ---


def test_full_match_category_normalized():
    provider, _ = _provider([_resp(full_matching_images=[_img("https://x/a.jpg", 0.9)])])

    result = provider.web_detection(b"img")

    assert len(result) == 1
    assert result[0].candidate_type == "full_image_match"
    assert result[0].url == "https://x/a.jpg"
    assert result[0].provider_score == pytest.approx(0.9)
    assert result[0].raw_provider_type == "full_matching_images"


def test_partial_match_category_normalized():
    provider, _ = _provider([_resp(partial_matching_images=[_img("https://x/crop.jpg")])])

    (candidate,) = provider.web_detection(b"img")

    assert candidate.candidate_type == "partial_image_match"
    assert candidate.raw_provider_type == "partial_matching_images"


def test_page_match_category_normalized():
    provider, _ = _provider(
        [_resp(pages_with_matching_images=[_page("https://x/article", title="Article")])]
    )

    (candidate,) = provider.web_detection(b"img")

    assert candidate.candidate_type == "page_match"
    assert candidate.url == "https://x/article"
    assert candidate.page_title == "Article"
    assert candidate.raw_provider_type == "pages_with_matching_images"


def test_visually_similar_category_normalized():
    provider, _ = _provider([_resp(visually_similar_images=[_img("https://x/sim.jpg", 0.4)])])

    (candidate,) = provider.web_detection(b"img")

    assert candidate.candidate_type == "visually_similar"
    assert candidate.provider_score == pytest.approx(0.4)


def test_mixed_categories_all_normalized():
    provider, _ = _provider(
        [
            _resp(
                full_matching_images=[_img("https://x/full.jpg")],
                partial_matching_images=[_img("https://x/part.jpg")],
                pages_with_matching_images=[_page("https://x/page")],
                visually_similar_images=[_img("https://x/sim.jpg")],
            )
        ]
    )

    result = provider.web_detection(b"img")

    assert [c.candidate_type for c in result] == [
        "full_image_match",
        "partial_image_match",
        "page_match",
        "visually_similar",
    ]


def test_zero_matches_returns_empty():
    provider, _ = _provider([_resp()])

    assert provider.web_detection(b"img") == []


def test_api_error_message_raises():
    provider, _ = _provider([_err_resp("vision down")])

    with pytest.raises(RuntimeError, match="vision down"):
        provider.web_detection(b"img")


# --- provider: lazy client, local bytes, timeout ---


def test_client_created_lazily_and_reused():
    provider, client = _provider([_resp(), _resp()])

    assert client.calls == []  # client factory not invoked at construction

    provider.web_detection(b"a")
    provider.web_detection(b"b")

    assert len(client.calls) == 2  # one client, reused


def test_request_sends_local_bytes_with_bounded_timeout():
    provider, client = _provider([_resp()])

    provider.web_detection(b"local-frame-bytes")

    ((content, timeout),) = client.calls
    assert content == b"local-frame-bytes"  # local image bytes, never a public URL
    assert 15 <= timeout <= 20  # design §7: 15-20s per frame


# --- service: run_visual_task ---


async def test_all_categories_flow_with_frame_ids(tmp_path):
    refs = _keyframes(tmp_path, ("f1",))
    provider = ScriptedVisionProvider(
        [
            [
                _cand("https://x/f.jpg", "full_image_match"),
                _cand("https://x/p.jpg", "partial_image_match"),
                _cand("https://x/pg", "page_match", title="T"),
                _cand("https://x/s.jpg", "visually_similar"),
            ]
        ]
    )

    result = await run_visual_task(_task(("f1",)), refs, provider)

    assert [c.candidate_type for c in result] == [
        "full_image_match",
        "partial_image_match",
        "page_match",
        "visually_similar",
    ]
    assert {c.frame_id for c in result} == {"f1"}
    assert result[2].page_title == "T"
    assert provider.calls == [b"bytes-f1"]  # local path bytes, not public_url


def test_candidate_schema_carries_no_truth_falsity_flags():
    (dump,) = [
        c.model_dump()
        for c in [
            VisualWebCandidate(
                candidate_id="vw_x",
                frame_id="f1",
                candidate_type="visually_similar",
                url="https://x/sim.jpg",
                provider_score=0.4,
                raw_provider_type="RAW_TYPE",
            )
        ]
    ]

    # Exact field set: visually_similar is discovery only, and no candidate
    # ever classifies footage as fake or adds a truth/falsity flag.
    assert set(dump) == {
        "candidate_id",
        "frame_id",
        "candidate_type",
        "url",
        "page_url",
        "page_title",
        "provider_score",
        "raw_provider_type",
    }


async def test_zero_matches_returns_empty_no_verdict(tmp_path):
    refs = _keyframes(tmp_path, ("f1",))
    provider = ScriptedVisionProvider([[]])

    result = await run_visual_task(_task(("f1",)), refs, provider)

    assert result == []  # origin unknown; valid no-match, nothing to flag


async def test_duplicate_url_dedupes_by_canonical_key(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/a.jpg?utm_source=tw", "partial_image_match")],
            [_cand("https://x/a.jpg", "full_image_match")],
        ]
    )

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert len(result) == 1
    assert result[0].candidate_type == "full_image_match"  # strongest wins
    assert result[0].url == "https://x/a.jpg"


async def test_category_strength_full_beats_partial_beats_page_beats_similar(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2", "f3", "f4"))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/a.jpg", "page_match")],
            [_cand("https://x/a.jpg", "visually_similar")],
            [_cand("https://x/a.jpg", "partial_image_match")],
            [_cand("https://x/a.jpg", "full_image_match")],
        ]
    )

    result = await run_visual_task(_task(("f1", "f2", "f3", "f4")), refs, provider)

    assert result[0].candidate_type == "full_image_match"


async def test_same_strength_duplicate_keeps_first_frame(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/a.jpg", "page_match")],
            [_cand("https://x/a.jpg", "page_match")],
        ]
    )

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert len(result) == 1
    assert result[0].frame_id == "f1"  # deterministic: first occurrence


async def test_distinct_urls_all_kept(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/1.jpg", "full_image_match")],
            [_cand("https://y/2.jpg", "full_image_match")],
        ]
    )

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert [(c.frame_id, c.url) for c in result] == [
        ("f1", "https://x/1.jpg"),
        ("f2", "https://y/2.jpg"),
    ]


async def test_multi_frame_order_follows_task_frame_ids(tmp_path):
    refs = _keyframes(tmp_path, ("f2", "f1"))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/2.jpg", "full_image_match")],
            [_cand("https://x/1.jpg", "full_image_match")],
        ]
    )

    result = await run_visual_task(_task(("f2", "f1")), refs, provider)

    assert [c.frame_id for c in result] == ["f2", "f1"]  # task.frame_ids order


async def test_frame_provider_failure_skipped_others_continue(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider(
        [RuntimeError("vision down for f1"), [_cand("https://x/2.jpg", "full_image_match")]]
    )

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert [c.frame_id for c in result] == ["f2"]


async def test_unreadable_frame_skipped_others_continue(tmp_path):
    refs = [
        KeyframeRef(frame_id="f1", timestamp_sec=0.0, local_path=str(tmp_path / "nope.jpg")),
        KeyframeRef(frame_id="f2", timestamp_sec=1.0, local_path=str(tmp_path / "f2.jpg")),
    ]
    (tmp_path / "f2.jpg").write_bytes(b"f2")
    provider = ScriptedVisionProvider([[_cand("https://x/2.jpg", "full_image_match")]])

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert [c.frame_id for c in result] == ["f2"]


async def test_missing_keyframe_ref_skipped(tmp_path):
    refs = _keyframes(tmp_path, ("f2",))
    provider = ScriptedVisionProvider([[_cand("https://x/2.jpg", "full_image_match")]])

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert [c.frame_id for c in result] == ["f2"]


async def test_all_frames_provider_failure_propagates(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider([RuntimeError("api down"), RuntimeError("api down")])

    with pytest.raises(RuntimeError, match="api down"):
        await run_visual_task(_task(("f1", "f2")), refs, provider)


async def test_all_frames_missing_keyframe_propagates(tmp_path):
    refs = _keyframes(tmp_path, ("f9",))
    provider = ScriptedVisionProvider([])

    with pytest.raises(KeyError):
        await run_visual_task(_task(("f1", "f2")), refs, provider)


async def test_zero_match_frame_with_other_failure_returns_empty(tmp_path):
    refs = _keyframes(tmp_path, ("f1", "f2"))
    provider = ScriptedVisionProvider([RuntimeError("api down"), []])

    result = await run_visual_task(_task(("f1", "f2")), refs, provider)

    assert result == []  # one frame succeeded (zero matches): no branch error


async def test_per_frame_timeout_skips_slow_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_search, "_FRAME_TIMEOUT_SEC", 0.02)
    refs = _keyframes(tmp_path, ("f1", "f2"))

    class SlowProvider:
        def __init__(self):
            self.calls = 0

        def web_detection(self, image_bytes):
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.2)
            return [_cand("https://x/2.jpg", "full_image_match")]

    result = await run_visual_task(_task(("f1", "f2")), refs, SlowProvider())

    assert [c.frame_id for c in result] == ["f2"]


async def test_all_frames_timeout_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_search, "_FRAME_TIMEOUT_SEC", 0.02)
    refs = _keyframes(tmp_path, ("f1",))

    class HangingProvider:
        def web_detection(self, image_bytes):
            time.sleep(0.2)
            return []

    with pytest.raises(TimeoutError):
        await run_visual_task(_task(("f1",)), refs, HangingProvider())


async def test_max_candidates_per_frame_capped(tmp_path):
    refs = _keyframes(tmp_path, ("f1",))
    provider = ScriptedVisionProvider(
        [
            [
                _cand("https://x/1.jpg", "full_image_match"),
                _cand("https://x/2.jpg", "full_image_match"),
                _cand("https://x/3.jpg", "full_image_match"),
            ]
        ]
    )

    result = await run_visual_task(_task(("f1",), max_candidates=2), refs, provider)

    assert [c.url for c in result] == ["https://x/1.jpg", "https://x/2.jpg"]


async def test_candidate_id_deterministic_across_runs(tmp_path):
    refs = _keyframes(tmp_path, ("f1",))
    provider = ScriptedVisionProvider(
        [
            [_cand("https://x/a.jpg", "full_image_match")],
            [_cand("https://x/a.jpg", "full_image_match")],
        ]
    )

    first = await run_visual_task(_task(("f1",)), refs, provider)
    second = await run_visual_task(_task(("f1",)), refs, provider)

    assert first[0].candidate_id == second[0].candidate_id
    assert len(first[0].candidate_id) == 3 + 12  # "vw_" + 12 hex chars
