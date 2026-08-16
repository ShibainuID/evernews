"""SerpAPI Google Lens vision provider (T30-compatible fallback).

Same ``VisionProvider`` surface as ``GoogleVisionProvider``: local image
bytes in, normalized ``VisualWebCandidate`` list out. Calls the SerpAPI
``google_lens`` engine with the image uploaded as base64 (no URL hosting
needed), so it needs only an API key — no cloud billing account.

Normalization mirrors HANDOFF §10.4: ``reverse_image_search`` (the matching
image on its source page) maps to ``full_image_match``; ``visual_matches``
(visually similar pages) map to ``visually_similar``. Provider errors and
quota exhaustion raise, so the orchestrator records the branch honestly —
never a fabricated candidate.
"""

from typing import Any

import httpx

from backend.config import Settings
from backend.schemas.investigation import VisualWebCandidate

_ENDPOINT = "https://serpapi.com/search.json"
_TIMEOUT_SEC = 18.0


def _normalize(data: dict[str, Any]) -> list[VisualWebCandidate]:
    candidates: list[VisualWebCandidate] = []
    reverse = data.get("reverse_image_search")
    for item in [reverse] if reverse else []:
        url = item.get("link")
        if not url:
            continue
        candidates.append(
            VisualWebCandidate(
                candidate_id="",
                frame_id="",
                candidate_type="full_image_match",
                url=url,
                page_title=item.get("title") or None,
                provider_score=None,
                raw_provider_type="serpapi_reverse_image",
            )
        )
    for item in data.get("visual_matches") or []:
        url = item.get("link")
        if not url:
            continue
        candidates.append(
            VisualWebCandidate(
                candidate_id="",
                frame_id="",
                candidate_type="visually_similar",
                url=url,
                page_title=item.get("title") or item.get("source") or None,
                provider_score=None,
                raw_provider_type="serpapi_visual_match",
            )
        )
    return candidates


class SerpAPIVisionProvider:
    """Google Lens via SerpAPI; constructor never touches the network."""

    def __init__(self, api_key: str | None = None, transport: httpx.BaseTransport | None = None):
        self._api_key = api_key if api_key is not None else Settings().serpapi_api_key
        self._transport = transport

    def web_detection(self, image_bytes: bytes) -> list[VisualWebCandidate]:
        if not self._api_key:
            raise RuntimeError("serpapi: SERPAPI_API_KEY is not configured")
        with httpx.Client(timeout=_TIMEOUT_SEC, transport=self._transport) as client:
            # Google Lens takes no base64: upload first for an image_id,
            # then run the search with it (no public hosting needed).
            upload = client.post(
                "https://serpapi.com/image",
                params={"api_key": self._api_key},
                files={"image": ("image.jpg", image_bytes, "image/jpeg")},
            )
            upload.raise_for_status()
            image_id = upload.json().get("image_id")
            if not isinstance(image_id, str) or not image_id:
                raise RuntimeError(f"serpapi: image upload returned no image_id: {upload.text[:200]}")
            response = client.get(
                _ENDPOINT,
                params={"engine": "google_lens", "api_key": self._api_key, "image_id": image_id},
            )
            response.raise_for_status()
            data = response.json()
        return _normalize(data)


class FallbackVisionProvider:
    """Try the primary provider; fall back to a secondary one on failure.

    Vision (billing-blocked today) falls back to SerpAPI Google Lens so the
    visual branch keeps working without a cloud billing account.
    """

    def __init__(self, primary: Any, fallback: Any):
        self._primary = primary
        self._fallback = fallback

    def web_detection(self, image_bytes: bytes) -> list[VisualWebCandidate]:
        try:
            return self._primary.web_detection(image_bytes)
        except Exception as primary_error:
            try:
                return self._fallback.web_detection(image_bytes)
            except Exception as fallback_error:
                raise RuntimeError(
                    f"visual search failed (primary: {primary_error}; fallback: {fallback_error})"
                ) from fallback_error
