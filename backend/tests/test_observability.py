"""T37: structured per-stage observability tests (TDD red-green).

Pins the public ``log_event`` seam (required fields, numeric non-negative
latency, deterministic redaction of sensitive keys and secret-shaped text,
clamped latency), the contextvar ``verification_scope`` injection, and the
integrations: every §22.2 pipeline stage emits an event with required fields
on success and failure (incl. degraded extraction providers), the T32
orchestrator emits one event per branch with counts and branch-status failure
visibility, and ``selected_source_id`` appears once selection exists (or the
explicit ``none`` marker). Sensitive values are asserted absent from log text
end-to-end.
"""

import json
import logging
from pathlib import Path

import pytest

from backend.services import pipeline
from backend.services.ingestion.video_ingestor import new_verification_id
from backend.services.preprocessing.ffmpeg import PreprocessingError
from backend.services.validation.orchestrator import execute
from backend.tests.fixtures.providers_fakes import FakeSpeechProvider
from backend.tests.fixtures.video_factory import make_video, make_video_20s, require_ffmpeg
from backend.tests.test_orchestrator import (
    ScriptedDemoIndex,
    ScriptedFactRunner,
    ScriptedVisualRunner,
    ScriptedWebRunner,
    _context,
    _fact_evidence,
    _plan,
    _visual_candidate,
    _web_result,
)
from backend.tests.test_pipeline import (
    ScriptedLuna,
    _case_a,
    _case_d,
    _claim,
    _fusion_raw,
)
from backend.utils.observability import log_event, verification_scope

OBS_LOGGER = "backend.observability"


def _events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(record.getMessage()) for record in caplog.records if record.name == OBS_LOGGER
    ]


def _by_stage(events: list[dict], stage: str) -> list[dict]:
    return [event for event in events if event["stage"] == stage]


# --- fixtures (test_pipeline pattern: real ffmpeg media, scripted providers) ---


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    require_ffmpeg()
    return make_video(tmp_path_factory.mktemp("media"))


@pytest.fixture()
def media(tmp_path: Path) -> Path:
    require_ffmpeg()
    return tmp_path


@pytest.fixture()
def settings(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Isolated WORKDIR; default settings otherwise (test_pipeline pattern)."""
    monkeypatch.delenv("WORKDIR", raising=False)
    from backend.config import Settings

    return Settings(workdir=str(tmp_path / "work"))


# --- log_event contract ---


def test_log_event_emits_required_fields(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    log_event("ver_1", "preprocessing", "ffmpeg", 12.5, "success", keyframes=3)

    event = _events(caplog)[0]
    assert event["verification_id"] == "ver_1"
    assert event["stage"] == "preprocessing"
    assert event["provider"] == "ffmpeg"
    assert isinstance(event["latency_ms"], (int, float))
    assert event["latency_ms"] >= 0
    assert event["status"] == "success"
    assert event["keyframes"] == 3


def test_log_event_clamps_negative_latency(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    log_event("ver_1", "planning_investigation", "luna", -4.2, "error")

    assert _events(caplog)[0]["latency_ms"] == 0


def test_log_event_redacts_sensitive_extras(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    log_event(
        "ver_1",
        "failed",
        "pipeline",
        1.0,
        "error",
        api_key="sk-super-secret-1234567890",
        password="hunter2-secret",
        token="tok3n-secret",
        secret="s3cr3t-value",
        authorization="Bearer abc.def.ghi.jkl",
        error="plain provider message",
    )

    text = caplog.text
    for secret in (
        "sk-super-secret-1234567890",
        "hunter2-secret",
        "tok3n-secret",
        "s3cr3t-value",
        "abc.def.ghi.jkl",
    ):
        assert secret not in text
    assert "plain provider message" in text  # non-sensitive text survives
    assert "<redacted>" in text


def test_log_event_redacts_secret_shaped_text(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    log_event(
        "ver_1",
        "failed",
        "pipeline",
        1.0,
        "error",
        error="provider died: sk-liveKEY1234567890, Authorization: Bearer xyz.abc123",
    )

    text = caplog.text
    assert "sk-liveKEY1234567890" not in text
    assert "xyz.abc123" not in text
    assert "provider died" in text  # surrounding message survives


def test_log_event_redacts_key_value_secrets_in_text(caplog: pytest.LogCaptureFixture):
    """F37-1: key=value secrets inside untrusted text (e.g. error strings) never leak."""
    caplog.set_level(logging.INFO)
    log_event(
        "ver_1",
        "failed",
        "pipeline",
        1.0,
        "error",
        error="provider rejected: password=hunter2 and api_key=plain-secret and Authorization: abcdef",
    )

    text = caplog.text
    for secret in ("hunter2", "plain-secret", "abcdef"):
        assert secret not in text
    assert "provider rejected" in text  # surrounding text survives
    assert "<redacted>" in text


def test_log_event_redacts_query_style_values(caplog: pytest.LogCaptureFixture):
    """F37-1: URL query secrets (?token=...&api_key=...) never leak."""
    caplog.set_level(logging.INFO)
    log_event(
        "ver_1",
        "failed",
        "pipeline",
        1.0,
        "error",
        error="fetch failed for https://example.com/v1/verify?token=QUERYTOKEN123&api_key=SECRETKEY456",
    )

    text = caplog.text
    assert "QUERYTOKEN123" not in text
    assert "SECRETKEY456" not in text
    assert "https://example.com/v1/verify" in text  # safe URL prefix survives


def test_log_event_redacts_key_value_secrets_case_insensitively(caplog: pytest.LogCaptureFixture):
    """F37-1: case-insensitive keys and _/-joined compound keys are covered."""
    caplog.set_level(logging.INFO)
    log_event(
        "ver_1",
        "failed",
        "pipeline",
        1.0,
        "error",
        error="PASSWORD=hunter2, Api-Key: mixedcase123, auth_token=ABC123XYZ, access_secret=DEF456UVW",
    )

    text = caplog.text
    for secret in ("hunter2", "mixedcase123", "ABC123XYZ", "DEF456UVW"):
        assert secret not in text
    assert "<redacted>" in text


def test_verification_scope_injects_id_into_plain_records(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("backend.services.pipeline")
    logger.info("outside scope")

    with verification_scope("ver_ctx"):
        logger.info("inside scope")

    in_scope = [record for record in caplog.records if record.getMessage() == "inside scope"]
    outside = [record for record in caplog.records if record.getMessage() == "outside scope"]
    assert in_scope and getattr(in_scope[0], "verification_id") == "ver_ctx"
    assert outside and not hasattr(outside[0], "verification_id")


# --- pipeline stage events (T35) ---


async def test_pipeline_emits_required_fields_for_every_stage(caplog, settings, video):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_a(ver_id), settings=settings
    )

    events = _events(caplog)
    assert {
        "preprocessing",
        "extracting_context",
        "planning_investigation",
        "fact_check_search",
        "web_research",
        "visual_source_search",
        "synthesizing_evidence",
        "comparing_context",
        "completed",
    } <= {event["stage"] for event in events}
    for event in events:
        assert event["verification_id"] == ver_id
        assert event["stage"] and event["provider"] and event["status"]
        assert isinstance(event["latency_ms"], (int, float)) and event["latency_ms"] >= 0

    extractors = {event["provider"] for event in _by_stage(events, "extracting_context")}
    assert {"speech", "ocr", "visual"} <= extractors


async def test_pipeline_events_carry_orchestrator_counts_and_selection(caplog, settings, video):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_a(ver_id), settings=settings
    )

    events = _events(caplog)
    web = _by_stage(events, "web_research")[0]
    assert web["searches"] >= 1 and web["pages_fetched"] >= 1
    visual = _by_stage(events, "visual_source_search")[0]
    assert visual["candidates"] >= 1
    synthesis = _by_stage(events, "synthesizing_evidence")[0]
    assert synthesis["selected_source_id"].startswith("src_")


async def test_pipeline_no_selection_emits_none_marker(caplog, settings, video):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), _case_d(ver_id), settings=settings
    )

    synthesis = _by_stage(_events(caplog), "synthesizing_evidence")[0]
    assert synthesis["selected_source_id"] == "none"


async def test_pipeline_preprocessing_failure_logged(caplog, settings, media):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    with pytest.raises(PreprocessingError):
        await pipeline.run_verification(
            ver_id,
            pipeline.VerificationRequest(video_path=make_video_20s(media)),
            _case_a(ver_id),
            settings=settings,
        )

    failed = _by_stage(_events(caplog), "failed")[0]
    assert failed["status"] == "error"
    assert failed["error_stage"] == "preprocessing"
    assert failed["error_code"] == "video_too_long"


async def test_pipeline_core_failure_logs_sanitized_error(caplog, settings, video):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.luna = ScriptedLuna(
        [
            _fusion_raw(
                _claim("flood", ["speech_01"], 0.96, normalized="flood"),
                _claim("Jakarta", ["speech_01"], 0.92, normalized="Jakarta, Indonesia"),
                _claim("today", ["speech_01"], 0.87),
                entities=["Jakarta"],
                keywords=["flood", "banjir"],
            ),
            RuntimeError("luna plan call failed sk-ACCOUNTKEY12345678"),
        ]
    )

    with pytest.raises(RuntimeError):
        await pipeline.run_verification(
            ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
        )

    failed = _by_stage(_events(caplog), "failed")[0]
    assert failed["status"] == "error"
    assert failed["error_stage"] == "planning_investigation"
    assert "sk-ACCOUNTKEY12345678" not in caplog.text
    assert "luna plan call failed" in caplog.text  # sanitized message survives


async def test_pipeline_degraded_extraction_logs_error_and_continues(caplog, settings, video):
    caplog.set_level(logging.INFO)
    ver_id = new_verification_id()
    providers = _case_a(ver_id)
    providers.speech = FakeSpeechProvider(
        [RuntimeError("whisper backend down sk-ACCOUNTKEY12345678")]
    )

    result = await pipeline.run_verification(
        ver_id, pipeline.VerificationRequest(video_path=video), providers, settings=settings
    )

    assert result.verification_id == ver_id  # degraded extraction must not fail the pipeline
    speech = [
        event
        for event in _events(caplog)
        if event["stage"] == "extracting_context" and event["provider"] == "speech"
    ]
    assert speech and speech[0]["status"] == "error"
    assert _by_stage(_events(caplog), "completed"), "pipeline must still reach completed"
    assert "sk-ACCOUNTKEY12345678" not in caplog.text


# --- orchestrator branch events (T32) ---


async def _run_orchestrator(fact_script, web_script, visual_script, demo_script, plan=None):
    fact = ScriptedFactRunner(fact_script)
    web = ScriptedWebRunner(web_script)
    visual = ScriptedVisualRunner(visual_script)
    demo = ScriptedDemoIndex(demo_script)
    return await execute(
        _context(),
        plan or _plan(),
        run_fact_check=fact,
        investigate=web,
        run_visual=visual,
        demo_index=demo.search,
    )


async def test_orchestrator_emits_branch_events_with_counts(caplog):
    caplog.set_level(logging.INFO)
    await _run_orchestrator(
        fact_script=[[_fact_evidence(), _fact_evidence()]],
        web_script=[_web_result("web_00")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
    )

    fact = _by_stage(_events(caplog), "fact_check_search")[0]
    assert fact["status"] == "success" and fact["evidence"] == 2 and fact["tasks"] == 1
    web = _by_stage(_events(caplog), "web_research")[0]
    assert web["status"] == "success"
    assert web["searches"] >= 1 and web["pages_fetched"] >= 1 and web["tasks"] == 1
    visual = _by_stage(_events(caplog), "visual_source_search")[0]
    assert visual["status"] == "success" and visual["candidates"] == 1 and visual["tasks"] == 1
    for event in (fact, web, visual):
        assert event["verification_id"] == "ver_123"
        assert isinstance(event["latency_ms"], (int, float)) and event["latency_ms"] >= 0


async def test_orchestrator_branch_failures_visible(caplog):
    caplog.set_level(logging.INFO)
    await _run_orchestrator(
        fact_script=[RuntimeError("fact api down"), [_fact_evidence("fc_ev_2")]],
        web_script=[RuntimeError("investigator timed out"), _web_result("web_01")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
        plan=_plan(fc=2, web=2),
    )

    fact = _by_stage(_events(caplog), "fact_check_search")[0]
    assert fact["status"] == "partial_failure"
    assert fact["tasks"] == 2 and fact["evidence"] == 1
    web = _by_stage(_events(caplog), "web_research")[0]
    assert web["status"] == "partial_failure"
    assert web["searches"] >= 1  # surviving sibling's counts still reported


async def test_orchestrator_full_branch_failure_visible(caplog):
    caplog.set_level(logging.INFO)
    await _run_orchestrator(
        fact_script=[RuntimeError("fact api down")],
        web_script=[_web_result("web_00")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
        plan=_plan(fc=1),
    )

    fact = _by_stage(_events(caplog), "fact_check_search")[0]
    assert fact["status"] == "error"
    assert fact["tasks"] == 1 and fact["evidence"] == 0
