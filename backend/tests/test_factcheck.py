"""T28: Google Fact Check branch tests (TDD red-green).

httpx MockTransport only — no network, no credentials. Pins the exact
``claims:search`` request shape (query, pageSize=10, key, optional
languageCode, pageToken on follow-up pages), 15s timeout, bounded 5xx/429
retry via ``utils/retry.py`` (max 3 attempts, i.e. two retries, numeric
Retry-After honored),
normalization of every claim-review into ``FactCheckEvidence`` (textualRating
verbatim, raw claim preserved), 3-page pagination cap, review_url dedupe in
the task runner, and timeout/error propagation (never a fake empty result).
"""

import asyncio

import httpx
import pytest

from backend.providers.google_factcheck import search_fact_checks
from backend.schemas.investigation import FactCheckTask
from backend.services.validation.fact_check import run_fact_check_task


def _review(
    url="https://check.example/a",
    rating="Misleading",
    publisher="Example Fact Check",
    title="Check title",
    review_date="2026-08-01",
):
    return {
        "publisher": {"name": publisher},
        "url": url,
        "title": title,
        "reviewDate": review_date,
        "textualRating": rating,
    }


def _claim(text="Claim text", claimant="Someone", reviews=None, **extra):
    # Real API shape (rest/v1alpha1/claims#Claim): reviews live under claimReview.
    claim = {"text": text, "claimant": claimant, "claimReview": reviews or [_review()]}
    claim.update(extra)
    return claim


def _page(claims=None, next_token=None):
    payload = {}
    if claims is not None:
        payload["claims"] = claims
    if next_token:
        payload["nextPageToken"] = next_token
    return payload


def _response(status, body=None, retry_after=None):
    request = httpx.Request("GET", "https://factchecktools.googleapis.com/v1alpha1/claims:search")
    headers = {"Retry-After": retry_after} if retry_after else {}
    if body is None:
        return httpx.Response(status, request=request, headers=headers)
    return httpx.Response(status, request=request, headers=headers, json=body)


def _scripted(responses):
    """MockTransport handler serving canned responses; records each request."""
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    return handler, calls


def _fake_sleep(record: list[float]):
    async def sleep(delay: float) -> None:
        record.append(delay)

    return sleep


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _task(queries=("q1", "q2"), languages=("id", "en")):
    return FactCheckTask(
        task_id="fc_01",
        queries=list(queries),
        language_codes=list(languages),
        goal="verify claim",
    )


# --- zero results is a valid no-match, never a truth/falsity judgment ---


async def test_zero_results_returns_empty_list():
    handler, calls = _scripted([_response(200, _page([]))])

    result = await search_fact_checks(
        "Jakarta flood 2026", api_key="test-key", transport=_transport(handler)
    )

    assert result == []  # valid no-match; FactCheckEvidence carries no truth/falsity flag
    assert len(calls) == 1


async def test_body_without_claims_field_is_valid_empty():
    handler, _ = _scripted([_response(200, {})])

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert result == []


# --- normalization: every claim-review becomes FactCheckEvidence ---


async def test_multiple_reviews_all_normalized():
    claim_a = _claim(
        text="Claim A",
        claimant="Speaker A",
        reviews=[_review(url="https://x/1"), _review(url="https://x/2", rating="True")],
    )
    claim_b = _claim(text="Claim B", claimant="Speaker B", reviews=[_review(url="https://x/3")])
    handler, _ = _scripted([_response(200, _page([claim_a, claim_b]))])

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert len(result) == 3
    first, second, third = result
    assert first.query == "q"
    assert first.claim_text == "Claim A"
    assert first.claimant == "Speaker A"
    assert first.publisher == "Example Fact Check"
    assert first.review_url == "https://x/1"
    assert first.review_title == "Check title"
    assert first.review_date == "2026-08-01"
    assert first.textual_rating == "Misleading"
    assert first.relevance_score is None
    assert first.raw == claim_a  # raw source object preserved verbatim
    assert first.evidence_id.startswith("fc_")
    assert len({e.evidence_id for e in result}) == 3  # unique per review
    assert second.claim_text == "Claim A" and second.review_url == "https://x/2"
    assert third.claim_text == "Claim B"


async def test_textual_rating_stored_verbatim_not_mapped():
    handler, _ = _scripted([_response(200, _page([_claim(reviews=[_review(rating="Misleading")])]))])

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert result[0].textual_rating == "Misleading"


async def test_review_without_url_is_skipped_not_crashed():
    bad = {"publisher": {"name": "No URL"}, "title": "t", "textualRating": "False"}
    handler, _ = _scripted([_response(200, _page([_claim(reviews=[bad, _review(url="https://x/ok")])]))])

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert [e.review_url for e in result] == ["https://x/ok"]


# --- exact request params ---


async def test_request_params_exact():
    handler, calls = _scripted([_response(200, _page([]))])

    await search_fact_checks(
        "Jakarta flood", api_key="sekrit", language="id", transport=_transport(handler)
    )

    (request,) = calls
    assert request.method == "GET"
    assert request.url.path == "/v1alpha1/claims:search"
    params = request.url.params
    assert params["query"] == "Jakarta flood"
    assert params["pageSize"] == "10"
    assert params["key"] == "sekrit"
    assert params["languageCode"] == "id"
    assert "pageToken" not in params


async def test_no_language_code_param_when_language_omitted():
    handler, calls = _scripted([_response(200, _page([]))])

    await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    (request,) = calls
    assert "languageCode" not in request.url.params


# --- pagination: bounded at 3 pages ---


async def test_pagination_follows_next_page_token():
    pages = [
        _response(200, _page([_claim(text="1")], next_token="t2")),
        _response(200, _page([_claim(text="2")], next_token="t3")),
        _response(200, _page([_claim(text="3")])),
    ]
    handler, calls = _scripted(pages)

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert [e.claim_text for e in result] == ["1", "2", "3"]
    assert len(calls) == 3
    assert calls[0].url.params.get("pageToken") is None
    assert calls[1].url.params["pageToken"] == "t2"
    assert calls[2].url.params["pageToken"] == "t3"


async def test_pagination_capped_at_three_pages():
    pages = [_response(200, _page([_claim(text=str(i))], next_token=f"t{i}")) for i in range(4)]
    handler, calls = _scripted(pages)

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert [e.claim_text for e in result] == ["0", "1", "2"]
    assert len(calls) == 3  # the 4th page is never requested


# --- bounded retry via utils/retry.py ---


async def test_5xx_retries_twice_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted(
        [_response(503), _response(503), _response(200, _page([_claim()]))]
    )

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert len(result) == 1
    assert len(calls) == 3  # max 3 attempts: one initial call plus two retries
    assert sleeps == [0.5, 1.0]  # base_delay * 2**attempt


async def test_429_honors_retry_after_within_three_attempts(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted(
        [
            _response(429, retry_after="7"),
            _response(429, retry_after="7"),
            _response(200, _page([_claim()])),
        ]
    )

    result = await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert len(result) == 1
    assert len(calls) == 3
    assert sleeps == [7.0, 7.0]  # Retry-After honored, not the backoff schedule


async def test_5xx_exhausted_raises_never_empty(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted([_response(500), _response(500), _response(500)])

    with pytest.raises(httpx.HTTPStatusError):
        await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert len(calls) == 3  # bounded: never a fourth attempt
    assert sleeps == [0.5, 1.0]


async def test_non_retryable_4xx_propagates_immediately():
    handler, calls = _scripted([_response(400)])

    with pytest.raises(httpx.HTTPStatusError):
        await search_fact_checks("q", api_key="test-key", transport=_transport(handler))

    assert len(calls) == 1


# --- timeout is a branch failure, never converted to [] ---


async def test_timeout_propagates_not_hidden_as_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await search_fact_checks("q", api_key="test-key", transport=_transport(handler))


# --- task runner: query variants, language settings, dedupe ---


async def test_run_loops_query_variants_and_language_codes():
    pages = [
        _response(200, _page([_claim(text="x", reviews=[_review(url="https://x/q1id")])])),
        _response(200, _page([_claim(text="x", reviews=[_review(url="https://x/q1en")])])),
        _response(200, _page([_claim(text="x", reviews=[_review(url="https://x/q2id")])])),
        _response(200, _page([_claim(text="x", reviews=[_review(url="https://x/q2en")])])),
    ]
    handler, calls = _scripted(pages)

    result = await run_fact_check_task(_task(), api_key="test-key", transport=_transport(handler))

    assert len(calls) == 4  # 2 queries x 2 languages
    assert [c.url.params["query"] for c in calls] == ["q1", "q1", "q2", "q2"]
    assert [c.url.params["languageCode"] for c in calls] == ["id", "en", "id", "en"]
    assert {e.query for e in result} == {"q1", "q2"}


async def test_run_empty_language_codes_sends_no_language_code():
    handler, calls = _scripted([_response(200, _page([_claim()]))])

    await run_fact_check_task(
        _task(queries=["q1"], languages=[]), api_key="test-key", transport=_transport(handler)
    )

    (request,) = calls
    assert "languageCode" not in request.url.params


async def test_run_dedupes_reviews_by_review_url_keeps_distinct():
    dup = _claim(text="dup", reviews=[_review(url="https://x/1")])
    distinct = _claim(text="distinct", reviews=[_review(url="https://x/2")])
    dup_again = _claim(text="dup again", reviews=[_review(url="https://x/1")])
    handler, calls = _scripted(
        [_response(200, _page([dup, distinct])), _response(200, _page([dup_again]))]
    )

    result = await run_fact_check_task(
        _task(queries=["q1", "q2"], languages=[]), api_key="test-key", transport=_transport(handler)
    )

    assert len(calls) == 2
    assert [e.review_url for e in result] == ["https://x/1", "https://x/2"]
    assert result[0].claim_text == "dup"  # first occurrence retained


async def test_run_timeout_propagates_not_empty():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await run_fact_check_task(_task(), api_key="test-key", transport=_transport(handler))


# --- settings wiring: production key resolution ---


async def test_api_key_defaults_from_settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_FACT_CHECK_API_KEY", "env-key")
    handler, calls = _scripted([_response(200, _page([]))])

    await search_fact_checks("q", transport=_transport(handler))

    (request,) = calls
    assert request.url.params["key"] == "env-key"
