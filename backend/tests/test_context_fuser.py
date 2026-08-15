"""Context fuser (T23): caption+speech+OCR+visual -> VideoContext via Luna (text-only).

Covers: Jakarta/flood/today example with date resolved via utils/dates; evidence
atom prefixes and raw-field retention; recursive evidence_ids validity; claim
vs observation (explicitly_claimed for caption/speech/OCR, visual-only stays
False); empty speech (no-audio -> transcript ""); low-confidence OCR confidence
cap; invalid-cited-ID sanitization to unresolved; strict claims schema
hallucination guard; deterministic repeatable construction; provider-free
fallback; and the no-web-verification rule (prompt file + received prompt).
"""

import json
from datetime import date
from pathlib import Path
from typing import Any, Coroutine, TypeVar

import pytest
from pydantic import BaseModel

from backend.schemas.context import OCRHit, SpeechExtraction, VideoContext, VisualObservation
from backend.schemas.evidence import EvidenceType, KeyframeRef
from backend.services.context.context_fuser import fuse
from backend.tests.fixtures.providers_fakes import FakeLunaProvider
from backend.utils.llm import StructuredOutputError

T = TypeVar("T", bound=BaseModel)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "context_fusion.txt"
NOW = date(2026, 8, 15)


def _kf(frame_id: str = "kf_01") -> KeyframeRef:
    return KeyframeRef(frame_id=frame_id, timestamp_sec=3.5, local_path=f"/tmp/{frame_id}.png")


def _claim_dict(
    value: str,
    evidence_ids: list[str],
    confidence: float = 0.9,
    normalized: str | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "normalized_value": normalized,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "explicitly_claimed": True,
    }


def _claims_response(
    event: dict[str, Any],
    location: dict[str, Any],
    time: dict[str, Any],
    summary: str | None = None,
    entities: list[str] | None = None,
    keywords: list[str] | None = None,
) -> str:
    body: dict[str, Any] = {"event": event, "location": location, "time": time}
    if summary is not None:
        body["summary"] = summary
    if entities is not None:
        body["entities"] = entities
    if keywords is not None:
        body["keywords"] = keywords
    return json.dumps(body)


class _RecordingLuna:
    """FakeLunaProvider wrapped with a call log (prompt/schema/image_paths)."""

    def __init__(self, script: list[str | Exception]):
        self._inner = FakeLunaProvider(script)
        self.calls: list[tuple[str, Any, list[str] | None]] = []

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        image_paths: list[str] | None = None,
    ) -> T:
        self.calls.append((prompt, schema, image_paths))
        return await self._inner.structured(prompt, schema, image_paths)


# --- Jakarta/flood/today example (HANDOFF §5.4) ---


async def test_jakarta_flood_today_example_fuses_claims_with_resolved_date():
    provider = _RecordingLuna(
        [
            _claims_response(
                event=_claim_dict("flood", ["caption_01", "speech_01", "visual_01"], normalized="flood"),
                location=_claim_dict("Jakarta", ["caption_01", "speech_01", "ocr_01"], normalized="Jakarta"),
                time=_claim_dict("today", ["caption_01", "speech_01"], normalized="today"),
                summary="Video shows flooding in Jakarta today.",
                entities=["Jakarta"],
                keywords=["flood", "Jakarta", "banjir"],
            )
        ]
    )
    visual = VisualObservation(
        scene_type="urban flood", events_visible=["flood"], location_clues=["Jakarta"]
    )

    ctx = await fuse(
        ver_id="ver_123",
        caption="Jakarta flood today",
        speech=SpeechExtraction(transcript="banjir Jakarta hari ini", confidence=0.9),
        ocr=[OCRHit(frame_id="kf_01", timestamp_sec=3.0, text="JAKARTA", confidence=0.95)],
        visual=visual,
        keyframes=[_kf()],
        now=NOW,
        luna_provider=provider,
    )

    assert ctx.verification_id == "ver_123"
    # WHAT/WHERE/WHEN, all explicitly claimed by caption/speech/OCR
    assert ctx.event.value == "flood" and ctx.event.explicitly_claimed is True
    assert ctx.location.value == "Jakarta" and ctx.location.explicitly_claimed is True
    assert ctx.time.value == "today"
    assert ctx.time.normalized_value == "2026-08-15"  # resolved deterministically from now
    assert ctx.time.explicitly_claimed is True

    # every claim's evidence_ids non-empty and refer to atoms in context.evidence
    atom_ids = [a.evidence_id for a in ctx.evidence]
    assert atom_ids == ["caption_01", "speech_01", "ocr_01", "visual_01", "visual_02", "visual_03"]
    for claim in (ctx.event, ctx.location, ctx.time):
        assert claim.evidence_ids, f"{claim.value!r} has empty evidence_ids"
        for evidence_id in claim.evidence_ids:
            assert evidence_id in atom_ids, f"claim references unknown atom {evidence_id}"

    # atoms retain raw fields
    ocr_atom = next(a for a in ctx.evidence if a.evidence_id == "ocr_01")
    assert ocr_atom.raw_excerpt == "JAKARTA"
    assert ocr_atom.frame_id == "kf_01" and ocr_atom.timestamp_sec == 3.0
    assert ocr_atom.confidence == 0.95
    assert next(a for a in ctx.evidence if a.evidence_id == "speech_01").confidence == 0.9

    # derived VideoContext fields
    assert ctx.transcript == "banjir Jakarta hari ini"
    assert ctx.ocr_texts == ["JAKARTA"]
    assert ctx.visual_summary == "Video shows flooding in Jakarta today."
    assert ctx.visual_observations == ["urban flood", "flood", "Jakarta"]
    assert ctx.visual_location_clues == ["Jakarta"]
    assert ctx.entities == ["Jakarta"]
    assert ctx.keywords == ["flood", "Jakarta", "banjir"]
    assert ctx.keyframes == [_kf()]
    assert ctx.unresolved == []

    # one text-only Luna call with the evidence block
    assert len(provider.calls) == 1
    prompt, schema, image_paths = provider.calls[0]
    assert image_paths is None  # text-only: no images sent
    assert PROMPT_PATH.read_text().splitlines()[0] in prompt  # prompt built from the file
    assert "caption_01 [user_caption]: Jakarta flood today" in prompt
    assert "speech_01 [speech]: banjir Jakarta hari ini" in prompt


# --- visual-only inference is not an explicit claim ---


async def test_visual_only_claims_are_not_explicit():
    provider = _RecordingLuna(
        [
            _claims_response(
                event=_claim_dict("flood", ["visual_01"], confidence=0.7, normalized="flood"),
                location=_claim_dict("Jakarta", ["visual_02"], confidence=0.6, normalized="Jakarta"),
                time=_claim_dict("today", ["visual_01"], confidence=0.5, normalized="today"),
            )
        ]
    )
    visual = VisualObservation(
        scene_type="urban flood",
        location_clues=["Jakarta"],
        evidence_frames={"kf_01": ["water visible"]},
    )

    ctx = await fuse(
        ver_id="ver_123",
        caption="",
        speech=SpeechExtraction(),
        ocr=[],
        visual=visual,
        keyframes=[_kf()],
        now=NOW,
        luna_provider=provider,
    )

    assert ctx.event.value == "flood" and ctx.event.explicitly_claimed is False
    assert ctx.location.value == "Jakarta" and ctx.location.explicitly_claimed is False
    assert ctx.time.value == "today" and ctx.time.explicitly_claimed is False
    assert ctx.time.normalized_value == "2026-08-15"
    # visual atom retains its source field and the frame reference
    loc_atom = next(a for a in ctx.evidence if a.evidence_id == "visual_02")
    assert loc_atom.value == "Jakarta"
    assert loc_atom.notes == ["field=location_clues", "frames=kf_01"]
    assert [a.evidence_id for a in ctx.evidence] == ["visual_01", "visual_02"]


# --- unsupported claims become unresolved, never fabricated ---


async def test_invalid_cited_evidence_ids_become_unresolved_claims():
    provider = _RecordingLuna(
        [
            _claims_response(
                event=_claim_dict("flood", ["caption_01"], normalized="flood"),
                location=_claim_dict("Jakarta", ["bogus_99"], normalized="Jakarta"),
                time=_claim_dict("today", ["speech_01"], normalized="today"),
            )
        ]
    )

    ctx = await fuse(
        ver_id="ver_123",
        caption="Jakarta flood today",
        speech=SpeechExtraction(transcript="banjir Jakarta hari ini"),
        ocr=[],
        visual=VisualObservation(),
        keyframes=[_kf()],
        now=NOW,
        luna_provider=provider,
    )

    assert ctx.event.value == "flood"
    assert ctx.time.value == "today"
    assert ctx.location.value is None  # no valid support -> not fabricated
    assert ctx.location.confidence == 0.0
    assert ctx.location.evidence_ids == []
    assert ctx.location.explicitly_claimed is False
    assert any("location claim unresolved" in note for note in ctx.unresolved)
    # input evidence is preserved even when no claim used it
    assert [a.evidence_id for a in ctx.evidence] == ["caption_01", "speech_01"]


# --- no-audio / empty speech ---


async def test_empty_speech_no_audio_yields_empty_transcript_without_error():
    provider = _RecordingLuna(
        [
            _claims_response(
                event=_claim_dict("flood", ["caption_01"], normalized="flood"),
                location=_claim_dict("Jakarta", ["caption_01"], normalized="Jakarta"),
                time=_claim_dict("today", ["caption_01"], normalized="today"),
            )
        ]
    )

    ctx = await fuse(
        ver_id="ver_123",
        caption="Jakarta flood today",
        speech=SpeechExtraction(),
        ocr=[],
        visual=VisualObservation(),
        keyframes=[_kf()],
        now=NOW,
        luna_provider=provider,
    )

    assert ctx.transcript == ""
    assert not [a for a in ctx.evidence if a.type is EvidenceType.SPEECH]
    assert ctx.event.value == "flood"  # fusion continues without speech


# --- low-confidence OCR guard ---


async def test_ocr_only_claim_confidence_capped_when_ocr_is_low_confidence():
    async def run(ocr_confidence: float) -> VideoContext:
        provider = _RecordingLuna(
            [
                _claims_response(
                    event=_claim_dict("flood", ["ocr_01"], confidence=0.9),
                    location=_claim_dict("Jakarta", ["ocr_01"], confidence=0.9),
                    time=_claim_dict("today", ["ocr_01"], confidence=0.9),
                )
            ]
        )
        return await fuse(
            ver_id="ver_123",
            caption="",
            speech=SpeechExtraction(),
            ocr=[
                OCRHit(
                    frame_id="kf_01",
                    timestamp_sec=1.0,
                    text="JAKARTA",
                    confidence=ocr_confidence,
                )
            ],
            visual=VisualObservation(),
            keyframes=[_kf()],
            now=NOW,
            luna_provider=provider,
        )

    low = await run(0.45)
    assert low.location.value == "Jakarta"
    assert low.location.confidence == 0.5  # capped: low-confidence OCR alone
    assert low.location.explicitly_claimed is True  # OCR is explicit, not inference
    assert low.location.evidence_ids == ["ocr_01"]  # claim/evidence retained for review

    high = await run(0.95)
    assert high.location.confidence == 0.9  # not capped: OCR confidence is fine


# --- strict claims schema (hallucination guard) ---


async def test_claims_schema_rejects_extra_fields_like_a_verdict():
    event = _claim_dict("flood", ["caption_01"])
    event["verdict"] = "fake"  # final-verdict style extra field must fail validation
    provider = FakeLunaProvider(
        [
            _claims_response(
                event=event,
                location=_claim_dict("Jakarta", ["caption_01"]),
                time=_claim_dict("today", ["caption_01"]),
            )
        ]
    )

    with pytest.raises(StructuredOutputError):
        await fuse(
            ver_id="ver_123",
            caption="Jakarta flood today",
            speech=SpeechExtraction(),
            ocr=[],
            visual=VisualObservation(),
            keyframes=[_kf()],
            now=NOW,
            luna_provider=provider,
        )


# --- no web verification ---


def test_prompt_file_forbids_web_verification():
    prompt = PROMPT_PATH.read_text()
    assert "Do not perform web verification" in prompt
    assert "{evidence}" in prompt  # local evidence block placeholder


async def test_received_prompt_contains_only_local_input_evidence():
    provider = _RecordingLuna(
        [
            _claims_response(
                event=_claim_dict("flood", ["caption_01"]),
                location=_claim_dict("Jakarta", ["caption_01"]),
                time=_claim_dict("today", ["caption_01"]),
            )
        ]
    )

    await fuse(
        ver_id="ver_123",
        caption="Jakarta flood today",
        speech=SpeechExtraction(transcript="banjir Jakarta hari ini"),
        ocr=[OCRHit(frame_id="kf_01", timestamp_sec=1.0, text="JAKARTA", confidence=0.9)],
        visual=VisualObservation(),
        keyframes=[_kf()],
        now=NOW,
        luna_provider=provider,
    )

    received = provider.calls[0][0]
    assert "Do not perform web verification" in received
    assert "caption_01 [user_caption]: Jakarta flood today" in received
    assert "ocr_01 [ocr]: JAKARTA" in received
    assert "https://" not in received  # no web result content ever injected


# --- deterministic construction ---


async def test_fusion_is_deterministic_for_identical_inputs():
    def run() -> Coroutine[Any, Any, VideoContext]:
        provider = _RecordingLuna(
            [
                _claims_response(
                    event=_claim_dict("flood", ["caption_01"], normalized="flood"),
                    location=_claim_dict("Jakarta", ["caption_01"], normalized="Jakarta"),
                    time=_claim_dict("today", ["caption_01"], normalized="today"),
                )
            ]
        )
        return fuse(
            ver_id="ver_123",
            caption="Jakarta flood today",
            speech=SpeechExtraction(transcript="banjir Jakarta hari ini"),
            ocr=[OCRHit(frame_id="kf_01", timestamp_sec=1.0, text="JAKARTA", confidence=0.9)],
            visual=VisualObservation(scene_type="urban flood"),
            keyframes=[_kf()],
            now=NOW,
            luna_provider=provider,
        )

    first = await run()
    second = await run()
    assert first.model_dump() == second.model_dump()


async def test_provider_none_returns_unresolved_claims_with_deterministic_evidence():
    ctx = await fuse(
        ver_id="ver_123",
        caption="Jakarta flood today",
        speech=SpeechExtraction(transcript="banjir Jakarta hari ini"),
        ocr=[],
        visual=VisualObservation(),
        keyframes=[_kf()],
        now=NOW,
    )

    for claim in (ctx.event, ctx.location, ctx.time):
        assert claim.value is None
        assert claim.confidence == 0.0
        assert claim.evidence_ids == []
        assert claim.explicitly_claimed is False
    assert len(ctx.unresolved) == 3
    assert [a.evidence_id for a in ctx.evidence] == ["caption_01", "speech_01"]
    assert ctx.transcript == "banjir Jakarta hari ini"
