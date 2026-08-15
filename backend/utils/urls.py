"""Deterministic URL canonicalization for source-candidate dedupe keys.

Strips the mandated tracking parameters and the fragment; preserves scheme,
host, path, and any other query parameters in their original order. No
over-normalization (no host casing, no path rewriting, no param sorting).
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Handoff §13.2: tracking params that must never appear in a dedupe key.
_TRACKING_PARAMS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "fbclid", "gclid"}
)


def canonicalize(url: str) -> str:
    """Canonical form of ``url``, safe to use as a dedupe key."""
    scheme, netloc, path, query, _fragment = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS
    ]
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))
