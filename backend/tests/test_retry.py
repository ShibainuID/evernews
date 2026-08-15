"""Bounded retry with exponential backoff for network producers."""

import asyncio

import httpx
import pytest

from backend.utils.retry import retry_async


def _fake_sleep(record):
    """Stand-in for asyncio.sleep that records delays without actually waiting."""

    async def sleep(delay):
        record.append(delay)

    return sleep


def _http_error(status_code, headers=None):
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status_code, request=request, headers=headers or {})
    return httpx.HTTPStatusError(f"status {status_code}", request=request, response=response)


def _retry_429_or_5xx(exc):
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code >= 500 or exc.response.status_code == 429
    )


async def test_success_on_first_attempt(monkeypatch):
    calls = []

    async def fn():
        calls.append(1)
        return "ok"

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    result = await retry_async(fn, attempts=3, base_delay=0.1)

    assert result == "ok"
    assert len(calls) == 1
    assert sleeps == []


async def test_retries_5xx_with_exponential_backoff(monkeypatch):
    statuses = iter([503, 502])
    calls = []

    async def fn():
        calls.append(1)
        status = next(statuses, None)
        if status is not None:
            raise _http_error(status)
        return "ok"

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    result = await retry_async(fn, attempts=3, base_delay=0.1)

    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [0.1, 0.2]  # base_delay * 2**attempt for attempts 0 and 1


async def test_429_honors_retry_after_within_attempt_bound(monkeypatch):
    calls = []

    async def fn():
        calls.append(1)
        raise _http_error(429, {"Retry-After": "5"})

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(fn, attempts=3, base_delay=0.1, retry_for=_retry_429_or_5xx)

    assert len(calls) == 3
    assert sleeps == [5.0, 5.0]  # Retry-After honored, not the backoff schedule


async def test_non_retryable_4xx_propagates_immediately(monkeypatch):
    calls = []

    async def fn():
        calls.append(1)
        raise _http_error(404)

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await retry_async(fn, attempts=3, base_delay=0.1)

    assert excinfo.value.response.status_code == 404
    assert len(calls) == 1
    assert sleeps == []


async def test_default_predicate_retries_only_5xx(monkeypatch):
    calls = []

    async def fn():
        calls.append(1)
        raise _http_error(429, {"Retry-After": "5"})

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(fn, attempts=3, base_delay=0.1)

    assert len(calls) == 1
    assert sleeps == []


async def test_total_attempts_never_exceed_bound(monkeypatch):
    calls = []

    async def fn():
        calls.append(1)
        raise _http_error(503)

    sleeps = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(fn, attempts=2, base_delay=0.1)

    assert len(calls) == 2
    assert sleeps == [0.1]
