"""SSRF-safe HTTP fetcher: scheme allowlist, DNS/IP guards, bounded body, manual redirects.

Tests run entirely against fakes: ``httpx.AsyncClient`` and ``socket.getaddrinfo``
are monkeypatched, so no test ever touches the network.
"""

import itertools
import socket

import httpx
import pytest

from backend.utils.fetch import (
    MAX_FETCH_BYTES,
    SafeFetchResult,
    TooManyRedirects,
    UnsafeURLError,
    is_private_ip,
    safe_fetch,
)

PUBLIC_IP = "93.184.216.34"  # example.com in real life


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status_code = status
        self.headers = httpx.Headers(headers or {})
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []

    async def get(self, url):
        self.urls.append(url)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass


def install_http(monkeypatch, responses, record=None):
    """Replace httpx.AsyncClient in the fetch module with a fake factory."""
    from backend.utils import fetch

    def factory(**kwargs):
        client = FakeClient(responses)
        if record is not None:
            record["kwargs"] = kwargs
            record["client"] = client
        return client

    monkeypatch.setattr(fetch.httpx, "AsyncClient", factory)


def install_dns(monkeypatch, mapping):
    """Resolve each host from ``mapping``; unknown hosts fail like a real DNS miss."""
    from backend.utils import fetch

    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(-2, "Name or service not known")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]
        ]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake_getaddrinfo)


def install_dns_fail_loudly(monkeypatch):
    """Fail the test if DNS is consulted at all."""
    from backend.utils import fetch

    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise AssertionError(f"DNS must not be consulted, but {host!r} was resolved")

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake_getaddrinfo)


def public_dns(monkeypatch, *hosts):
    install_dns(monkeypatch, {host: [PUBLIC_IP] for host in hosts})


def redirect_response(location, status=302):
    return FakeResponse(status=status, headers={"location": location})


# --- happy path / basics -----------------------------------------------------


async def test_https_fetch_returns_result(monkeypatch):
    record = {}
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse(body=b"hello world")], record)

    result = await safe_fetch("https://example.com/")

    assert result == SafeFetchResult(
        url="https://example.com/", status=200, body=b"hello world", truncated=False
    )
    assert record["client"].urls == ["https://example.com/"]
    assert record["kwargs"]["timeout"] == 10.0
    assert record["kwargs"]["follow_redirects"] is False


async def test_custom_timeout_is_forwarded(monkeypatch):
    record = {}
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse()], record)

    await safe_fetch("https://example.com/", timeout=3.5)

    assert record["kwargs"]["timeout"] == 3.5


async def test_http_status_error_is_returned_not_raised(monkeypatch):
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse(status=404, body=b"missing")])

    result = await safe_fetch("https://example.com/missing")

    assert result.status == 404
    assert result.body == b"missing"
    assert result.truncated is False


# --- scheme allowlist --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "data:text/plain,hello",
    ],
)
async def test_non_http_schemes_rejected(monkeypatch, url):
    record = {}
    install_dns_fail_loudly(monkeypatch)
    install_http(monkeypatch, [FakeResponse()], record)

    with pytest.raises(UnsafeURLError):
        await safe_fetch(url)

    assert record == {}  # rejected before any client/DNS work


@pytest.mark.parametrize("url", ["http:///path", "https://?x=1", "http://"])
async def test_url_without_host_rejected(monkeypatch, url):
    install_dns_fail_loudly(monkeypatch)
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch(url)


# --- DNS / IP guards ---------------------------------------------------------


async def test_localhost_hostname_rejected(monkeypatch):
    install_dns(monkeypatch, {"localhost": ["127.0.0.1"]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("http://localhost:80/")


async def test_literal_loopback_rejected(monkeypatch):
    install_dns(monkeypatch, {"127.0.0.1": ["127.0.0.1"]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("http://127.0.0.1/")


@pytest.mark.parametrize("ip", ["10.0.0.1", "192.168.1.1", "172.16.0.1"])
async def test_literal_private_ipv4_rejected(monkeypatch, ip):
    install_dns(monkeypatch, {ip: [ip]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch(f"http://{ip}/")


async def test_link_local_metadata_rejected(monkeypatch):
    install_dns(monkeypatch, {"169.254.169.254": ["169.254.169.254"]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("http://169.254.169.254/latest/meta-data/")


@pytest.mark.parametrize("ip", ["::1", "::ffff:127.0.0.1", "fd00::1"])
async def test_unsafe_ipv6_literals_rejected(monkeypatch, ip):
    install_dns(monkeypatch, {ip: [ip]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch(f"http://[{ip}]/")


async def test_dns_rebinding_to_private_ip_rejected(monkeypatch):
    install_dns(monkeypatch, {"internal.example.com": ["10.1.2.3"]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("http://internal.example.com/")


async def test_any_private_address_in_resolution_rejected(monkeypatch):
    install_dns(monkeypatch, {"mixed.example.com": [PUBLIC_IP, "127.0.0.1"]})
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("http://mixed.example.com/")


async def test_unresolvable_host_fails_closed(monkeypatch):
    install_dns(monkeypatch, {})  # every host raises gaierror
    install_http(monkeypatch, [FakeResponse()])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("https://does-not-exist.example/")


# --- manual redirects --------------------------------------------------------


async def test_redirect_chain_of_three_allowed(monkeypatch):
    hosts = ["example.com", "example.net", "example.org", "example.edu"]
    public_dns(monkeypatch, *hosts)
    responses = [
        redirect_response("https://example.net/"),
        redirect_response("https://example.org/"),
        redirect_response("https://example.edu/"),
        FakeResponse(body=b"final"),
    ]
    record = {}
    install_http(monkeypatch, responses, record)

    result = await safe_fetch("https://example.com/")

    assert record["client"].urls == [
        "https://example.com/",
        "https://example.net/",
        "https://example.org/",
        "https://example.edu/",
    ]
    assert result.url == "https://example.edu/"
    assert result.body == b"final"
    assert record["kwargs"]["follow_redirects"] is False  # every hop was manual


async def test_fourth_redirect_rejected(monkeypatch):
    hosts = ["example.com", "example.net", "example.org", "example.edu", "example.info"]
    public_dns(monkeypatch, *hosts)
    responses = [
        redirect_response("https://example.net/"),
        redirect_response("https://example.org/"),
        redirect_response("https://example.edu/"),
        redirect_response("https://example.info/"),
    ]
    record = {}
    install_http(monkeypatch, responses, record)

    with pytest.raises(TooManyRedirects):
        await safe_fetch("https://example.com/")

    assert record["client"].urls == [
        "https://example.com/",
        "https://example.net/",
        "https://example.org/",
        "https://example.edu/",
    ]


async def test_redirect_target_to_private_ip_rejected(monkeypatch):
    install_dns(monkeypatch, {"example.com": [PUBLIC_IP], "10.0.0.1": ["10.0.0.1"]})
    record = {}
    install_http(monkeypatch, [redirect_response("http://10.0.0.1/evil")], record)

    with pytest.raises(UnsafeURLError):
        await safe_fetch("https://example.com/")

    assert record["client"].urls == ["https://example.com/"]  # target never requested


async def test_redirect_target_to_unsafe_scheme_rejected(monkeypatch):
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [redirect_response("file:///etc/passwd")])

    with pytest.raises(UnsafeURLError):
        await safe_fetch("https://example.com/")


async def test_relative_redirect_target_resolved(monkeypatch):
    public_dns(monkeypatch, "example.com")
    record = {}
    install_http(monkeypatch, [redirect_response("/next"), FakeResponse()], record)

    await safe_fetch("https://example.com/base/page")

    assert record["client"].urls == ["https://example.com/base/page", "https://example.com/next"]


# --- bounded body ------------------------------------------------------------


async def test_body_exactly_at_limit_not_truncated(monkeypatch):
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse(body=b"x" * MAX_FETCH_BYTES)])

    result = await safe_fetch("https://example.com/")

    assert result.truncated is False
    assert len(result.body) == MAX_FETCH_BYTES


async def test_body_over_limit_truncated(monkeypatch):
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse(body=b"x" * (MAX_FETCH_BYTES + 100))])

    result = await safe_fetch("https://example.com/")

    assert result.truncated is True
    assert len(result.body) == MAX_FETCH_BYTES


async def test_chunked_body_crossing_limit_truncated(monkeypatch):
    public_dns(monkeypatch, "example.com")
    chunks = [b"x" * (MAX_FETCH_BYTES - 1), b"y", b"z"]  # total = limit + 2
    install_http(monkeypatch, [FakeResponse(chunks=chunks)])

    result = await safe_fetch("https://example.com/")

    assert result.truncated is True
    assert len(result.body) == MAX_FETCH_BYTES
    assert result.body[-1:] == b"y"  # the chunk that completed the cap is kept


async def test_infinite_stream_is_bounded(monkeypatch):
    public_dns(monkeypatch, "example.com")
    install_http(monkeypatch, [FakeResponse(chunks=itertools.repeat(b"x" * 1024 * 1024))])

    result = await safe_fetch("https://example.com/")

    assert result.truncated is True
    assert len(result.body) == MAX_FETCH_BYTES


# --- is_private_ip -----------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # private A
        "172.16.0.1",  # private B
        "192.168.1.1",  # private C
        "169.254.169.254",  # link-local / cloud metadata
        "0.0.0.0",  # unspecified
        "240.0.0.1",  # reserved
        "::1",  # IPv6 loopback
        "::",  # IPv6 unspecified
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "fd00::1",  # IPv6 unique local
        "fe80::1",  # IPv6 link-local
    ],
)
def test_is_private_ip_truthy(ip):
    assert is_private_ip(ip)


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
        "2001:4860:4860::8888",
        "2606:4700:4700::1111",
    ],
)
def test_is_private_ip_falsy(ip):
    assert not is_private_ip(ip)


def test_is_private_ip_invalid_raises_value_error():
    with pytest.raises(ValueError):
        is_private_ip("not-an-ip")
