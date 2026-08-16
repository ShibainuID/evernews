"""T32: parallel validation orchestrator tests (TDD red-green).

Fake branch runners only — no network, no providers, no credentials. Pins:
nested ``asyncio.gather(return_exceptions=True)`` so the three branch groups
(fact check / web research / visual search) run concurrently and every task
inside a group is bounded by its own ``asyncio.wait_for`` budget; the timeout
constants are module-level and shorten-able via monkeypatch; every branch
failure is normalized into ``bundle.errors`` + ``branch_status`` (never an
exception); partial evidence from successful siblings is preserved; a
failed/timed-out web task becomes an explicit ``status="insufficient"``
``WebResearchResult``; the max-3 web task cap is defended even against a
5-task plan; the visual demo-index fallback (T27) is invoked with the
context's keyframe local paths and its candidates enter the bundle marked
``raw_provider_type="demo_index"``; a successful zero-match without demo
candidates stays a valid no-match (no verdict anywhere); and the three branch
groups demonstrably run in parallel.
"""

import asyncio
from typing import Literal

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ContextClaim, KeyframeRef
from backend.schemas.investigation import (
    FactCheckEvidence,
    FactCheckTask,
    InvestigationPlan,
    VisualSearchTask,
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)
from backend.schemas.result import SourceCandidate
from backend.services.validation import orchestrator
from backend.services.validation.orchestrator import execute


# --- scripted branch fakes (T32 seams) ---


class ScriptedFactRunner:
    """Scripted ``list[FactCheckEvidence]`` or ``Exception`` per task; records task ids."""

    def __init__(self, script):
        self._script = list(script)
        self.task_ids: list[str] = []

    async def __call__(self, task: FactCheckTask) -> list[FactCheckEvidence]:
        self.task_ids.append(task.task_id)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedWebRunner:
    """Scripted ``WebResearchResult`` or ``Exception`` per task; records task ids."""

    def __init__(self, script):
        self._script = list(script)
        self.task_ids: list[str] = []

    async def __call__(self, task: WebResearchTask) -> WebResearchResult:
        self.task_ids.append(task.task_id)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedVisualRunner:
    """Scripted ``list[VisualWebCandidate]`` or ``Exception`` per task; records task ids."""

    def __init__(self, script):
        self._script = list(script)
        self.task_ids: list[str] = []

    async def __call__(self, task: VisualSearchTask) -> list[VisualWebCandidate]:
        self.task_ids.append(task.task_id)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ScriptedDemoIndex:
    """Scripted ``list[SourceCandidate]`` or ``Exception``; records the frame paths."""

    def __init__(self, script):
        self._script = list(script)
        self.frames_calls: list[list[str]] = []

    def search(self, frames: list[str]) -> list[SourceCandidate]:
        self.frames_calls.append(list(frames))
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- fixtures ---


def _claim(value: str) -> ContextClaim:
    return ContextClaim(
        value=value, confidence=0.9, evidence_ids=["ev_1"], explicitly_claimed=True
    )


def _context(frame_paths=("kf_01", "kf_02")) -> VideoContext:
    return VideoContext(
        verification_id="ver_123",
        event=_claim("flood"),
        location=_claim("Jakarta"),
        time=_claim("2026-08-15"),
        evidence=[],
        keyframes=[
            KeyframeRef(
                frame_id=fid, timestamp_sec=float(i), local_path=f"/tmp/{fid}.png"
            )
            for i, fid in enumerate(frame_paths)
        ],
    )


def _plan(fc=1, web=1, vis=1) -> InvestigationPlan:
    return InvestigationPlan(
        verification_id="ver_123",
        fact_check_tasks=[
            FactCheckTask(task_id=f"fc_{i:02d}", queries=["q"], goal="g")
            for i in range(fc)
        ],
        web_research_tasks=[
            WebResearchTask(
                task_id=f"web_{i:02d}",
                question=f"Q{i}",
                queries=["q"],
                preferred_source_types=["news"],
            )
            for i in range(web)
        ],
        visual_search_tasks=[
            VisualSearchTask(task_id=f"vis_{i:02d}", frame_ids=["kf_01"], goal="g")
            for i in range(vis)
        ],
        investigation_questions=["Q?"],
        stop_conditions=["stop"],
    )


def _fact_evidence(evidence_id: str = "fc_ev_1") -> FactCheckEvidence:
    return FactCheckEvidence(
        evidence_id=evidence_id,
        query="q",
        review_url=f"https://factcheck.example/{evidence_id}",
        raw={},
    )


def _web_result(
    task_id: str, status: Literal["supported", "contradicted", "mixed", "insufficient"] = "supported"
) -> WebResearchResult:
    return WebResearchResult(
        task_id=task_id,
        question=f"Q{task_id}",
        status=status,
        finding="found",
        evidence=[],
        unresolved=[],
        searches_used=1,
        pages_fetched=1,
    )


def _visual_candidate(url: str = "https://x/a.jpg") -> VisualWebCandidate:
    return VisualWebCandidate(
        candidate_id="vw_1",
        frame_id="kf_01",
        candidate_type="full_image_match",
        url=url,
        raw_provider_type="full_matching_images",
    )


def _demo_source(source_id: str = "demo_1") -> SourceCandidate:
    return SourceCandidate(
        source_id=source_id,
        url=f"https://demo.example/{source_id}",
        canonical_url=f"https://demo.example/{source_id}",
        matched_frame_ids=["kf_01"],
        match_types=["visually_similar", "hash:average_hamming"],
        origin="demo_index",
    )


async def _execute(context, plan, fact_script, web_script, visual_script, demo_script):
    """Run execute with scripted fakes; returns (bundle, fakes...) for assertions."""
    fact = ScriptedFactRunner(fact_script)
    web = ScriptedWebRunner(web_script)
    visual = ScriptedVisualRunner(visual_script)
    demo = ScriptedDemoIndex(demo_script)
    bundle = await execute(
        context,
        plan,
        run_fact_check=fact,
        investigate=web,
        run_visual=visual,
        demo_index=demo.search,
    )
    return bundle, fact, web, visual, demo


# --- all success ---


async def test_all_success_full_bundle_and_statuses():
    bundle, fact, web, visual, demo = await _execute(
        _context(),
        _plan(),
        fact_script=[[_fact_evidence()]],
        web_script=[_web_result("web_00")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
    )

    assert bundle.verification_id == "ver_123"
    assert bundle.plan.fact_check_tasks[0].task_id == "fc_00"  # plan carried
    assert [e.evidence_id for e in bundle.fact_checks] == ["fc_ev_1"]
    assert bundle.web_research[0].task_id == "web_00"
    assert [c.url for c in bundle.visual_candidates] == ["https://x/a.jpg"]
    assert bundle.branch_status == {
        "fact_check": "success",
        "web_research": "success",
        "visual_search": "success",
    }
    assert bundle.errors == []
    assert demo.frames_calls == []  # fallback untouched on success
    assert fact.task_ids == ["fc_00"]
    assert web.task_ids == ["web_00"]
    assert visual.task_ids == ["vis_00"]


async def test_fact_no_matches_is_valid_success_no_matches():
    bundle, *_ = await _execute(
        _context(),
        _plan(),
        fact_script=[[]],
        web_script=[_web_result("web_00")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
    )

    assert bundle.fact_checks == []
    assert bundle.branch_status["fact_check"] == "success_no_matches"
    assert bundle.errors == []


# --- fact check branch ---


async def test_fact_timeout_error_and_other_branches_survive(monkeypatch):
    monkeypatch.setattr(orchestrator, "FACT_CHECK_TIMEOUT_SEC", 0.01)

    async def hanging(task):
        await asyncio.sleep(0.2)
        return [_fact_evidence()]

    web = ScriptedWebRunner([_web_result("web_00")])
    visual = ScriptedVisualRunner([[_visual_candidate()]])

    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=hanging,
        investigate=web,
        run_visual=visual,
        demo_index=ScriptedDemoIndex([]).search,
    )

    assert bundle.branch_status["fact_check"] == "error"
    assert bundle.fact_checks == []
    assert any("TimeoutError" in e for e in bundle.errors)
    assert bundle.branch_status["web_research"] == "success"  # sibling branches done
    assert bundle.branch_status["visual_search"] == "success"
    assert bundle.web_research[0].task_id == "web_00"
    assert web.task_ids == ["web_00"]
    assert visual.task_ids == ["vis_00"]


async def test_fact_partial_failure_preserves_sibling_evidence():
    bundle, *_ = await _execute(
        _context(),
        _plan(fc=2),
        fact_script=[RuntimeError("fact check api down"), [_fact_evidence("fc_ev_2")]],
        web_script=[_web_result("web_00")],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
    )

    assert bundle.branch_status["fact_check"] == "partial_failure"
    assert [e.evidence_id for e in bundle.fact_checks] == ["fc_ev_2"]
    assert any("fact check api down" in e for e in bundle.errors)


# --- web research branch ---


async def test_web_timeout_partial_failure_insufficient_stub(monkeypatch):
    monkeypatch.setattr(orchestrator, "WEB_TIMEOUT_SEC", 0.01)

    async def runner(task):
        if task.task_id == "web_00":
            await asyncio.sleep(0.2)  # only the first task hangs
        return _web_result(task.task_id)

    bundle = await execute(
        _context(),
        _plan(web=2),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=runner,
        run_visual=ScriptedVisualRunner([[_visual_candidate()]]),
        demo_index=ScriptedDemoIndex([]).search,
    )

    assert bundle.branch_status["web_research"] == "partial_failure"
    stub, ok = bundle.web_research
    assert stub.task_id == "web_00"
    assert stub.status == "insufficient"  # incomplete result, not a crash
    assert stub.unresolved  # carries the failure note
    assert ok.task_id == "web_01"
    assert ok.status == "supported"  # fast sibling preserved
    assert any("TimeoutError" in e for e in bundle.errors)


async def test_web_all_failed_error_with_insufficient_stubs(monkeypatch):
    monkeypatch.setattr(orchestrator, "WEB_TIMEOUT_SEC", 0.01)

    async def hanging(task):
        await asyncio.sleep(0.2)
        return _web_result(task.task_id)

    bundle = await execute(
        _context(),
        _plan(web=2),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=hanging,
        run_visual=ScriptedVisualRunner([[_visual_candidate()]]),
        demo_index=ScriptedDemoIndex([]).search,
    )

    assert bundle.branch_status["web_research"] == "error"
    assert len(bundle.web_research) == 2
    assert all(r.status == "insufficient" for r in bundle.web_research)
    assert bundle.fact_checks  # other branches preserved


# --- visual search branch + demo fallback ---


async def test_visual_failure_triggers_demo_fallback_with_local_paths():
    demo = ScriptedDemoIndex([[_demo_source("demo_7")]])
    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=ScriptedWebRunner([_web_result("web_00")]),
        run_visual=ScriptedVisualRunner([RuntimeError("vision api down")]),
        demo_index=demo.search,
    )

    assert bundle.branch_status["visual_search"] == "demo_fallback"
    assert [c.candidate_id for c in bundle.visual_candidates] == ["demo_7"]
    assert bundle.visual_candidates[0].raw_provider_type == "demo_index"  # origin marker
    assert bundle.visual_candidates[0].frame_id == "kf_01"  # matched_frame_ids first
    assert demo.frames_calls == [["/tmp/kf_01.png", "/tmp/kf_02.png"]]  # local paths
    assert any("vision api down" in e for e in bundle.errors)


async def test_visual_empty_triggers_demo_fallback():
    demo = ScriptedDemoIndex([[_demo_source("demo_7")]])
    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=ScriptedWebRunner([_web_result("web_00")]),
        run_visual=ScriptedVisualRunner([[]]),
        demo_index=demo.search,
    )

    assert bundle.branch_status["visual_search"] == "demo_fallback"
    assert [c.candidate_id for c in bundle.visual_candidates] == ["demo_7"]
    assert demo.frames_calls == [["/tmp/kf_01.png", "/tmp/kf_02.png"]]


async def test_demo_fallback_preserves_strongest_match_type():
    demo = ScriptedDemoIndex(
        [
            [
                _demo_source("demo_weak"),  # weak tier: stays visually_similar
                SourceCandidate(
                    source_id="demo_full",
                    url="https://demo.example/demo_full",
                    canonical_url="https://demo.example/demo_full",
                    matched_frame_ids=["kf_01"],
                    match_types=["full_image_match", "hash:average_hamming", "hash_distance:2"],
                    origin="demo_index",
                ),
            ]
        ]
    )
    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=ScriptedWebRunner([_web_result("web_00")]),
        run_visual=ScriptedVisualRunner([[]]),
        demo_index=demo.search,
    )

    by_id = {c.candidate_id: c for c in bundle.visual_candidates}
    assert by_id["demo_weak"].candidate_type == "visually_similar"  # weak stays weak
    assert by_id["demo_full"].candidate_type == "full_image_match"  # D1: strong survives
    assert by_id["demo_full"].raw_provider_type == "demo_index"


async def test_visual_zero_match_without_demo_candidates_is_valid_no_match():
    bundle, *_ = await _execute(
        _context(),
        _plan(),
        fact_script=[[_fact_evidence()]],
        web_script=[_web_result("web_00")],
        visual_script=[[]],
        demo_script=[[]],  # one successful demo query with zero hits
    )

    assert bundle.branch_status["visual_search"] == "success_no_matches"
    assert bundle.visual_candidates == []  # no demo candidates -> no fabricated match
    assert bundle.errors == []  # nothing failed; no verdict anywhere


async def test_visual_timeout_triggers_demo_fallback(monkeypatch):
    monkeypatch.setattr(orchestrator, "VISUAL_TIMEOUT_SEC", 0.01)

    async def hanging(task):
        await asyncio.sleep(0.2)
        return [_visual_candidate()]

    demo = ScriptedDemoIndex([[_demo_source()]])
    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=ScriptedFactRunner([[_fact_evidence()]]),
        investigate=ScriptedWebRunner([_web_result("web_00")]),
        run_visual=hanging,
        demo_index=demo.search,
    )

    assert bundle.branch_status["visual_search"] == "demo_fallback"
    assert bundle.visual_candidates[0].raw_provider_type == "demo_index"
    assert any("TimeoutError" in e for e in bundle.errors)


async def test_visual_partial_failure_preserves_successful_candidates():
    bundle, *_ = await _execute(
        _context(),
        _plan(vis=2),
        fact_script=[[_fact_evidence()]],
        web_script=[_web_result("web_00")],
        visual_script=[RuntimeError("vision down"), [_visual_candidate("https://x/2.jpg")]],
        demo_script=[],
    )

    assert bundle.branch_status["visual_search"] == "partial_failure"
    assert [c.url for c in bundle.visual_candidates] == ["https://x/2.jpg"]
    assert any("vision down" in e for e in bundle.errors)


# --- web task cap ---


async def test_web_three_task_cap_defended_against_five_task_plan():
    bundle, fact, web, visual, demo = await _execute(
        _context(),
        _plan(web=5),
        fact_script=[[_fact_evidence()]],
        web_script=[_web_result(f"web_{i:02d}") for i in range(3)],
        visual_script=[[_visual_candidate()]],
        demo_script=[],
    )

    assert web.task_ids == ["web_00", "web_01", "web_02"]  # only the first 3 ran
    assert [r.task_id for r in bundle.web_research] == ["web_00", "web_01", "web_02"]
    assert bundle.branch_status["web_research"] == "success"
    assert any("Bounded investigation" in e for e in bundle.errors)


# --- concurrency ---


async def test_three_branch_groups_run_concurrently(monkeypatch):
    monkeypatch.setattr(orchestrator, "FACT_CHECK_TIMEOUT_SEC", 1.0)
    fact_started = asyncio.Event()
    release_fact = asyncio.Event()

    async def fact_runner(task):
        fact_started.set()
        await release_fact.wait()  # released by the web group, if it runs in parallel
        return [_fact_evidence()]

    async def web_runner(task):
        await fact_started.wait()
        release_fact.set()
        return _web_result("web_00")

    bundle = await execute(
        _context(),
        _plan(),
        run_fact_check=fact_runner,
        investigate=web_runner,
        run_visual=ScriptedVisualRunner([[_visual_candidate()]]),
        demo_index=ScriptedDemoIndex([]).search,
    )

    assert bundle.branch_status["fact_check"] == "success"
    assert bundle.branch_status["web_research"] == "success"
    assert bundle.branch_status["visual_search"] == "success"
    assert bundle.errors == []


# --- error normalization ---


async def test_every_branch_failure_normalized_never_raises():
    bundle, *_ = await _execute(
        _context(),
        _plan(),
        fact_script=[RuntimeError("fact check down")],
        web_script=[RuntimeError("investigator down")],
        visual_script=[RuntimeError("vision down")],
        demo_script=[RuntimeError("demo index broken")],
    )

    assert bundle.branch_status == {
        "fact_check": "error",
        "web_research": "error",
        "visual_search": "unavailable",  # all visual failed and demo yielded nothing
    }
    assert bundle.fact_checks == []
    assert len(bundle.web_research) == 1
    assert bundle.web_research[0].status == "insufficient"
    assert bundle.visual_candidates == []
    assert any("fact check down" in e for e in bundle.errors)
    assert any("investigator down" in e for e in bundle.errors)
    assert any("demo index broken" in e for e in bundle.errors)


# --- budget constants ---


def test_default_timeouts_match_handoff_targets():
    assert orchestrator.FACT_CHECK_TIMEOUT_SEC == 15.0  # §11.2: 15s/task
    assert orchestrator.VISUAL_TIMEOUT_SEC == 18.0  # §11.2: 15-20s/frame
    assert orchestrator.WEB_TIMEOUT_SEC == 150.0  # §11.2: 45-60s/task; raised to 150s after measured 82-120s/task on opencode-go
