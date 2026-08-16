"""SerpAPI Google Lens provider + Vision fallback chain (T30-compatible)."""

import base64

import httpx
import pytest

from backend.providers.serpapi import FallbackVisionProvider, SerpAPIVisionProvider
from backend.schemas.investigation import VisualWebCandidate

_BASE64_JPEG = base64.b64encode(b"fake-jpeg-bytes").decode("ascii")


def _lens_response() -> dict:
    return {
        "reverse_image_search": {
            "link": "https://example.com/original",
            "title": "Original photo page",
        },
        "visual_matches": [
            {
                "position": 1,
                "title": "Similar page",
                "link": "https://example.com/similar",
                "source": "Example News",
            },
            {"position": 2, "link": "https://example.com/no-title"},
        ],
    }


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _scripted_lens_handler():
    """Two-step flow: image upload -> image_id, then google_lens search."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/image":
            return httpx.Response(200, json={"image_id": "lens_1"})
        assert request.url.params.get("engine") == "google_lens"
        assert request.url.params.get("image_id") == "lens_1"
        return httpx.Response(200, json=_lens_response())

    return handler, calls


def test_web_detection_uploads_image_then_searches_with_image_id():
    handler, calls = _scripted_lens_handler()
    provider = SerpAPIVisionProvider(api_key="serp-sekrit", transport=_mock_transport(handler))

    candidates = provider.web_detection(b"fake-jpeg-bytes")

    assert calls[0].url.path == "/image"
    assert calls[0].url.params.get("api_key") == "serp-sekrit"
    assert calls[1].url.path == "/search.json"
    assert calls[1].url.params["engine"] == "google_lens"
    assert calls[1].url.params["api_key"] == "serp-sekrit"
    assert calls[1].url.params["image_id"] == "lens_1"
    assert len(candidates) == 3


def test_reverse_image_search_maps_to_full_match():
    handler, _ = _scripted_lens_handler()
    provider = SerpAPIVisionProvider(api_key="k", transport=_mock_transport(handler))

    candidates = provider.web_detection(b"x")

    by_type = {c.raw_provider_type: c for c in candidates}
    assert by_type["serpapi_reverse_image"].candidate_type == "full_image_match"
    assert by_type["serpapi_reverse_image"].url == "https://example.com/original"
    assert by_type["serpapi_reverse_image"].page_title == "Original photo page"
    assert by_type["serpapi_visual_match"].candidate_type == "visually_similar"


def test_missing_api_key_raises():
    handler, _ = _scripted_lens_handler()
    provider = SerpAPIVisionProvider(api_key="", transport=_mock_transport(handler))

    with pytest.raises(RuntimeError, match="SERPAPI_API_KEY"):
        provider.web_detection(b"x")


def test_fallback_used_when_primary_fails():
    class FailingPrimary:
        def web_detection(self, image_bytes: bytes):
            raise RuntimeError("billing disabled")

    handler, _ = _scripted_lens_handler()
    provider = FallbackVisionProvider(
        primary=FailingPrimary(),
        fallback=SerpAPIVisionProvider(api_key="k", transport=_mock_transport(handler)),
    )

    candidates = provider.web_detection(b"x")

    assert len(candidates) == 3


def test_primary_success_never_touches_fallback():
    primary_calls, fallback_calls = [], []

    class CountingPrimary:
        def web_detection(self, image_bytes: bytes):
            primary_calls.append(1)
            return [
                VisualWebCandidate(
                    candidate_id="", frame_id="", candidate_type="page_match",
                    url="https://primary.example.com", raw_provider_type="vision",
                )
            ]

    class CountingFallback:
        def web_detection(self, image_bytes: bytes):
            fallback_calls.append(1)
            return []

    provider = FallbackVisionProvider(primary=CountingPrimary(), fallback=CountingFallback())

    candidates = provider.web_detection(b"x")

    assert candidates[0].url == "https://primary.example.com"
    assert primary_calls == [1]
    assert fallback_calls == []


def test_both_fail_reports_both_errors():
    provider = FallbackVisionProvider(
        primary=_Failing("primary boom"), fallback=_Failing("fallback boom")
    )

    with pytest.raises(RuntimeError, match="primary boom.*fallback boom"):
        provider.web_detection(b"x")


class _Failing:
    def __init__(self, message: str):
        self._message = message

    def web_detection(self, image_bytes: bytes):
        raise RuntimeError(self._message)
