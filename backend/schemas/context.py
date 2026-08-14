"""Stage 1 context extraction contracts: HANDOFF §3.5 (VideoContext), §5.1-5.3 (extractors)."""

from pydantic import BaseModel

from backend.schemas.evidence import ContextClaim, EvidenceAtom, KeyframeRef


class SpeechExtraction(BaseModel):
    transcript: str = ""
    language: str | None = None
    segments: list[dict] = []
    confidence: float | None = None


class OCRHit(BaseModel):
    frame_id: str
    timestamp_sec: float
    text: str
    confidence: float
    bbox: list[list[float]] | None = None


class VisualObservation(BaseModel):
    scene_type: str | None = None
    events_visible: list[str] = []
    objects: list[str] = []
    actions: list[str] = []
    visible_text_candidates: list[str] = []
    landmarks: list[str] = []
    location_clues: list[str] = []
    environmental_clues: list[str] = []
    anomalies: list[str] = []
    uncertain_observations: list[str] = []
    evidence_frames: dict[str, list[str]] = {}


class VideoContext(BaseModel):
    verification_id: str

    event: ContextClaim
    location: ContextClaim
    time: ContextClaim

    people_or_orgs: list[str] = []
    entities: list[str] = []
    keywords: list[str] = []

    transcript: str | None = None
    ocr_texts: list[str] = []

    visual_summary: str | None = None
    visual_observations: list[str] = []
    visual_location_clues: list[str] = []

    evidence: list[EvidenceAtom]
    keyframes: list[KeyframeRef]

    unresolved: list[str] = []
