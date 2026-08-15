"""Deterministic WHAT/WHERE/WHEN context comparison (T15): HANDOFF §17, §37.

Small exact rules on top of the T08/T09 dictionaries. UNKNOWN propagates when a
side is missing or a value does not resolve and never becomes MISMATCH. A
``semantic_equiv`` fallback resolves only MISMATCH pairs the tiny deterministic
dictionaries do not know (at least one side is not a recognized dictionary
entry); a known deterministic mismatch where both sides are recognized, and
exact/synonym/parent-child matches, never consult it, nor does a missing side.
"""

from collections.abc import Callable
from datetime import date

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ComparisonStatus, ContextClaim
from backend.schemas.result import ContextComparison, DimensionComparison, SourceContext
from backend.utils.text import (
    LocationRelation,
    _EVENT_CANONICAL,
    _LOCATION_ALIASES,
    _LOCATION_PARENTS,
    events_relation,
    location_relation,
    normalize_event,
)

_NO_SOURCE = "No reliable source found to compare against."


def _claim_value(claim: ContextClaim) -> str | None:
    """Compared value: normalized_value when present, else the raw value."""
    return claim.normalized_value if claim.normalized_value is not None else claim.value


def _resolve_date(value: str | None) -> date | None:
    """Resolvable ISO date, or None. No clock here: relative expressions
    (non-normalized) are unresolvable and map to UNKNOWN, never mismatch."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _event_known(value: str) -> bool:
    """True when the event dictionary recognizes the value (canonical or synonym)."""
    return normalize_event(value) in _EVENT_CANONICAL


def _location_known(value: str) -> bool:
    """True when the location dictionary recognizes the value.

    Recognized = alias, city key, or parent name of the T09 tables (F15-2:
    parent values like "Indonesia"/"Thailand" are explicit dictionary entries
    too). Uses the same keying as ``backend.utils.text._key`` without importing
    the private helper: lowercase, comma-split to the core city, alias-resolved.
    """
    core = value.strip().lower().split(",")[0]
    resolved = _LOCATION_ALIASES.get(core, core)
    return (
        core in _LOCATION_ALIASES
        or resolved in _LOCATION_PARENTS
        or resolved in _LOCATION_PARENTS.values()
    )


def _pair_status(
    cur: str | None,
    src: str | None,
    relation: LocationRelation,
    semantic_equiv: Callable[[str, str], bool | None] | None,
    recognized: Callable[[str], bool],
) -> tuple[ComparisonStatus, float]:
    """Status+confidence for one event/location pair (HANDOFF §17.1, §17.3).

    The mismatch guard lives here: either side missing -> UNKNOWN, so a pair
    can never be judged a mismatch without both values populated. The semantic
    fallback only resolves pairs the deterministic dictionaries do not know: a
    MISMATCH pair where at least one value is unrecognized. A known
    deterministic mismatch (both sides recognized) returns MISMATCH directly
    and never consults the fallback (F15-1).
    """
    if cur is None or src is None:
        return ComparisonStatus.UNKNOWN, 0.0
    if relation is LocationRelation.MISMATCH:
        if semantic_equiv is not None and not (recognized(cur) and recognized(src)):
            result = semantic_equiv(cur, src)
            if result is True:
                return ComparisonStatus.CONSISTENT, 1.0
            if result is False:
                return ComparisonStatus.MISMATCH, 1.0
            return ComparisonStatus.UNKNOWN, 0.0
        return ComparisonStatus.MISMATCH, 1.0
    return ComparisonStatus.CONSISTENT, 1.0


def _date_status(cur: str | None, src: str | None) -> tuple[ComparisonStatus, float]:
    """Resolvable dates compare exactly; an unresolvable side is UNKNOWN."""
    a, b = _resolve_date(cur), _resolve_date(src)
    if a is None or b is None:
        return ComparisonStatus.UNKNOWN, 0.0
    if a == b:
        return ComparisonStatus.CONSISTENT, 1.0
    return ComparisonStatus.MISMATCH, 1.0


def _explain(
    dim: str,
    status: ComparisonStatus,
    cur: str | None,
    src: str | None,
    parent_child: bool = False,
) -> str:
    if status is ComparisonStatus.UNKNOWN:
        if src is None:
            return f"No source {dim.lower()} to compare against."
        if cur is None:
            return f"No current {dim.lower()} claim to compare with the source."
        return f"Could not determine a relation between {cur!r} and {src!r}."
    if status is ComparisonStatus.CONSISTENT:
        if parent_child:
            return f"{dim} {cur!r} is a parent/child of source {src!r}."
        return f"{dim} {cur!r} matches source {src!r}."
    return f"{dim} {cur!r} differs from source {src!r}."


def _dimension(
    cur: str | None,
    src: str | None,
    status: ComparisonStatus,
    confidence: float,
    evidence_ids: list[str],
    explanation: str,
) -> DimensionComparison:
    return DimensionComparison(
        current=cur,
        source=src,
        status=status,
        confidence=confidence,
        evidence_ids=evidence_ids,
        explanation=explanation,
    )


def compare(
    current: VideoContext,
    source: SourceContext | None,
    semantic_equiv: Callable[[str, str], bool | None] | None = None,
) -> ContextComparison:
    """Compare WHAT/WHERE/WHEN of the current context against a probable source.

    Evidence ids always come from the current claim for that dimension;
    ``SourceContext`` carries none. A missing source (None or no event/
    location/date) yields three UNKNOWN dimensions, never a mismatch.
    """
    if source is None or not any((source.event, source.location, source.date)):
        return ContextComparison(
            event=_dimension(
                _claim_value(current.event), None, ComparisonStatus.UNKNOWN, 0.0,
                current.event.evidence_ids, _NO_SOURCE,
            ),
            location=_dimension(
                _claim_value(current.location), None, ComparisonStatus.UNKNOWN, 0.0,
                current.location.evidence_ids, _NO_SOURCE,
            ),
            date=_dimension(
                _claim_value(current.time), None, ComparisonStatus.UNKNOWN, 0.0,
                current.time.evidence_ids, _NO_SOURCE,
            ),
        )

    cur_event = _claim_value(current.event)
    event_rel = events_relation(cur_event, source.event)
    event_status, event_conf = _pair_status(
        cur_event, source.event, event_rel, semantic_equiv, _event_known
    )

    cur_location = _claim_value(current.location)
    location_rel = location_relation(cur_location, source.location)
    location_status, location_conf = _pair_status(
        cur_location, source.location, location_rel, semantic_equiv, _location_known
    )

    cur_date = _claim_value(current.time)
    date_status, date_conf = _date_status(cur_date, source.date)

    return ContextComparison(
        event=_dimension(
            cur_event, source.event, event_status, event_conf, current.event.evidence_ids,
            _explain(
                "Event", event_status, cur_event, source.event,
                event_rel is LocationRelation.COMPATIBLE_PARENT_CHILD,
            ),
        ),
        location=_dimension(
            cur_location, source.location, location_status, location_conf,
            current.location.evidence_ids,
            _explain(
                "Location", location_status, cur_location, source.location,
                location_rel is LocationRelation.COMPATIBLE_PARENT_CHILD,
            ),
        ),
        date=_dimension(
            cur_date, source.date, date_status, date_conf, current.time.evidence_ids,
            _explain("Date", date_status, cur_date, source.date),
        ),
    )
