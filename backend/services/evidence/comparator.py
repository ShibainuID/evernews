"""Deterministic WHAT/WHERE/WHEN context comparison (T15): HANDOFF §17, §37.

Small exact rules on top of the T08/T09 dictionaries. UNKNOWN propagates when a
side is missing or a value does not resolve and never becomes MISMATCH. A
``semantic_equiv`` fallback resolves only pairs the tiny deterministic
dictionaries cannot resolve (relation MISMATCH); it is never consulted for
exact/synonym/parent-child matches or when either side is missing.
"""

from collections.abc import Callable
from datetime import date

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ComparisonStatus, ContextClaim
from backend.schemas.result import ContextComparison, DimensionComparison, SourceContext
from backend.utils.text import LocationRelation, events_relation, location_relation

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


def _pair_status(
    cur: str | None,
    src: str | None,
    relation: LocationRelation,
    semantic_equiv: Callable[[str, str], bool | None] | None,
) -> tuple[ComparisonStatus, float]:
    """Status+confidence for one event/location pair (HANDOFF §17.1, §17.3).

    The mismatch guard lives here: either side missing -> UNKNOWN, so a pair
    can never be judged a mismatch without both values populated.
    """
    if cur is None or src is None:
        return ComparisonStatus.UNKNOWN, 0.0
    if relation is LocationRelation.MISMATCH:
        if semantic_equiv is None:
            return ComparisonStatus.MISMATCH, 1.0
        result = semantic_equiv(cur, src)
        if result is True:
            return ComparisonStatus.CONSISTENT, 1.0
        if result is False:
            return ComparisonStatus.MISMATCH, 1.0
        return ComparisonStatus.UNKNOWN, 0.0
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
    event_status, event_conf = _pair_status(cur_event, source.event, event_rel, semantic_equiv)

    cur_location = _claim_value(current.location)
    location_rel = location_relation(cur_location, source.location)
    location_status, location_conf = _pair_status(
        cur_location, source.location, location_rel, semantic_equiv
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
