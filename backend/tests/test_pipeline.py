"""T35: end-to-end pipeline + state store tests (TDD red-green).

Every test is fake-driven against ``video_factory`` real media (ffmpeg
preprocessing is the repo-established integration seam; heavy native
providers are never loaded). The Luna fake dispatches by call shape
(``image_paths`` = one visual-extraction call per keyframe, unbounded count;
text calls pop fusion -> plan -> synthesis raws), so ffmpeg's keyframe count
never makes the script brittle. ``isolated=False`` keeps scripted fakes
in-process; the process boundary itself is proven separately with
importable, picklable providers spawned as real children.

Pinned behaviors: HANDOFF §38 serial order and §22.2 stage names; golden
cases A-D (HANDOFF §36) incl. Case D never possible_false_context; §26 error
matrix (no audio continues, >15s reject with video_too_long, whisper failure
-> empty speech evidence, fact-check branch error, investigator timeout ->
insufficient web branch); vision-to-demo fallback; and recursive
evidence-ID invariants on every result (design §5).
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from backend import state as state_module
from backend.schemas.context import OCRHit, SpeechExtraction
from backend.schemas.evidence import ComparisonStatus, EvidenceType, ResultClassification
from backend.schemas.result import SourceCandidate, VerificationResult
from backend.services import pipeline
from backend.services.ingestion.video_ingestor import new_verification_id
from backend.services.preprocessing.ffmpeg import PreprocessingError
from backend.state import VerificationStateStore
from backend.tests.fixtures.golden_cases import (
    build_fact_check,
    build_visual_candidate,
    build_web_research,
    build_web_source,
)
from backend.tests.fixtures.providers_fakes import (
    FakeOCRExtractor,
    FakeSpeechProvider,
    FakeVisionProvider,
    FakeWebResearchProvider,
)
from backend.tests.fixtures.video_factory import (
    make_video,
    make_video_20s,
    make_video_no_audio,
    require_ffmpeg,
)
from backend.utils.llm import parse_structured
from backend.utils.urls import canonicalize


# --- scripted seams (pipeline-local, deterministic) ---


class ScriptedLuna:
    """Schema-agnostic scripted Luna fake (T21 discipline via parse_structured).

    Image calls are unbounded (one per keyframe; the count depends on ffmpeg
    scene detection) so each gets a fresh ``visual_builder`` raw; text calls
    pop the scripted raws in order: fusion -> plan -> synthesis.
    """

    def __init__(self, script: list[str | Exception], visual_builder: Callable[[], str] | None = None):
        self._script = list(script)
        self._visual_builder = visual_builder or _visual_raw

    async def structured(self, prompt: str, schema: type[Any], image_paths: list[str] | None = None) -> Any:
        if image_paths is not None:
            raw: str | Exception = self._visual_builder()
        else:
            if not self._script:
                raise AssertionError("ScriptedLuna text script exhausted")
            raw = self._script.pop(0)
        if isinstance(raw, Exception):
            raise raw
        return parse_structured(raw, schema)


class ScriptedFactCheckRunner:
    """Scripted ``list[FactCheckEvidence]`` or ``Exception`` per task (T28 seam)."""

    def __init__(self, script: list[Any]):
        self._script = list(script)

    async def __call__(self, task: Any) -> list[Any]:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedDemoIndex:
    """Scripted ``list[SourceCandidate]`` or ``Exception``; records frame paths (T27 seam)."""

    def __init__(self, script: list[Any]):
        self._script = list(script)

    def search(self, frames: list[str]) -> list[SourceCandidate]:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingStore(VerificationStateStore):
    """State store recording every update, for stage-sequence assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.updates: list[dict[str, Any]] = []

    def update(self, ver_id: str, **changes: Any) -> None:
        self.updates.append(dict(changes))
        super().update(ver_id, **changes)


# --- heavy-provider isolation fakes (module-level: picklable + spawn-importable) ---


class _PidWritingSpeech:
    """Writes the pid of the process it transcribes in; proves the child boundary."""

    def __init__(self, pid_file: str):
        self._pid_file = pid_file

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        Path(self._pid_file).write_text(str(os.getpid()))
        return SpeechExtraction(transcript="spoken in a child process")


class _FailingSpeech:
    """Raises in the child, like a broken faster-whisper load."""

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        raise RuntimeError("child whisper exploded")


class _SleepingSpeech:
    """Sleeps past any sane budget; the pipeline must terminate it."""

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        await asyncio.sleep(30)
        return SpeechExtraction(transcript="too late")


class _FailingOcr:
    """Raises in the child, like a broken PaddleOCR load."""

    def extract(self, frame_paths: list[str]) -> list[OCRHit]:
        raise RuntimeError("child paddle exploded")


# --- scripted output builders ---


def _claim(value: str, evidence_ids: list[str], confidence: float = 0.9, normalized: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "value": value,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "explicitly_claimed": True,
    }
    if normalized is not None:
        body["normalized_value"] = normalized
    return body


def _fusion_raw(event: dict[str, Any], location: dict[str, Any], time: dict[str, Any], **extra: Any) -> str:
    body: dict[str, Any] = {"event": event, "location": location, "time": time}
    body.update(extra)
    return json.dumps(body)


def _plan_raw(ver_id: str) -> str:
    return json.dumps(
        {
            "verification_id": ver_id,
            "investigation_questions": ["Did the claimed event happen when and where stated?"],
            "stop_conditions": ["Stop when at least two reputable sources answer the question."],
            "fact_check_tasks": [
                {"task_id": "fc_01", "queries": ["Jakarta banjir 2026"], "goal": "Find existing fact checks"}
            ],
            "web_research_tasks": [
                {
                    "task_id": "web_01",
                    "question": "When was this footage first published?",
                    "queries": ["flood footage Jakarta"],
                    "preferred_source_types": ["news"],
                }
            ],
            "visual_search_tasks": [
                {
                    "task_id": "vis_01",
                    "frame_ids": [f"{ver_id}_kf000"],
                    "goal": "Find pages using the same footage",
                }
            ],
        }
    )


def _synthesis_raw(
    finding: str,
    summary: str,
    best_source_id: str | None = None,
    supporting: list[str] | None = None,
    unresolved: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "event_web_finding": finding,
            "synthesis_summary": summary,
            "existing_fact_checks_found": True,
            "best_visual_source_id": best_source_id,
            "supporting_evidence_ids": supporting or [],
            "contradicting_evidence_ids": [],
            "conflicts": [],
            "unresolved": unresolved or [],
        }
    )


def _visual_raw() -> str:
    return json.dumps(
        {
            "scene_type": "urban flooding",
            "events_visible": ["flood"],
            "objects": ["vehicles"],
            "environmental_clues": ["deep water"],
        }
    )


def _source_id(url: str) -> str:
    """Replicates normalizer._to_candidate's deterministic source id."""
    return "src_" + hashlib.sha256(canonicalize(url).encode()).hexdigest()[:12]


async def _never_fetch(url: str) -> Any:
    # no page_match candidate is scripted, so the enrichment fetcher is never used
    raise AssertionError(f"page fetcher must not be called: {url}")


def _providers(**seams: Any) -> pipeline.Providers:
    return pipeline.Providers(
        speech=seams["speech"],
        ocr=seams["ocr"],
        luna=seams["luna"],
        vision=seams["vision"],
        web_research=seams["web"],
        fact_check=seams.get("fact_check"),
        demo_index=seams.get("demo_index", ScriptedDemoIndex([]).search),
        page_fetcher=seams.get("page_fetcher", _never_fetch),
        isolated=seams.get("isolated", False),
    )


def assert_evidence_invariants(result: VerificationResult) -> None:
    """Recursive evidence-grounding invariants (design §5): value-bearing
    statements cite evidence; evidence-less claims stay explicitly unresolved
    (never invented IDs); strongest_evidence_ids are backed by presented sources."""
    for claim in (result.current_context.event, result.current_context.location, result.current_context.time):
        if claim.value is not None:
            assert claim.evidence_ids, f"claim {claim.value!r} has no evidence ids"
        else:
            assert claim.evidence_ids == [], f"unresolved claim invented ids: {claim.evidence_ids}"
            assert claim.explicitly_claimed is False
    for dim in (result.comparison.event, result.comparison.location, result.comparison.date):
        if dim.current is not None:
            assert dim.evidence_ids, f"comparison dimension {dim.current!r} has no evidence ids"
        else:
            assert dim.status is ComparisonStatus.UNKNOWN, "missing current value must stay UNKNOWN"
    known_ids = {eid for source in result.sources for eid in source.evidence_ids}
    for eid in result.strongest_evidence_ids:
        assert eid in known_ids, f"strongest evidence id {eid} is not backed by any presented source"
    for source in result.sources:
        assert source.evidence_ids, f"source {source.source_id} has no evidence ids"


# --- fixtures ---


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolated WORKDIR; default settings otherwise (test_ffmpeg pattern)."""
    monkeypatch.delenv("WORKDIR", raising=False)
    from backend.config import Settings

    return Settings(workdir=str(tmp_path / "work"))


@pytest.fixture(scope="module")
def video(tmp_path_factory: Any) -> Path:
    """6s synthetic MP4 with audio, encoded once per module."""
    require_ffmpeg()
    return make_video(tmp_path_factory.mktemp("media"))


@pytest.fixture()
def media(tmp_path: Path) -> Path:
    """tmp dir with ffmpeg available, for per-test synthetic media."""
    require_ffmpeg()
    return tmp_path


# --- golden case scripts (HANDOFF §36) ---


def _luna_a(ver_id: str, synthesis_raw: str) -> ScriptedLuna:
    """Case-A Luna script (fusion + plan) with a caller-chosen synthesis raw,
    so branch-failure tests can cite only the evidence that survived."""
    return ScriptedLuna(
        [
            _fusion_raw(
                _claim("flood", ["speech_01"], 0.96, normalized="flood"),
                _claim("Jakarta", ["speech_01"], 0.92, normalized="Jakarta, Indonesia"),
                _claim("today", ["speech_01"], 0.87),
                entities=["Jakarta"],
                keywords=["flood", "banjir"],
            ),
            _plan_raw(ver_id),
            synthesis_raw,
        ]
    )


def _case_a(ver_id: str) -> pipeline.Providers:
    url = "https://example.com/article/bangkok-flood"
    return _providers(
        speech=FakeSpeechProvider(
            [SpeechExtraction(transcript="banjir Jakarta pagi ini semakin parah", language="id")]
        ),
        ocr=FakeOCRExtractor([[]]),
        luna=_luna_a(
            ver_id,
            _synthesis_raw(
                "supported",
                "The footage matches Bangkok flooding from October 2022.",
                best_source_id=_source_id(url),
                supporting=["fc_a_01", "web_a_01"],
            ),
        ),
        vision=FakeVisionProvider([[build_visual_candidate("vis_a_01", "kf_01", url=url)]]),
        web=FakeWebResearchProvider(
            [
                build_web_research(
                    "web_a_01",
                    "When was this flood footage first published?",
                    "The footage matches Bangkok flooding from October 2022.",
                    [
                        build_web_source(
                            "web_a_01",
                            url,
                            publisher="Example News",
                            title="Flooding in Bangkok",
                            published_at="2022-10-03",
                            event="flood",
                            location="Bangkok",
                            date_context="2022-10-03",
                            supports_question=True,
                            relevance_score=0.95,
                        )
                    ],
                )
            ]
        ),
        fact_check=ScriptedFactCheckRunner(
            [
                [
                    build_fact_check(
                        "fc_a_01",
                        "Jakarta banjir 2026",
                        review_url=url,
                        publisher="Example Fact Check",
                        review_date="2022-10-05",
                        textual_rating="old footage",
                        relevance_score=0.9,
                    )
                ]
            ]
        ),
    )


def _case_b(ver_id: str) -> pipeline.Providers:
    url = "https://example.com/article/jakarta-protest-2023"
    return _providers(
        speech=FakeSpeechProvider(
            [SpeechExtraction(transcript="demonstrasi di Jakarta hari ini", language="id")]
        ),
        ocr=FakeOCRExtractor([[]]),
        luna=ScriptedLuna(
            [
                _fusion_raw(
                    _claim("protest", ["speech_01"], 0.95, normalized="protest"),
                    _claim("Jakarta", ["speech_01"], 0.93, normalized="Jakarta, Indonesia"),
                    _claim("today", ["speech_01"], 0.9),
                    entities=["Jakarta"],
                    keywords=["protest", "demonstrasi"],
                ),
                _plan_raw(ver_id),
                _synthesis_raw(
                    "supported",
                    "The footage matches a Jakarta protest from June 2023.",
                    best_source_id=_source_id(url),
                    supporting=["fc_b_01", "web_b_01"],
                ),
            ]
        ),
        vision=FakeVisionProvider([[build_visual_candidate("vis_b_01", "kf_01", url=url)]]),
        web=FakeWebResearchProvider(
            [
                build_web_research(
                    "web_b_01",
                    "When was this protest footage first published?",
                    "The footage matches a Jakarta protest from June 2023.",
                    [
                        build_web_source(
                            "web_b_01",
                            url,
                            publisher="Example News",
                            title="Protest in Jakarta in 2023",
                            published_at="2023-06-05",
                            event="protest",
                            location="Jakarta",
                            date_context="2023-06-05",
                            supports_question=True,
                            relevance_score=0.93,
                        )
                    ],
                )
            ]
        ),
        fact_check=ScriptedFactCheckRunner(
            [
                [
                    build_fact_check(
                        "fc_b_01",
                        "Jakarta demo hari ini 2026",
                        review_url=url,
                        publisher="Example Fact Check",
                        review_date="2023-06-07",
                        textual_rating="old footage",
                        relevance_score=0.88,
                    )
                ]
            ]
        ),
    )


def _case_c(ver_id: str) -> pipeline.Providers:
    url = "https://example.com/article/jakarta-flood-2022"
    return _providers(
        speech=FakeSpeechProvider([SpeechExtraction(transcript="banjir Jakarta 3 Oktober 2022", language="id")]),
        ocr=FakeOCRExtractor([[]]),
        luna=ScriptedLuna(
            [
                _fusion_raw(
                    _claim("flood", ["speech_01"], 0.96, normalized="flood"),
                    _claim("Jakarta", ["speech_01"], 0.94, normalized="Jakarta, Indonesia"),
                    _claim("3 Oktober 2022", ["speech_01"], 0.91, normalized="2022-10-03"),
                    entities=["Jakarta"],
                    keywords=["flood", "banjir"],
                ),
                _plan_raw(ver_id),
                _synthesis_raw(
                    "supported",
                    "The footage and metadata consistently describe Jakarta flooding in October 2022.",
                    best_source_id=_source_id(url),
                    supporting=["fc_c_01", "web_c_01"],
                ),
            ]
        ),
        vision=FakeVisionProvider([[build_visual_candidate("vis_c_01", "kf_01", url=url)]]),
        web=FakeWebResearchProvider(
            [
                build_web_research(
                    "web_c_01",
                    "Does this flood footage match Jakarta, October 2022?",
                    "The footage and metadata consistently describe Jakarta flooding in October 2022.",
                    [
                        build_web_source(
                            "web_c_01",
                            url,
                            publisher="Example News",
                            title="Flooding in Jakarta",
                            published_at="2022-10-03",
                            event="flood",
                            location="Jakarta",
                            date_context="2022-10-03",
                            supports_question=True,
                            relevance_score=0.96,
                        )
                    ],
                )
            ]
        ),
        fact_check=ScriptedFactCheckRunner(
            [
                [
                    build_fact_check(
                        "fc_c_01",
                        "Jakarta banjir 3 Oktober 2022",
                        review_url=url,
                        publisher="Example Fact Check",
                        review_date="2022-10-04",
                        textual_rating="accurate",
                        relevance_score=0.95,
                    )
                ]
            ]
        ),
    )


def _case_d(ver_id: str) -> pipeline.Providers:
    return _providers(
        speech=FakeSpeechProvider([SpeechExtraction(transcript="gempa di Yogyakarta", language="id")]),
        ocr=FakeOCRExtractor([[]]),
        luna=ScriptedLuna(
            [
                _fusion_raw(
                    _claim("earthquake", ["speech_01"], 0.7, normalized="earthquake"),
                    _claim("Yogyakarta", ["speech_01"], 0.8, normalized="Yogyakarta, Indonesia"),
                    _claim("today", ["speech_01"], 0.75),
                    entities=["Yogyakarta"],
                    keywords=["earthquake", "gempa"],
                ),
                _plan_raw(ver_id),
                _synthesis_raw(
                    "insufficient",
                    "No source could be matched or retrieved.",
                ),
            ]
        ),
        vision=FakeVisionProvider([[]]),
        web=FakeWebResearchProvider(
            [
                build_web_research(
                    "web_d_01",
                    "Was there an earthquake in Yogyakarta today?",
                    "No reliable source could be retrieved.",
                    [],
                    status="insufficient",
                )
            ]
        ),
        fact_check=ScriptedFactCheckRunner([[]]),
        demo_index=ScriptedDemoIndex([[]]).search,
    )


# --- golden cases end-to-end ---


async def test_case_a_golden_possible_false_context(settings: Any, video: Path):
    ver_id = new_verification_id()
    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_a(ver_id), settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.classification is ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert result.visual_match == "high"
    assert result.comparison.event.status is ComparisonStatus.CONSISTENT
    assert result.comparison.location.status is ComparisonStatus.MISMATCH
    assert result.comparison.date.status is ComparisonStatus.MISMATCH
    assert result.source_context is not None
    assert result.source_context.location == "Bangkok"
    assert result.source_context.date == "2022-10-03"
    assert "location_changed" in result.manipulation_types
    assert "old_footage_reused" in result.manipulation_types
    assert result.sources, "case A must present the matched source"
    assert_evidence_invariants(result)


async def test_case_b_golden_false_time_only(settings: Any, video: Path):
    ver_id = new_verification_id()
    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_b(ver_id), settings=settings
    )

    assert result.classification is ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert result.comparison.event.status is ComparisonStatus.CONSISTENT
    assert result.comparison.location.status is ComparisonStatus.CONSISTENT
    assert result.comparison.date.status is ComparisonStatus.MISMATCH
    assert_evidence_invariants(result)


async def test_case_c_golden_matching_context(settings: Any, video: Path):
    ver_id = new_verification_id()
    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_c(ver_id), settings=settings
    )

    assert result.classification is ResultClassification.CONTEXT_CONSISTENT_WITH_SOURCE
    assert result.comparison.event.status is ComparisonStatus.CONSISTENT
    assert result.comparison.location.status is ComparisonStatus.CONSISTENT
    assert result.comparison.date.status is ComparisonStatus.CONSISTENT
    assert_evidence_invariants(result)


async def test_case_d_golden_insufficient_evidence_never_false_context(settings: Any, video: Path):
    ver_id = new_verification_id()
    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_d(ver_id), settings=settings
    )

    assert result.classification is ResultClassification.INSUFFICIENT_EVIDENCE
    assert result.classification is not ResultClassification.POSSIBLE_FALSE_CONTEXT
    assert result.source_context is None
    assert result.visual_match == "unknown"
    assert result.sources == []
    assert result.strongest_evidence_ids == []
    assert not any("web_research incomplete" in note for note in result.unresolved), (
        "a normal no-result web branch must not carry the failure marker"
    )
    for dim in (result.comparison.event, result.comparison.location, result.comparison.date):
        assert dim.status is ComparisonStatus.UNKNOWN
    assert_evidence_invariants(result)


# --- state tracking (HANDOFF §22.2, §23) ---


async def test_state_tracks_stages_progress_and_completed(monkeypatch: pytest.MonkeyPatch, settings: Any, video: Path):
    ver_id = new_verification_id()
    recording = RecordingStore()
    monkeypatch.setattr(state_module, "store", recording)

    recording.create(ver_id)
    initial = recording.get(ver_id)
    assert initial is not None
    assert initial.status == "processing"
    assert initial.stage == "queued"
    assert initial.progress == 0.0

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_a(ver_id), settings=settings
    )

    stages = [update["stage"] for update in recording.updates if "stage" in update]
    assert stages == [
        "preprocessing",
        "extracting_context",
        "planning_investigation",
        "fact_check_search",
        "web_research",
        "visual_source_search",
        "synthesizing_evidence",
        "comparing_context",
        "completed",
    ]
    progress = [
        update["progress"] for update in recording.updates if "progress" in update
    ]
    assert progress == sorted(progress) and len(set(progress)) == len(progress), "progress must be strictly increasing"

    final = recording.get(ver_id)
    assert final is not None
    assert final.status == "completed"
    assert final.stage == "completed"
    assert final.progress == 1.0
    assert final.error is None
    assert final.result is result

    # T36 debug payloads: plan/bundle persisted as JSON-safe dumps keyed by id
    assert final.plan is not None and final.plan["verification_id"] == ver_id
    assert final.bundle is not None and final.bundle["verification_id"] == ver_id


# --- error matrix (HANDOFF §26) ---


async def test_no_audio_continues_with_empty_speech_evidence(settings: Any, media: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.speech = FakeSpeechProvider([SpeechExtraction()])  # no-audio artifact shape
    result = await pipeline.run_verification(
        ver_id,
        pipeline.VerificationRequest(video_path=make_video_no_audio(media)),
        providers,
        settings=settings,
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.transcript == ""
    assert all(atom.type is not EvidenceType.SPEECH for atom in result.current_context.evidence)
    assert_evidence_invariants(result)


async def test_video_too_long_rejected_with_failed_status(settings: Any, media: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)

    with pytest.raises(PreprocessingError) as excinfo:
        await pipeline.run_verification(
            ver_id,
            pipeline.VerificationRequest(video_path=make_video_20s(media)),
            providers,
            settings=settings,
        )
    assert excinfo.value.code == "video_too_long"

    failed = state_module.store.get(ver_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.stage == "failed"
    assert failed.progress == 1.0
    assert failed.error is not None and "video_too_long" in failed.error
    assert failed.result is None


async def test_whisper_failure_continues_with_empty_speech_evidence(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.speech = FakeSpeechProvider([RuntimeError("whisper down")])

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.transcript == ""
    assert all(atom.type is not EvidenceType.SPEECH for atom in result.current_context.evidence)
    assert any("speech" in note.lower() for note in result.unresolved), "speech gap must be surfaced"
    assert_evidence_invariants(result)


async def test_ocr_failure_continues_with_empty_ocr(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.ocr = FakeOCRExtractor([RuntimeError("paddle down")])

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.ocr_texts == []
    assert any("ocr" in note.lower() for note in result.unresolved)
    assert_evidence_invariants(result)


async def test_fact_check_failure_records_branch_error_but_builds_result(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.fact_check = ScriptedFactCheckRunner([RuntimeError("fact check unavailable")])
    # the failed branch contributed no evidence: cite only the surviving ids
    providers.luna = _luna_a(
        ver_id,
        _synthesis_raw(
            "supported",
            "The footage matches Bangkok flooding from October 2022.",
            best_source_id=_source_id("https://example.com/article/bangkok-flood"),
            supporting=["web_a_01"],
        ),
    )

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.sources, "web + visual branches still produce sources"
    assert any("fact check" in note.lower() for note in result.unresolved), "branch error must be recorded"
    assert_evidence_invariants(result)


async def test_investigator_timeout_marks_web_branch_incomplete(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.web_research = FakeWebResearchProvider([RuntimeError("investigator timed out")])
    # the timed-out branch contributed no evidence: cite only the surviving ids
    providers.luna = _luna_a(
        ver_id,
        _synthesis_raw(
            "supported",
            "The fact-check review describes the footage as old material.",
            best_source_id=_source_id("https://example.com/article/bangkok-flood"),
            supporting=["fc_a_01"],
        ),
    )

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert any("web research" in note.lower() for note in result.unresolved), "incomplete web branch must be surfaced"
    assert any("web_research incomplete" in note for note in result.unresolved), (
        "failed/timed-out web research must carry the explicit incomplete marker"
    )
    assert all(source.origin != "web_research" for source in result.sources), "no invented web sources"
    assert_evidence_invariants(result)


@pytest.mark.parametrize(
    ("match_types", "strength_marker"),
    [
        (["visually_similar", "hash:average_hamming"], None),  # weak tier stays weak
        (["full_image_match", "hash:average_hamming", "hash_distance:2"], "high"),
    ],
)
async def test_empty_vision_with_demo_index_yields_demo_candidate(
    match_types: list[str], strength_marker: str | None, settings: Any, video: Path
):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.vision = FakeVisionProvider([[]])  # provider healthy, zero matches
    demo_url = "https://example.com/demo/bangkok-flood"
    providers.demo_index = ScriptedDemoIndex(
        [
            [
                SourceCandidate(
                    source_id="src_bangkok_flood_2022",
                    url=demo_url,
                    canonical_url=canonicalize(demo_url),
                    publisher="Demo News Source",
                    title="Flooding in Bangkok",
                    published_at="2022-10-03",
                    event="flood",
                    location="Bangkok",
                    time_context="2022-10-03",
                    matched_frame_ids=[f"{ver_id}_kf000"],
                    match_types=match_types,
                    earliest_known_date="2022-10-03",
                    origin="demo_index",
                )
            ]
        ]
    ).search

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    demo = [source for source in result.sources if source.url == demo_url]
    assert demo, "demo candidate must enter the result when vision is empty"
    assert any("demo_index" in match_type for match_type in demo[0].match_types)
    if strength_marker is not None:
        assert strength_marker in demo[0].match_types  # D1: match strength survives normalization
    assert_evidence_invariants(result)


# --- heavy-provider process isolation (T35 seam) ---


async def test_isolated_speech_runs_in_child_process(tmp_path: Path):
    pid_file = str(tmp_path / "child.pid")
    providers = _providers(
        speech=_PidWritingSpeech(pid_file),
        ocr=FakeOCRExtractor([[]]),
        luna=ScriptedLuna([]),
        vision=FakeVisionProvider([[]]),
        web=FakeWebResearchProvider([]),
        isolated=True,
    )

    speech = await pipeline._extract_speech(providers, "/tmp/irrelevant.wav")

    assert speech.transcript == "spoken in a child process"
    child_pid = int(Path(pid_file).read_text())
    assert child_pid != os.getpid(), "transcription must not run in the main process"


async def test_isolated_speech_child_failure_is_an_evidence_gap(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.speech = _FailingSpeech()
    providers.isolated = True

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.transcript == ""
    assert any("speech" in note.lower() for note in result.unresolved)
    assert_evidence_invariants(result)


async def test_isolated_speech_child_timeout_is_an_evidence_gap(
    monkeypatch: pytest.MonkeyPatch, settings: Any, video: Path
):
    monkeypatch.setattr(pipeline, "SPEECH_TIMEOUT_SEC", 0.2)
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.speech = _SleepingSpeech()
    providers.isolated = True

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.transcript == ""
    assert any("speech" in note.lower() for note in result.unresolved)
    assert_evidence_invariants(result)


async def test_isolated_ocr_child_failure_is_an_evidence_gap(settings: Any, video: Path):
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.ocr = _FailingOcr()
    providers.isolated = True

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert isinstance(result, VerificationResult)
    assert result.current_context.ocr_texts == []
    assert any("ocr" in note.lower() for note in result.unresolved)
    assert_evidence_invariants(result)
