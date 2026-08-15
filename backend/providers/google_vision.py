"""Google Cloud Vision Web Detection provider (T30): image bytes -> candidates.

HANDOFF §10.3: ADC-authenticated ``ImageAnnotatorClient`` created lazily on
first call — the constructor never touches credentials or the network, and
requests carry local image bytes (``vision.Image(content=...)``), never a
public URL. HANDOFF §10.4: each provider category maps to one normalized
``candidate_type``; the raw category field name is preserved verbatim in
``raw_provider_type`` and optional score/title are kept. ``visually_similar``
is discovery only — this branch never treats it as proof of same footage.
A per-request 18s timeout (design §7: 15-20s/frame) is passed to the client.
"""

from typing import Any, Callable, Literal

from google.cloud import vision

from backend.schemas.investigation import VisualWebCandidate

# design §7: vision branch 15-20s per frame; also the service-level bound.
_TIMEOUT_SEC = 18.0

CandidateType = Literal[
    "full_image_match", "partial_image_match", "page_match", "visually_similar"
]

# provider field name (HANDOFF §10.2) -> normalized candidate_type (§10.4)
_CATEGORY_TYPES: dict[str, CandidateType] = {
    "full_matching_images": "full_image_match",
    "partial_matching_images": "partial_image_match",
    "pages_with_matching_images": "page_match",
    "visually_similar_images": "visually_similar",
}


def _default_client_factory() -> Any:
    """Production client; project/credentials come from ADC (HANDOFF §10.3)."""
    return vision.ImageAnnotatorClient()


class GoogleVisionProvider:
    """Web Detection adapter: local image bytes in, normalized candidates out."""

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory or _default_client_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:  # lazy: ADC/network only on first call
            self._client = self._client_factory()
        return self._client

    def web_detection(self, image_bytes: bytes) -> list[VisualWebCandidate]:
        response = self._get_client().web_detection(
            image=vision.Image(content=image_bytes), timeout=_TIMEOUT_SEC
        )
        if response.error.message:
            raise RuntimeError(response.error.message)
        return _normalize(response.web_detection)


def _normalize(web: Any) -> list[VisualWebCandidate]:
    """One candidate per returned web image/page, category-mapped (§10.4)."""
    if web is None:
        return []
    candidates: list[VisualWebCandidate] = []
    for field, candidate_type in _CATEGORY_TYPES.items():
        for item in getattr(web, field, None) or []:
            url = item.url
            if not url:
                continue
            candidates.append(
                VisualWebCandidate(
                    candidate_id="",  # stamped by the service after dedupe
                    frame_id="",  # stamped by the service per frame
                    candidate_type=candidate_type,
                    url=url,
                    page_title=getattr(item, "page_title", None) or None,
                    provider_score=item.score or None,
                    raw_provider_type=field,
                )
            )
    return candidates
