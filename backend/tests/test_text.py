"""Event/location normalization tests: synonyms, parent/child, unknown handling."""

from backend.utils.text import (
    LocationRelation,
    events_relation,
    location_relation,
    normalize_event,
    normalize_location,
)


def test_normalize_event_synonyms():
    assert normalize_event("flooding") == "flood"
    assert normalize_event("major flood") == "flood"
    assert normalize_event("banjir") == "flood"
    assert normalize_event("flood") == "flood"


def test_normalize_event_unknown_passthrough_lowercased():
    assert normalize_event("Wildfire") == "wildfire"
    assert normalize_event(None) is None


def test_normalize_location_passthrough():
    assert normalize_location("Jakarta, Indonesia") == "Jakarta, Indonesia"
    assert normalize_location("  Jakarta  ") == "Jakarta"
    assert normalize_location(None) is None


def test_location_relation_same():
    assert location_relation("Jakarta", "Jakarta, Indonesia") is LocationRelation.SAME


def test_location_relation_compatible_parent_child():
    assert (
        location_relation("Jakarta", "Indonesia")
        is LocationRelation.COMPATIBLE_PARENT_CHILD
    )


def test_location_relation_mismatch():
    assert location_relation("Jakarta", "Bangkok") is LocationRelation.MISMATCH


def test_location_relation_unknown_not_mismatch():
    assert location_relation("Jakarta", None) is LocationRelation.UNKNOWN
    assert location_relation(None, "Jakarta") is LocationRelation.UNKNOWN
    assert location_relation(None, None) is LocationRelation.UNKNOWN


def test_location_relation_dki_jakarta_alias():
    assert location_relation("DKI Jakarta", "Jakarta") is LocationRelation.SAME
    assert location_relation("DKI Jakarta", "Bangkok") is LocationRelation.MISMATCH


def test_events_relation():
    assert events_relation("flood", "banjir") is LocationRelation.SAME
    assert events_relation("flood", "wildfire") is LocationRelation.MISMATCH
    assert events_relation("flood", None) is LocationRelation.UNKNOWN
