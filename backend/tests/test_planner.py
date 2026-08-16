"""T31: investigation planner tests (TDD red-green).

Fake Luna only — no network, no credentials. Covers: task/query caps from
``Settings`` (5 web tasks x 20 queries -> 3 tasks x <= 4 queries) with explicit
"Bounded investigation" notes appended to ``stop_conditions``; verification_id
always from the input ``VideoContext`` (never the model's); Indonesian claims
gain missing id/en query variants and fact-check ``language_codes`` without
duplicating variants the model already produced; English-only claims are left
untouched; per-task schema budgets are clamped while other task fields are
preserved; ``InvestigationPlan`` has no verdict/unresolved field and a
model-emitted verdict is rejected by the strict schema with exactly one repair
(never a loop); ``stop_conditions`` always populated; and the static prompt
asset (``prompts/planner.txt``) carries no truth/verdict/browsing bias.
"""

import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from backend.config import Settings
from backend.schemas.context import VideoContext
from backend.schemas.evidence import (
    ContextClaim,
    EvidenceAtom,
    EvidenceType,
    KeyframeRef,
)
from backend.schemas.investigation import (
    FactCheckTask,
    InvestigationPlan,
    VisualSearchTask,
    WebResearchTask,
)
from backend.services.validation.planner import create_plan, render_planner_prompt
from backend.tests.fixtures.providers_fakes import FakeLunaProvider
from backend.utils.llm import StructuredOutputError

T = TypeVar("T", bound=BaseModel)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "planner.txt"


def _claim(value: str, normalized: str | None = None) -> ContextClaim:
    return ContextClaim(
        value=value,
        normalized_value=normalized,
        confidence=0.9,
        evidence_ids=["speech_01"],
        explicitly_claimed=True,
    )


def _id_context() -> VideoContext:
    """Indonesian claim context (Jakarta flood), mirroring the T23 example."""
    transcript = "banjir Jakarta hari ini"
    return VideoContext(
        verification_id="ver_123",
        event=_claim("Banjir besar melanda Jakarta"),
        location=_claim("Jakarta"),
        time=_claim("hari ini", "2026-08-15"),
        entities=["Jakarta"],
        keywords=["flood", "banjir"],
        transcript=transcript,
        visual_summary="Urban flooding scene",
        visual_location_clues=["Jakarta"],
        evidence=[
            EvidenceAtom(
                evidence_id="speech_01", type=EvidenceType.SPEECH, value=transcript
            )
        ],
        keyframes=[
            KeyframeRef(frame_id="kf_01", timestamp_sec=0.0, local_path="/tmp/kf_01.png")
        ],
    )


def _en_context() -> VideoContext:
    """English-only context: no Indonesian tokens anywhere."""
    return _id_context().model_copy(
        update={
            "event": _claim("flood"),
            "location": _claim("Jakarta"),
            "time": _claim("2026-08-15"),
            "transcript": "Jakarta flood today",
            "keywords": ["flood"],
        }
    )


def _plan_json(
    fc_tasks: list[dict] | None = None,
    web_tasks: list[dict] | None = None,
    vis_tasks: list[dict] | None = None,
    stop: list[str] | None = None,
) -> str:
    plan = InvestigationPlan(
        verification_id="luna_ver",
        fact_check_tasks=[
            FactCheckTask(**task) for task in fc_tasks
        ] if fc_tasks else [
            FactCheckTask(
                task_id="fc_01",
                queries=["banjir Jakarta video"],
                language_codes=["id"],
                goal="Find existing fact-checks discussing this claim or reused footage",
            )
        ],
        web_research_tasks=[
            WebResearchTask(**task) for task in web_tasks
        ] if web_tasks else [
            WebResearchTask(
                task_id="web_01",
                question="Did flooding occur in Jakarta on 2026-08-15?",
                queries=["banjir Jakarta 15 Agustus 2026"],
                preferred_source_types=["government", "reputable_news"],
            )
        ],
        visual_search_tasks=[
            VisualSearchTask(**task) for task in vis_tasks
        ] if vis_tasks else [
            VisualSearchTask(
                task_id="vis_01",
                frame_ids=["kf_01"],
                goal="Find earlier appearances of the same or cropped footage",
            )
        ],
        investigation_questions=[
            "Did significant flooding occur in Jakarta on 2026-08-15?"
        ],
        stop_conditions=stop
        if stop is not None
        else ["At least two reputable textual sources answer the event question"],
    )
    return plan.model_dump_json()


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


def _default_settings(monkeypatch) -> Settings:
    """Hermetic Settings() with the planner env keys cleared."""
    monkeypatch.delenv("MAX_WEB_RESEARCH_TASKS", raising=False)
    monkeypatch.delenv("MAX_QUERIES_PER_TASK", raising=False)
    return Settings()


def _strip_verdict(raw: str, error: ValidationError) -> str:
    data = json.loads(raw)
    data.pop("verdict", None)
    return json.dumps(data)


# --- bounded output ---


async def test_caps_web_tasks_and_queries_and_notes_truncation(monkeypatch):
    web_tasks = [
        {
            "task_id": f"web_{i:02d}",
            "question": f"Question {i}?",
            "queries": [f"banjir Jakarta query {j}" for j in range(20)],
            "preferred_source_types": ["news"],
        }
        for i in range(5)
    ]
    provider = _RecordingLuna([_plan_json(web_tasks=web_tasks)])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.verification_id == "ver_123"  # from the input context, not the model
    assert len(plan.fact_check_tasks) == 1
    assert len(plan.web_research_tasks) == 3  # MAX_WEB_RESEARCH_TASKS
    assert len(plan.visual_search_tasks) == 1
    for task in plan.fact_check_tasks + plan.web_research_tasks:
        assert len(task.queries) <= 4  # MAX_QUERIES_PER_TASK
    assert plan.stop_conditions
    assert any(
        "Bounded investigation" in note and "truncated" in note
        for note in plan.stop_conditions
    )
    assert provider.calls[0][2] is None  # text-only planner call: no keyframe images


async def test_caps_fact_check_tasks(monkeypatch):
    fc_tasks = [
        {"task_id": "fc_01", "queries": ["q1"], "language_codes": ["id"], "goal": "g1"},
        {"task_id": "fc_02", "queries": ["q2"], "language_codes": ["id"], "goal": "g2"},
    ]
    provider = _RecordingLuna([_plan_json(fc_tasks=fc_tasks)])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert len(plan.fact_check_tasks) == 1
    assert plan.fact_check_tasks[0].task_id == "fc_01"  # first task wins


async def test_caps_visual_search_tasks(monkeypatch):
    vis_tasks = [
        {"task_id": "vis_01", "frame_ids": ["kf_01"], "goal": "g1"},
        {"task_id": "vis_02", "frame_ids": ["kf_02"], "goal": "g2"},
    ]
    provider = _RecordingLuna([_plan_json(vis_tasks=vis_tasks)])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert len(plan.visual_search_tasks) == 1
    assert plan.visual_search_tasks[0].task_id == "vis_01"


async def test_bounds_come_from_settings_not_hardcoded(monkeypatch):
    monkeypatch.setenv("MAX_WEB_RESEARCH_TASKS", "2")
    monkeypatch.setenv("MAX_QUERIES_PER_TASK", "2")
    web_tasks = [
        {
            "task_id": f"web_{i:02d}",
            "question": f"Question {i}?",
            "queries": [f"banjir query {j}" for j in range(5)],
            "preferred_source_types": ["news"],
        }
        for i in range(4)
    ]
    provider = _RecordingLuna([_plan_json(web_tasks=web_tasks)])

    plan = await create_plan(_id_context(), provider, settings=Settings())

    assert len(plan.web_research_tasks) == 2
    assert all(len(t.queries) <= 2 for t in plan.web_research_tasks)


# --- id+en variants for Indonesian claims ---


async def test_indonesian_claims_get_id_en_variants(monkeypatch):
    web_tasks = [
        {
            "task_id": "web_01",
            "question": "Did flooding occur in Jakarta on 2026-08-15?",
            "queries": ["banjir Jakarta 15 Agustus 2026"],
            "preferred_source_types": ["news"],
        }
    ]
    provider = _RecordingLuna([_plan_json(web_tasks=web_tasks)])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    fc = plan.fact_check_tasks[0]
    web = plan.web_research_tasks[0]
    assert fc.language_codes == ["id", "en"]  # missing "en" appended deterministically
    assert "banjir Jakarta 15 Agustus 2026" in web.queries  # model id variant kept
    assert "Jakarta flood" in fc.queries  # deterministic en variant (entities+keywords)
    assert "Jakarta flood" in web.queries


async def test_existing_id_en_variants_not_duplicated(monkeypatch):
    web_queries = ["Jakarta flood August 2026", "banjir Jakarta Agustus 2026"]
    fc_tasks = [
        {
            "task_id": "fc_01",
            "queries": ["Jakarta flood viral", "banjir Jakarta video"],
            "language_codes": ["id", "en"],
            "goal": "Find existing fact-checks",
        }
    ]
    web_tasks = [
        {
            "task_id": "web_01",
            "question": "Did flooding occur in Jakarta on 2026-08-15?",
            "queries": web_queries,
            "preferred_source_types": ["news"],
        }
    ]
    provider = _RecordingLuna([_plan_json(fc_tasks=fc_tasks, web_tasks=web_tasks)])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.fact_check_tasks[0].language_codes == ["id", "en"]  # already present
    assert plan.fact_check_tasks[0].queries == ["Jakarta flood viral", "banjir Jakarta video"]
    assert plan.web_research_tasks[0].queries == web_queries


async def test_non_indonesian_claims_leave_queries_and_codes_untouched(monkeypatch):
    fc_tasks = [
        {
            "task_id": "fc_01",
            "queries": ["Jakarta flood video"],
            "language_codes": ["en"],
            "goal": "Find existing fact-checks",
        }
    ]
    web_tasks = [
        {
            "task_id": "web_01",
            "question": "Did flooding occur in Jakarta?",
            "queries": ["Jakarta flood 2026"],
            "preferred_source_types": ["news"],
        }
    ]
    provider = _RecordingLuna([_plan_json(fc_tasks=fc_tasks, web_tasks=web_tasks)])

    plan = await create_plan(_en_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.fact_check_tasks[0].language_codes == ["en"]
    assert plan.fact_check_tasks[0].queries == ["Jakarta flood video"]
    assert plan.web_research_tasks[0].queries == ["Jakarta flood 2026"]


# --- no verdict field / no bias ---


def test_investigation_plan_schema_has_no_verdict_or_unresolved_field():
    fields = InvestigationPlan.model_fields
    assert "verdict" not in fields
    assert "final_label" not in fields
    assert "unresolved" not in fields  # plan carries notes in stop_conditions only


async def test_model_verdict_field_rejected_and_repaired_once(monkeypatch):
    with_verdict = json.loads(_plan_json())
    with_verdict["verdict"] = "fake"
    provider = FakeLunaProvider([json.dumps(with_verdict)], repair_fn=_strip_verdict)

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert not hasattr(plan, "verdict")
    assert plan.verification_id == "ver_123"


async def test_model_verdict_field_without_repair_raises(monkeypatch):
    with_verdict = json.loads(_plan_json())
    with_verdict["verdict"] = "fake"
    provider = FakeLunaProvider([json.dumps(with_verdict)])

    with pytest.raises(StructuredOutputError):
        await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))


# --- one-repair semantics, never a loop ---


async def test_invalid_schema_repairs_exactly_once(monkeypatch):
    provider = FakeLunaProvider(
        ["not json at all", "still not json"], repair_fn=lambda raw, err: _plan_json()
    )

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.verification_id == "ver_123"
    assert provider._script == ["still not json"]  # initial + one repair, then stop


async def test_invalid_schema_twice_raises_without_loop(monkeypatch):
    provider = FakeLunaProvider(
        ["not json at all", "still not json"], repair_fn=lambda raw, err: "still not json"
    )

    with pytest.raises(StructuredOutputError):
        await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert provider._script == ["still not json"]  # exactly one repair attempted, then raise


# --- stop conditions ---


async def test_stop_conditions_always_populated_when_model_returns_empty(monkeypatch):
    provider = _RecordingLuna([_plan_json(stop=[])])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.stop_conditions  # deterministic default added


async def test_truncation_notes_appended_when_stop_conditions_empty(monkeypatch):
    web_tasks = [
        {
            "task_id": "web_01",
            "question": "Q?",
            "queries": [f"banjir q{j}" for j in range(9)],
            "preferred_source_types": ["news"],
        }
    ]
    provider = _RecordingLuna([_plan_json(web_tasks=web_tasks, stop=[])])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    assert plan.stop_conditions
    assert any("Bounded investigation" in note for note in plan.stop_conditions)


# --- per-task schema bounds preserved/enforced ---


async def test_web_budgets_and_visual_candidates_clamped_to_schema_bounds(monkeypatch):
    plan_obj = InvestigationPlan(
        verification_id="luna_ver",
        fact_check_tasks=[
            FactCheckTask(task_id="fc_01", queries=["q"], language_codes=["id"], goal="g")
        ],
        web_research_tasks=[
            WebResearchTask(
                task_id="web_01",
                question="Q?",
                queries=["banjir Jakarta"],
                preferred_source_types=["news"],
                max_searches=99,
                max_pages=0,
            )
        ],
        visual_search_tasks=[
            VisualSearchTask(
                task_id="vis_01",
                frame_ids=["kf_01"],
                goal="g",
                max_candidates_per_frame=999,
            )
        ],
        investigation_questions=["Q?"],
        stop_conditions=["stop"],
    )
    provider = _RecordingLuna([plan_obj.model_dump_json()])

    plan = await create_plan(_id_context(), provider, settings=_default_settings(monkeypatch))

    web = plan.web_research_tasks[0]
    assert web.max_searches == 2  # clamped to schema default
    assert web.max_pages == 1  # clamped to schema default, never below 1
    assert web.task_id == "web_01"  # valid structured fields preserved
    assert web.preferred_source_types == ["news"]
    assert web.question == "Q?"
    assert plan.visual_search_tasks[0].max_candidates_per_frame == 10
    assert plan.fact_check_tasks[0].goal == "g"


# --- prompt rendering and static asset ---


def test_render_prompt_includes_context_and_config_bounds(monkeypatch):
    prompt = render_planner_prompt(_id_context(), _default_settings(monkeypatch))

    assert "ver_123" in prompt
    assert "Banjir besar melanda Jakarta" in prompt
    assert "banjir Jakarta hari ini" in prompt  # transcript
    assert "kf_01" in prompt
    assert "Visual observations: none" in prompt or "Visual observations:" in prompt
    assert "On-screen OCR text:" in prompt
    assert "(max 3 tasks)" in prompt  # MAX_WEB_RESEARCH_TASKS rendered
    assert "At most 2 queries per task." in prompt  # MAX_QUERIES_PER_TASK rendered


def test_planner_prompt_has_no_truth_or_verdict_bias():
    text = PROMPT_PATH.read_text()

    assert "You DO NOT determine truth" in text
    assert "You DO NOT browse the web or fetch any content yourself" in text
    assert "You DO NOT choose or select a final source" in text
    assert (
        "You DO NOT label the video, the claim, or any source as fake, hoax, or misinformation"
        in text
    )
    assert "Never emit a verdict" in text
    assert "Return JSON only" in text
    assert "stop_conditions" in text
