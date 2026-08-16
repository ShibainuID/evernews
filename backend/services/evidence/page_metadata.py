"""Deterministic metadata extraction from fetched page HTML (HANDOFF 13.3).

Pure stdlib (``html.parser`` + ``json``): OpenGraph ``<meta>`` tags, JSON-LD
Article/NewsArticle/Event nodes, and a ``<time datetime>`` fallback. The
function never raises — malformed HTML or JSON yields an empty
:class:`PageMetadata`, so page enrichment can never abort normalization.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any, Iterable

# og:/article: meta property -> PageMetadata field
_OG_PROPERTIES = {
    "og:title": "title",
    "og:site_name": "publisher",
    "og:description": "description",
    "article:published_time": "published_at",
}
_ARTICLE_TYPES = frozenset({"Article", "NewsArticle"})

# JSON-LD fields sourced per node type; only explicit structured values.
_ARTICLE_FIELDS = {
    "title": "headline",
    "published_at": "datePublished",
    "description": "description",
    "publisher": "publisher",  # string or {"name": ...}
    "location": "locationCreated",  # string or {"name": ...}
}
_EVENT_FIELDS = {
    "event": "name",
    "time_context": "startDate",
    "location": "location",  # string or {"name": ...}
}
_DATE_FIELDS = frozenset({"published_at", "time_context"})


@dataclass(frozen=True)
class PageMetadata:
    """Metadata a page explicitly provides; every field optional."""

    title: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    description: str | None = None
    event: str | None = None
    location: str | None = None
    time_context: str | None = None


class _PageParser(HTMLParser):
    """Collects OG meta properties, JSON-LD script bodies, first <time datetime>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self.time_datetime: str | None = None
        self._ld_buffer: list[str] = []
        self._in_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        if tag == "meta":
            prop = attributes.get("property") or attributes.get("name")
            content = attributes.get("content", "").strip()
            if prop in _OG_PROPERTIES and content:
                self.meta.setdefault(prop, content)  # first occurrence wins
        elif tag == "script":
            if attributes.get("type", "").strip() == "application/ld+json":
                self._in_ld = True
                self._ld_buffer = []
        elif tag == "time" and self.time_datetime is None:
            self.time_datetime = attributes.get("datetime", "").strip() or None

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_ld:
            self.jsonld.append("".join(self._ld_buffer))
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        # script content arrives raw (entities and <!-- --> wrappers intact)
        if self._in_ld:
            self._ld_buffer.append(data)


def _normalize_date(expr: str) -> str | None:
    """Reduce an ISO date/datetime to YYYY-MM-DD; malformed input -> None."""
    try:
        return datetime.fromisoformat(expr.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _node_name(value: Any) -> str | None:
    """A plain string or a {"name": ...} node (publisher/location/Place)."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        text = value["name"].strip()
        return text or None
    return None


def _iter_nodes(payload: Any) -> Iterable[dict[str, Any]]:
    """Every JSON-LD node in document order: top-level dict, list items, @graph."""
    if isinstance(payload, dict):
        yield payload
        graph = payload.get("@graph")
        if isinstance(graph, list):
            yield from (item for item in graph if isinstance(item, dict))
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_nodes(item)


def _extract_jsonld(scripts: list[str]) -> dict[str, str]:
    """First-wins per field over all JSON-LD nodes in document order."""
    meta: dict[str, str] = {}
    for script in scripts:
        payload = _parse_script(script)
        if payload is None:
            continue
        for node in _iter_nodes(payload):
            raw_types = node.get("@type")
            type_list = (
                raw_types if isinstance(raw_types, list) else [raw_types]
            )
            if any(t in _ARTICLE_TYPES for t in type_list if isinstance(t, str)):
                fields = _ARTICLE_FIELDS
            elif "Event" in type_list:
                fields = _EVENT_FIELDS
            else:
                continue
            for field, key in fields.items():
                if field in meta:
                    continue
                extracted = _node_name(node.get(key))
                if extracted:
                    meta[field] = unescape(extracted)
    return meta


def _parse_script(body: str) -> Any | None:
    """JSON payload of one ld+json script; legacy wrappers/entities tolerated."""
    stripped = body.strip()
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        # some sites comment-wrapped JSON-LD out for legacy browsers
        stripped = stripped[4:-3].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # ponytail: fully entity-encoded JSON is not valid JSON; unescape and
        # retry. Values are additionally unescaped after extraction.
        try:
            return json.loads(unescape(stripped))
        except json.JSONDecodeError:
            return None


def parse_page_metadata(html: bytes | str) -> PageMetadata:
    """Extract the metadata a page explicitly provides; never raises.

    Precedence per field: OpenGraph first, JSON-LD second, then the first
    ``<time datetime>`` as a published-time fallback. Dates are reduced to
    ``YYYY-MM-DD`` so downstream date comparisons stay lexicographic.
    """
    if isinstance(html, bytes):
        # ponytail: utf-8 with replacement; charset sniffing when non-UTF-8
        # pages measurably matter
        html = html.decode("utf-8", errors="replace")
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return PageMetadata()

    meta: dict[str, str] = {}
    for prop, field in _OG_PROPERTIES.items():
        if prop in parser.meta:
            meta[field] = parser.meta[prop]
    for field, value in _extract_jsonld(parser.jsonld).items():
        meta.setdefault(field, value)
    if "published_at" not in meta and parser.time_datetime is not None:
        meta["published_at"] = parser.time_datetime

    for field in _DATE_FIELDS:
        if field in meta:
            date = _normalize_date(meta[field])
            if date is None:
                del meta[field]
            else:
                meta[field] = date
    return PageMetadata(**{field: value for field, value in meta.items() if value})
