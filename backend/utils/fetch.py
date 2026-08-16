"""SSRF-safe HTTP fetcher for page enrichment (handoff §31.2).

Web content is untrusted data, so :func:`safe_fetch` refuses anything that
cannot be fetched safely:

- only ``http``/``https`` schemes (``file``, ``gopher``, ``ftp``, ... rejected);
- the hostname is resolved with :func:`socket.getaddrinfo` and every resolved
  address must be public — private, loopback, link-local, reserved, and
  unspecified IPs (and any DNS answer that contains one) are rejected;
- redirects are followed manually (httpx auto-follow disabled), at most three
  hops, and every followed target is validated the same way;
- the body is read at most ``MAX_FETCH_BYTES`` bytes with truncation flagged;
- an explicit per-request timeout is applied.

DNS failures fail closed: an unresolvable host raises :class:`UnsafeURLError`.
Not coupled to ``utils.retry`` yet — retry wrapping is a consumer decision.
"""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_FETCH_BYTES = 2 * 1024 * 1024  # 2 MiB hard cap on the returned body
MAX_REDIRECTS = 3  # at most three manually followed hops
_REQUEST_TIMEOUT = 10.0  # seconds, applies to connect/read/write/pool
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ALLOWED_SCHEMES = frozenset({"http", "https"})


class FetchError(Exception):
    """Base class for deterministic failures from :func:`safe_fetch`."""


class UnsafeURLError(FetchError):
    """The URL or a redirect target is not allowed (scheme or resolved IP)."""


class TooManyRedirects(FetchError):
    """The redirect chain exceeded the maximum of three hops."""


@dataclass(frozen=True)
class SafeFetchResult:
    """What was actually fetched: final URL, status, capped body, truncation flag."""

    url: str
    status: int
    body: bytes
    truncated: bool


def is_private_ip(ip: str) -> bool:
    """True when ``ip`` must never be fetched: private, loopback, link-local, reserved, or unspecified."""
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve(host: str) -> list[str]:
    """All unique addresses ``host`` resolves to; unresolvable hosts fail closed."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host {host!r}") from exc
    resolved = sorted({str(info[4][0]) for info in infos})  # sockaddr host is always a str
    if not resolved:
        raise UnsafeURLError(f"host {host!r} resolved to no addresses")
    return resolved


def _reject_unsafe(url: str) -> None:
    """Raise :class:`UnsafeURLError` unless ``url`` is HTTP(S) resolving only to public IPs."""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"scheme {parts.scheme!r} is not allowed; only http/https may be fetched"
        )
    host = parts.hostname
    if not host:
        raise UnsafeURLError(f"URL has no hostname: {url!r}")
    for ip in _resolve(host):
        if is_private_ip(ip):
            raise UnsafeURLError(f"host {host!r} resolves to blocked address {ip}")


async def _read_limited(response: httpx.Response, limit: int = MAX_FETCH_BYTES) -> tuple[bytes, bool]:
    """Read at most ``limit`` bytes from the stream; report truncation.

    The body is consumed chunk by chunk and never buffered beyond the cap.
    """
    body = bytearray()
    truncated = False
    try:
        async for chunk in response.aiter_bytes():
            room = limit - len(body)
            if room <= 0:
                truncated = True
                break
            if len(chunk) > room:
                body += chunk[:room]
                truncated = True
                break
            body += chunk
    finally:
        await response.aclose()
    return bytes(body), truncated


async def safe_fetch(
    url: str, *, timeout: float = _REQUEST_TIMEOUT, max_bytes: int = MAX_FETCH_BYTES
) -> SafeFetchResult:
    """Fetch ``url`` with SSRF guards; see the module docstring for the guarantees.

    ``max_bytes`` caps the returned body (page enrichment stays at the module
    default; callers fetching whole media may raise it).
    """
    _reject_unsafe(url)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            request = client.build_request("GET", url)
            # stream=True: get() would eagerly buffer the whole body before
            # _read_limited, defeating the MAX_FETCH_BYTES cap (F12-1)
            response = await client.send(request, stream=True)
            location = (
                response.headers.get("location")
                if response.status_code in _REDIRECT_STATUSES
                else None
            )
            if location is not None:
                await response.aclose()
                if hop == MAX_REDIRECTS:
                    raise TooManyRedirects(
                        f"redirect chain exceeds {MAX_REDIRECTS} hops"
                    )
                url = urljoin(url, location)
                _reject_unsafe(url)
                continue
            body, truncated = await _read_limited(response, max_bytes)
            return SafeFetchResult(url=url, status=response.status_code, body=body, truncated=truncated)
    raise AssertionError("unreachable: loop always returns or raises")
