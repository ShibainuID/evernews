"""Context fusion (T23): caption+speech+OCR+visual -> VideoContext (one text-only Luna call).

Deterministic ``EvidenceAtom`` building (IDs prefixed ``caption_``/``speech_``/
``ocr_``/``visual_``) happens before the Luna call; the prompt carries that atom
list so the model can only cite real IDs. Cited claim evidence IDs are then
sanitized against the built atoms: a dimension without valid support becomes an
unresolved claim (value None, confidence 0, empty evidence_ids,
explicitly_claimed False) plus an unresolved note — never a fabricated one.

``explicitly_claimed`` is set locally, not trusted from the model: True only
when caption/speech/OCR support the claim; visual-only inference stays False.
OCR-only claims whose every OCR atom is low-confidence are confidence-capped at
0.5 (OCR is evidence, not truth) but the claim/evidence is retained. Relative
time is normalized deterministically via
``backend.utils.dates.resolve_relative_date`` against the supplied ``now``.
No web verification happens here: the provider receives only the local
evidence block (HANDOFF §5.4: "You do not verify whether they are true").
"""

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from backend.providers.base import LunaProvider
from backend.schemas.context import OCRHit, SpeechExtraction, VideoContext, VisualObservation
from backend.schemas.evidence import ContextClaim, EvidenceAtom, EvidenceType, KeyframeRef
from backend.utils.dates import resolve_relative_date

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "context_fusion.txt"
PROMPT = PROMPT_PATH.read_text()

_EXPLICIT_TYPES = frozenset(
    {EvidenceType.USER_CAPTION, EvidenceType.SPEECH, EvidenceType.OCR}
)
_OCR_LOW_CONFIDENCE = 0.6  # OCR atoms below this are "low confidence"
_OCR_ONLY_CONFIDENCE_CAP = 0.5
_UNRESOLVED_TEMPLATE = "{name} claim unresolved: no valid supporting evidence"


class _StrictClaim(ContextClaim):
    """Hallucination guard: reject any extra JSON field (e.g. a guessed verdict)."""

    model_config = ConfigDict(extra="forbid")


class _LocalClaims(BaseModel):
    """Small strict claims schema Luna fills in; VideoContext is built locally."""

    model_config = ConfigDict(extra="forbid")

    event: _StrictClaim
    location: _StrictClaim
    time: _StrictClaim
    summary: str | None = None
    entities: list[str] = []
    keywords: list[str] = []


# --- deterministic evidence building ---


def _visual_items(visual: VisualObservation) -> list[tuple[str, str]]:
    """(value, source_field) pairs for every non-empty observation, in schema order."""
    items: list[tuple[str, str]] = []
    for field in VisualObservation.model_fields:
        if field == "evidence_frames":
            continue
        value = getattr(visual, field)
        if isinstance(value, list):
            items.extend((item, field) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            items.append((value.strip(), field))
    return items


def _frames_note(visual: VisualObservation) -> str | None:
    if not visual.evidence_frames:
        return None
    return f"frames={','.join(sorted(visual.evidence_frames))}"


def _build_atoms(
    caption: str,
    speech: SpeechExtraction,
    ocr: list[OCRHit],
    visual: VisualObservation,
) -> list[EvidenceAtom]:
    """Deterministic evidence atoms, built before the Luna call."""
    atoms: list[EvidenceAtom] = []
    if caption.strip():
        atoms.append(
            EvidenceAtom(
                evidence_id="caption_01", type=EvidenceType.USER_CAPTION, value=caption
            )
        )
    if speech.transcript.strip():
        atoms.append(
            EvidenceAtom(
                evidence_id="speech_01",
                type=EvidenceType.SPEECH,
                value=speech.transcript,
                confidence=speech.confidence,
            )
        )
    for index, hit in enumerate(ocr, start=1):
        notes = [f"bbox={hit.bbox}"] if hit.bbox is not None else []
        atoms.append(
            EvidenceAtom(
                evidence_id=f"ocr_{index:02d}",
                type=EvidenceType.OCR,
                value=hit.text,
                confidence=hit.confidence,
                frame_id=hit.frame_id,
                timestamp_sec=hit.timestamp_sec,
                raw_excerpt=hit.text,
                notes=notes,
            )
        )
    frames_note = _frames_note(visual)
    for index, (item, field) in enumerate(_visual_items(visual), start=1):
        notes = [f"field={field}"]
        if frames_note is not None:
            notes.append(frames_note)
        atoms.append(
            EvidenceAtom(
                evidence_id=f"visual_{index:02d}",
                type=EvidenceType.VISUAL,
                value=item,
                notes=notes,
            )
        )
    return atoms


def _format_evidence(atoms: list[EvidenceAtom]) -> str:
    return "\n".join(f"- {a.evidence_id} [{a.type.value}]: {a.value}" for a in atoms)


# --- claim sanitization ---


def _unresolved_claim() -> ContextClaim:
    return ContextClaim(
        value=None,
        normalized_value=None,
        confidence=0.0,
        evidence_ids=[],
        explicitly_claimed=False,
    )


def _resolve_claim(
    claim: ContextClaim, atoms: list[EvidenceAtom], name: str
) -> tuple[ContextClaim, str | None]:
    """Map a model claim onto real atoms; unsupported claims become unresolved.

    Cited IDs are sanitized to IDs actually present (ordered, de-duplicated).
    ``explicitly_claimed`` is decided locally from the supporting atom types;
    OCR-only claims with only low-confidence atoms are capped at 0.5.
    """
    atom_ids = {a.evidence_id for a in atoms}
    valid_ids = list(dict.fromkeys(eid for eid in claim.evidence_ids if eid in atom_ids))
    if claim.value is None or not valid_ids:
        return _unresolved_claim(), _UNRESOLVED_TEMPLATE.format(name=name)
    support = [a for a in atoms if a.evidence_id in valid_ids]
    types = {a.type for a in support}
    confidence = claim.confidence
    if types <= {EvidenceType.OCR} and all(
        a.confidence is not None and a.confidence < _OCR_LOW_CONFIDENCE for a in support
    ):
        confidence = min(confidence, _OCR_ONLY_CONFIDENCE_CAP)
    return (
        ContextClaim(
            value=claim.value,
            normalized_value=claim.normalized_value,
            confidence=confidence,
            evidence_ids=valid_ids,
            explicitly_claimed=bool(types & _EXPLICIT_TYPES),
        ),
        None,
    )


def _normalize_time(claim: ContextClaim, now: date | datetime) -> str | None:
    """Resolve relative time expressions deterministically; else keep the model value."""
    resolved = resolve_relative_date(claim.value, now) if claim.value is not None else None
    return resolved.isoformat() if resolved is not None else claim.normalized_value


async def fuse(
    ver_id: str,
    caption: str,
    speech: SpeechExtraction,
    ocr: list[OCRHit],
    visual: VisualObservation,
    keyframes: list[KeyframeRef],
    now: date | datetime,
    luna_provider: LunaProvider | None = None,
) -> VideoContext:
    """Fuse caption/speech/OCR/visual observations into an evidence-linked VideoContext.

    One text-only structured Luna call (no images) extracts local claims; the
    VideoContext itself is constructed deterministically. Without a provider,
    every dimension stays unresolved but the evidence atoms are still built.
    """
    atoms = _build_atoms(caption, speech, ocr, visual)

    claims: _LocalClaims | None = None
    if luna_provider is not None:
        claims = await luna_provider.structured(
            PROMPT.format(evidence=_format_evidence(atoms)), _LocalClaims
        )

    if claims is None:
        event = location = time = _unresolved_claim()
        unresolved = [_UNRESOLVED_TEMPLATE.format(name=n) for n in ("event", "location", "time")]
        summary, entities, keywords = None, [], []
    else:
        event, event_note = _resolve_claim(claims.event, atoms, "event")
        location, location_note = _resolve_claim(claims.location, atoms, "location")
        time, time_note = _resolve_claim(claims.time, atoms, "time")
        unresolved = [n for n in (event_note, location_note, time_note) if n]
        summary, entities, keywords = claims.summary, list(claims.entities), list(claims.keywords)

    time.normalized_value = _normalize_time(time, now)

    return VideoContext(
        verification_id=ver_id,
        event=event,
        location=location,
        time=time,
        entities=entities,
        keywords=keywords,
        transcript=speech.transcript,
        ocr_texts=[hit.text for hit in ocr],
        visual_summary=summary,
        visual_observations=[a.value for a in atoms if a.type is EvidenceType.VISUAL],
        visual_location_clues=list(visual.location_clues),
        evidence=atoms,
        keyframes=keyframes,
        unresolved=unresolved,
    )
