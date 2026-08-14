"""Deterministic relative-date normalization for comparator/fuser/ranker.

Relative expressions ("today", "yesterday", "this <daypart>") resolve only
against the supplied request date/time; unknown or malformed input returns
None instead of raising. Explicit ISO dates pass through unchanged.
"""

from datetime import date, datetime

_DAYPARTS = ("morning", "afternoon", "evening", "night")
_YESTERDAY = "yesterday"


def parse_daypart(expr: str | None) -> str | None:
    """Canonical daypart for a 'this <daypart>' expression, else None."""
    if expr is None:
        return None
    words = expr.strip().lower().split()
    if len(words) == 2 and words[0] == "this" and words[1] in _DAYPARTS:
        return words[1]
    return None


def resolve_relative_date(expr: str | None, now: date | datetime) -> date | None:
    """Resolve a relative date expression to a date, or None when unknown."""
    if expr is None:
        return None
    token = expr.strip().lower()
    base = now.date() if isinstance(now, datetime) else now
    if token == "today":
        return base
    if token == _YESTERDAY:
        return base.fromordinal(base.toordinal() - 1)
    if parse_daypart(token) is not None:
        return base
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def dates_differ(a: date | None, b: date | None) -> bool | None:
    """True when the dates are on different days, False when equal, None when unknown."""
    if a is None or b is None:
        return None
    return a != b
