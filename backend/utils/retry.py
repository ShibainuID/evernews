"""Bounded retry with exponential backoff for network producers.

``retry_async`` wraps a single producer call (``fn``) and retries it at most
``attempts`` times. Retryability is decided by ``retry_for``, a predicate on
the raised exception; the default retries ``httpx.HTTPStatusError`` 5xx.
Between retries it sleeps ``base_delay * 2**attempt``, except for HTTP 429
responses, where the ``Retry-After`` header (integer seconds) takes priority.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")


def _default_retry_for(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500


def _delay_for(exc: Exception, attempt: int, base_delay: float) -> float:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass  # non-numeric Retry-After falls back to the backoff schedule
    return base_delay * (2**attempt)


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
    retry_for: Callable[[Exception], bool] | None = None,
) -> T:
    """Run ``fn`` with bounded retries; see module docstring for semantics."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    predicate = _default_retry_for if retry_for is None else retry_for
    for attempt in range(attempts):
        try:
            return await fn()
        except Exception as exc:
            if not predicate(exc) or attempt == attempts - 1:
                raise
            await asyncio.sleep(_delay_for(exc, attempt, base_delay))
    raise AssertionError("unreachable: loop always returns or raises")
