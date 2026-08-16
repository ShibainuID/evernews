"""Deterministic event/location normalization for the comparator.

A deliberately tiny hand-curated dictionary: flood synonyms and the minimal
Jakarta/Indonesia, Bangkok/Thailand parent/child relations. Unknown input
passes through or maps to UNKNOWN — never guessed.
"""

from enum import Enum

_EVENT_CANONICAL = {
    "flood": "flood",
    "flooding": "flood",
    "major flood": "flood",
    "banjir": "flood",
    "protest": "protest",
    "unjuk rasa": "protest",
}

# ponytail: alias+parent tables are data, not logic; expand as consumers need.
_LOCATION_ALIASES = {"dki jakarta": "jakarta"}
_LOCATION_PARENTS = {"jakarta": "indonesia", "bangkok": "thailand"}


class LocationRelation(Enum):
    """Tri-state relation between two locations: never guess unknown."""

    SAME = "same"
    COMPATIBLE_PARENT_CHILD = "compatible_parent_child"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


def normalize_event(value: str | None) -> str | None:
    """Canonical event name for known synonyms; unknown input lowercased."""
    if value is None:
        return None
    key = value.strip().lower()
    return _EVENT_CANONICAL.get(key, key)


def normalize_location(value: str | None) -> str | None:
    """Stripped location string; unknown locations pass through unchanged."""
    if value is None:
        return None
    return value.strip()


def _key(value: str) -> str:
    """Lowercased, alias-resolved key; multi-part names reduce to the core city."""
    core = value.strip().lower().split(",")[0]
    return _LOCATION_ALIASES.get(core, core)


def location_relation(a: str | None, b: str | None) -> LocationRelation:
    """Same / parent-child / mismatch / unknown for two location strings."""
    if a is None or b is None:
        return LocationRelation.UNKNOWN
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return LocationRelation.SAME
    if _LOCATION_PARENTS.get(ka) == kb or _LOCATION_PARENTS.get(kb) == ka:
        return LocationRelation.COMPATIBLE_PARENT_CHILD
    return LocationRelation.MISMATCH


def events_relation(a: str | None, b: str | None) -> LocationRelation:
    """Same when normalized events match; mismatch; unknown when either is None."""
    if a is None or b is None:
        return LocationRelation.UNKNOWN
    if normalize_event(a) == normalize_event(b):
        return LocationRelation.SAME
    return LocationRelation.MISMATCH
