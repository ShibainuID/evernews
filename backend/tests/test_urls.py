"""URL canonicalization tests: tracking params, fragments, path preservation."""

from backend.utils.urls import canonicalize


def test_strips_all_mandated_tracking_params():
    url = (
        "https://example.com/article?utm_source=twitter&utm_medium=social"
        "&utm_campaign=launch&fbclid=abc&gclid=def&id=42"
    )
    assert canonicalize(url) == "https://example.com/article?id=42"


def test_strips_fragment():
    assert canonicalize("https://example.com/page#section") == "https://example.com/page"


def test_preserves_scheme_host_path_and_other_query():
    assert canonicalize("https://example.com/a/b?x=1&y=2") == "https://example.com/a/b?x=1&y=2"


def test_different_paths_remain_different():
    a = canonicalize("https://example.com/path-one?utm_source=foo")
    b = canonicalize("https://example.com/path-two?utm_source=foo")
    assert a != b
    assert a == "https://example.com/path-one"
    assert b == "https://example.com/path-two"
