"""Deterministic caption-only claim extraction: a Luna-free stand-in for the
full T23 context fuser (``backend/services/context/context_fuser.py``).

The fuser needs OCR/speech/visual extractors and a configured Luna provider,
none of which are wired into the frontend-facing endpoint yet. This module
covers the "uploaded video + typed caption" demo path (PRD hackathon
priority) with the same tiny event/location dictionaries the comparator
already trusts, so a caption like "Jakarta is flooding today" resolves to
real, comparable claims without any external API key.

ponytail: keyword lookup, not NLU — swap for ``context_fuser.fuse`` (Luna)
once OCR/speech extraction and an API key are wired in.
"""

import re
from datetime import date, datetime

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ContextClaim, EvidenceAtom, EvidenceType, KeyframeRef
from backend.utils.dates import resolve_relative_date
from backend.utils.text import (
    _EVENT_CANONICAL,
    _LOCATION_ALIASES,
    _LOCATION_PARENTS,
    LocationRelation,
    location_relation,
)

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_KNOWN_LOCATIONS = {
    *_LOCATION_ALIASES.keys(),
    *_LOCATION_PARENTS.keys(),
    *_LOCATION_PARENTS.values(),
}

# Extraction-only extras: words worth pulling out of a caption that the
# comparator's own dictionary deliberately doesn't "recognize" (that
# dictionary gates a semantic-fallback path — see its tests — so it must
# stay flood-only). An unrecognized event/location still compares correctly
# by exact string match; this just widens what caption text we can find.
_EXTRACTABLE_EVENTS = {**_EVENT_CANONICAL, "protest": "protest", "unjuk rasa": "protest"}


def _unresolved() -> ContextClaim:
    return ContextClaim(value=None, normalized_value=None, confidence=0.0, evidence_ids=[], explicitly_claimed=False)


def _find_event(caption_lc: str) -> str | None:
    return next((canon for synonym, canon in _EXTRACTABLE_EVENTS.items() if synonym in caption_lc), None)


def _find_location(caption_lc: str) -> str | None:
    match = next((name for name in _KNOWN_LOCATIONS if name in caption_lc), None)
    return match.title() if match else None


def _find_date_token(caption_lc: str) -> str | None:
    iso = _ISO_DATE_RE.search(caption_lc)
    if iso is not None:
        return iso.group(1)
    for token in ("today", "yesterday"):
        if token in caption_lc:
            return token
    return None


def has_internal_conflict(caption: str | None) -> bool:
    """True when the caption names two different recognized events, or two
    non-compatible recognized locations, within itself.

    ponytail: this is a caption-only proxy for the "textual conflict" signal
    — we have no second independent text source (OCR/speech/fact-check) to
    cross-check yet, so the only deterministic conflict this endpoint can
    see is the caption contradicting itself (e.g. naming both Jakarta and
    Bangkok for the same clip). Swap for real cross-source checking once
    OCR/speech/fact-check are wired into this endpoint.
    """
    caption_lc = (caption or "").lower()
    events = {canon for synonym, canon in _EXTRACTABLE_EVENTS.items() if synonym in caption_lc}
    if len(events) > 1:
        return True
    found_locations = list(dict.fromkeys(name for name in _KNOWN_LOCATIONS if name in caption_lc))
    return any(
        location_relation(a, b) is LocationRelation.MISMATCH
        for i, a in enumerate(found_locations)
        for b in found_locations[i + 1 :]
    )


def extract_claims(ver_id: str, caption: str, keyframes: list[KeyframeRef], now: date | datetime) -> VideoContext:
    """Build a ``VideoContext`` from caption text alone using exact keyword matches."""
    caption = caption or ""
    caption_lc = caption.lower()
    atoms: list[EvidenceAtom] = []
    unresolved: list[str] = []

    if caption.strip():
        atoms.append(EvidenceAtom(evidence_id="caption_01", type=EvidenceType.USER_CAPTION, value=caption))
        evidence_ids = ["caption_01"]
    else:
        evidence_ids = []

    def _claim(value: str | None, normalized: str | None, name: str) -> ContextClaim:
        if value is None or not evidence_ids:
            unresolved.append(f"{name} claim unresolved: no valid supporting evidence")
            return _unresolved()
        return ContextClaim(
            value=value, normalized_value=normalized, confidence=0.6,
            evidence_ids=evidence_ids, explicitly_claimed=True,
        )

    event_value = _find_event(caption_lc)
    location_value = _find_location(caption_lc)
    date_token = _find_date_token(caption_lc)
    resolved_date = resolve_relative_date(date_token, now) if date_token else None

    return VideoContext(
        verification_id=ver_id,
        event=_claim(event_value, event_value, "event"),
        location=_claim(location_value, location_value, "location"),
        time=_claim(date_token, resolved_date.isoformat() if resolved_date else None, "time"),
        transcript=None,
        ocr_texts=[],
        evidence=atoms,
        keyframes=keyframes,
        unresolved=unresolved,
    )
