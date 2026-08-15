"""Provider protocol contracts (HANDOFF §5.1-5.3, §9.6, §10.4) and their
scripted fakes: fakes must satisfy runtime protocol checks, and Luna fake
outputs must enforce the requested schema through ``parse_structured``.
"""

import pytest

from backend.providers.base import (
    LunaProvider,
    OCRExtractor,
    SpeechProvider,
    VisionProvider,
    WebResearchProvider,
)
from backend.schemas.context import OCRHit, SpeechExtraction, VisualObservation
from backend.schemas.investigation import (
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
    WebSourceEvidence,
)
from backend.tests.fixtures.providers_fakes import (
    FakeLunaProvider,
    FakeOCRExtractor,
    FakeSpeechProvider,
    FakeVisionProvider,
    FakeWebResearchProvider,
)
from backend.utils.llm import StructuredOutputError


class NotAProvider:
    pass


def _web_result(task_id="web_01") -> WebResearchResult:
    return WebResearchResult(
        task_id=task_id,
        question="Did flooding occur in Jakarta on 2026-08-15?",
        status="supported",
        finding="A local news article confirms the flood.",
        evidence=[
            WebSourceEvidence(
                evidence_id="ev_1",
                url="https://example.com/flood",
                retrieved_at="2026-08-15T10:00:00Z",
            )
        ],
        unresolved=[],
        searches_used=2,
        pages_fetched=1,
    )


# --- runtime protocol checks ---


def test_fakes_satisfy_runtime_protocol_checks():
    assert isinstance(FakeSpeechProvider([]), SpeechProvider)
    assert isinstance(FakeOCRExtractor([]), OCRExtractor)
    assert isinstance(FakeLunaProvider([]), LunaProvider)
    assert isinstance(FakeVisionProvider([]), VisionProvider)
    assert isinstance(FakeWebResearchProvider([]), WebResearchProvider)


def test_protocol_check_rejects_non_conforming_object():
    assert not isinstance(NotAProvider(), SpeechProvider)


# --- speech ---


async def test_fake_speech_returns_scripted_extraction():
    expected = SpeechExtraction(transcript="some speech", language="id")
    provider = FakeSpeechProvider([expected])

    result = await provider.transcribe("/tmp/audio.wav")

    assert result == expected


async def test_fake_speech_no_audio_returns_empty_schema_object():
    provider = FakeSpeechProvider([SpeechExtraction()])

    result = await provider.transcribe("/tmp/empty.wav")

    assert result.transcript == ""
    assert result.segments == []
    assert result.confidence is None


async def test_fake_speech_scripted_exception_propagates():
    provider = FakeSpeechProvider([RuntimeError("whisper down")])

    with pytest.raises(RuntimeError, match="whisper down"):
        await provider.transcribe("/tmp/audio.wav")


# --- OCR ---


def test_fake_ocr_returns_scripted_hits():
    hits = [OCRHit(frame_id="f1", timestamp_sec=1.5, text="JAKARTA", confidence=0.9)]
    provider = FakeOCRExtractor([hits])

    assert provider.extract(["/tmp/f1.png"]) == hits


def test_fake_ocr_empty_list_is_valid():
    provider = FakeOCRExtractor([[]])

    assert provider.extract(["/tmp/f1.png"]) == []


def test_fake_ocr_scripted_exception_propagates():
    provider = FakeOCRExtractor([RuntimeError("paddle down")])

    with pytest.raises(RuntimeError, match="paddle down"):
        provider.extract(["/tmp/f1.png"])


# --- Luna ---


async def test_fake_luna_valid_raw_returns_requested_schema():
    raw = SpeechExtraction(transcript="from luna").model_dump_json()
    provider = FakeLunaProvider([raw])

    result = await provider.structured("transcribe this", SpeechExtraction)

    assert isinstance(result, SpeechExtraction)
    assert result.transcript == "from luna"


async def test_fake_luna_return_type_follows_requested_schema():
    raw = VisualObservation(scene_type="city street").model_dump_json()
    provider = FakeLunaProvider([raw])

    result = await provider.structured("describe", VisualObservation)

    assert isinstance(result, VisualObservation)
    assert result.scene_type == "city street"


async def test_fake_luna_invalid_raw_no_repair_raises():
    provider = FakeLunaProvider(["not json"])

    with pytest.raises(StructuredOutputError):
        await provider.structured("describe", VisualObservation)


async def test_fake_luna_enforces_schema():
    # Valid JSON missing required OCRHit fields must fail validation.
    provider = FakeLunaProvider(['{"frame_id": "f1"}'])

    with pytest.raises(StructuredOutputError):
        await provider.structured("read text", OCRHit)


async def test_fake_luna_repair_path():
    calls = []

    def repair_fn(raw_input, first_error):
        calls.append(1)
        return '{"transcript": "repaired"}'

    provider = FakeLunaProvider(['{"transcript": "bad",}'], repair_fn=repair_fn)

    result = await provider.structured("transcribe this", SpeechExtraction)

    assert result.transcript == "repaired"
    assert calls == [1]


async def test_fake_luna_scripted_exception_propagates():
    provider = FakeLunaProvider([RuntimeError("luna down")])

    with pytest.raises(RuntimeError, match="luna down"):
        await provider.structured("describe", VisualObservation)


# --- vision ---


def test_fake_vision_returns_scripted_candidates():
    candidates = [
        VisualWebCandidate(
            candidate_id="c1",
            frame_id="f1",
            candidate_type="full_image_match",
            url="https://example.com/img.jpg",
            raw_provider_type="FULL_MATCH",
        )
    ]
    provider = FakeVisionProvider([candidates])

    assert provider.web_detection(b"image-bytes") == candidates


def test_fake_vision_scripted_exception_propagates():
    provider = FakeVisionProvider([RuntimeError("vision down")])

    with pytest.raises(RuntimeError, match="vision down"):
        provider.web_detection(b"image-bytes")


# --- web research ---


async def test_fake_web_returns_scripted_result():
    expected = _web_result()
    provider = FakeWebResearchProvider([expected])

    task = WebResearchTask(
        task_id="web_01",
        question="Did flooding occur in Jakarta on 2026-08-15?",
        queries=["Jakarta flood 2026"],
        preferred_source_types=["news"],
    )
    result = await provider.investigate(task)

    assert result == expected


async def test_fake_web_scripted_exception_propagates():
    provider = FakeWebResearchProvider([RuntimeError("investigator down")])

    task = WebResearchTask(
        task_id="web_01",
        question="q",
        queries=["q"],
        preferred_source_types=[],
    )
    with pytest.raises(RuntimeError, match="investigator down"):
        await provider.investigate(task)
