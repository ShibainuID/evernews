"""Google Fact Check Tools provider (T28): ``claims:search`` -> FactCheckEvidence.

``search_fact_checks`` runs one query stream against
``GET https://factchecktools.googleapis.com/v1alpha1/claims:search`` with a
15s timeout, following ``nextPageToken`` for at most 3 pages. Each page
request retries at most 2 attempts for 5xx/429 via ``utils/retry.py`` (429
honors a numeric ``Retry-After``); other errors — including timeouts — are
never retried and never converted into a fake empty result: a raised error is
a branch failure for the orchestrator (T32), while ``[]`` is only returned
when the API itself has no claims.

Every claim-review pair becomes one ``FactCheckEvidence``; the publisher's
``textualRating`` is stored verbatim (never mapped to a final classification)
and the raw claim object is preserved. Reviews without a ``review_url`` are
skipped — the schema requires it and it is the dedupe key.
"""

from hashlib import sha256
from typing import Any

import httpx

from backend.config import Settings
from backend.schemas.investigation import FactCheckEvidence
from backend.utils.retry import retry_async

_ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
_TIMEOUT_SEC = 15.0
_MAX_PAGES = 3
_MAX_ATTEMPTS = 2  # bounded: one initial call plus one retry
_BASE_DELAY = 0.5


def _retryable(exc: Exception) -> bool:
    """Retry 5xx and 429 only; Retry-After delay selection lives in utils/retry.py."""
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code >= 500 or exc.response.status_code == 429
    )


async def _get_page(client: httpx.AsyncClient, params: dict[str, Any]) -> dict:
    response = await client.get(_ENDPOINT, params=params)
    response.raise_for_status()
    return response.json()


def _normalize_claims(data: dict, query: str) -> list[FactCheckEvidence]:
    evidence: list[FactCheckEvidence] = []
    for claim in data.get("claims") or []:
        for review in claim.get("review") or []:
            url = review.get("url")
            if not url:  # review_url is required and the dedupe key; skip malformed entry
                continue
            publisher = review.get("publisher")
            evidence.append(
                FactCheckEvidence(
                    evidence_id="fc_" + sha256(url.encode()).hexdigest()[:12],
                    query=query,
                    claim_text=claim.get("text"),
                    claimant=claim.get("claimant"),
                    publisher=publisher.get("name") if isinstance(publisher, dict) else None,
                    review_url=url,
                    review_title=review.get("title"),
                    review_date=review.get("reviewDate"),
                    textual_rating=review.get("textualRating"),
                    raw=claim,
                )
            )
    return evidence


async def search_fact_checks(
    query: str,
    api_key: str | None = None,
    language: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[FactCheckEvidence]:
    """Search one query; returns at most 3 pages of normalized reviews (may be []).

    ``api_key`` defaults to ``Settings().google_fact_check_api_key``; the
    ``transport`` seam exists only for deterministic MockTransport tests.
    """
    key = api_key if api_key is not None else Settings().google_fact_check_api_key
    params: dict[str, Any] = {"query": query, "pageSize": 10, "key": key}
    if language:
        params["languageCode"] = language
    results: list[FactCheckEvidence] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC, transport=transport) as client:
        for _ in range(_MAX_PAGES):
            data = await retry_async(
                lambda: _get_page(client, params),
                attempts=_MAX_ATTEMPTS,
                base_delay=_BASE_DELAY,
                retry_for=_retryable,
            )
            results.extend(_normalize_claims(data, query))
            next_token = data.get("nextPageToken")
            if not next_token:
                break
            params["pageToken"] = next_token
    return results
