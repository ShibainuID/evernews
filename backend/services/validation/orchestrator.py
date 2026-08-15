"""Parallel validation orchestrator (T32): InvestigationPlan -> RawValidationBundle.

Runs the three branch groups — fact check (T28), web research (T29), visual
search (T30) — concurrently via nested ``asyncio.gather(return_exceptions=True)``
(HANDOFF §11.1); every task inside a group runs under its own
``asyncio.wait_for`` budget (HANDOFF §11.2: 15s fact-check, 60s web, 18s
visual). No failure aborts the bundle: exceptions are normalized into
``RawValidationBundle.errors`` + ``branch_status`` and partial evidence from
successful siblings is preserved. A failed/timed-out web task becomes an
explicit ``status="insufficient"`` ``WebResearchResult`` (HANDOFF §11.3). When
the visual branch yields zero candidates (empty or unavailable) the demo
source index (T27) is queried with the context's keyframe local paths; its
candidates enter the bundle marked ``raw_provider_type="demo_index"``. A
successful zero-match without demo candidates remains a valid no-match — no
verdict is produced anywhere (T33 consumes the bundle).

The orchestrator is provider-agnostic: the branch dependencies (``run_fact_check``
/ ``investigate`` / ``run_visual`` / ``demo_index``) are injected; production
defaults are wired lazily in ``_default_runners`` so importing this module
never touches concrete providers, ``Settings``, or the demo index file.
"""

import asyncio
from typing import Any, Awaitable, Callable, Sequence, cast

from backend.schemas.context import VideoContext
from backend.schemas.investigation import (
    FactCheckEvidence,
    FactCheckTask,
    InvestigationPlan,
    RawValidationBundle,
    VisualSearchTask,
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)
from backend.schemas.result import SourceCandidate

# Branch budgets (HANDOFF §11.2). Module-level so tests can shorten them.
FACT_CHECK_TIMEOUT_SEC = 15.0
WEB_TIMEOUT_SEC = 60.0
VISUAL_TIMEOUT_SEC = 18.0

# Second line of defense after the planner's Settings cap (T31): a plan that
# somehow carries more web tasks than this is truncated here, never executed.
MAX_WEB_TASKS = 3

FactRunner = Callable[[FactCheckTask], Awaitable[list[FactCheckEvidence]]]
WebRunner = Callable[[WebResearchTask], Awaitable[WebResearchResult]]
VisualRunner = Callable[[VisualSearchTask], Awaitable[list[VisualWebCandidate]]]
DemoSearch = Callable[[list[str]], list[SourceCandidate]]


def _error_text(exc: BaseException) -> str:
    detail = str(exc).strip()
    return detail or type(exc).__name__


def _insufficient_web(task: WebResearchTask, error: BaseException) -> WebResearchResult:
    """A failed/timed-out web task becomes an explicit incomplete result."""
    return WebResearchResult(
        task_id=task.task_id,
        question=task.question,
        status="insufficient",
        finding="Web research could not complete for this task.",
        evidence=[],
        unresolved=[f"web task {task.task_id}: {_error_text(error)}"],
        searches_used=0,
        pages_fetched=0,
    )


async def _run_group(
    runners: Sequence[tuple[Callable[..., Awaitable[Any]], Any]], timeout_sec: float
) -> list[Any]:
    """One outcome per (runner, task): the value or its exception — never raises."""

    async def _bounded(runner, task):
        return await asyncio.wait_for(runner(task), timeout=timeout_sec)

    return await asyncio.gather(
        *(_bounded(runner, task) for runner, task in runners), return_exceptions=True
    )


def _fact_normalize(outcomes: list[Any]) -> tuple[list[FactCheckEvidence], str, list[str]]:
    evidence: list[FactCheckEvidence] = []
    errors: list[str] = []
    failed = 0
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            failed += 1
            errors.append(f"fact check task: {_error_text(outcome)}")
        else:
            evidence.extend(outcome)
    if failed:
        status = "error" if failed == len(outcomes) else "partial_failure"
    else:
        status = "success" if evidence else "success_no_matches"
    return evidence, status, errors


def _web_normalize(
    outcomes: list[Any], tasks: Sequence[WebResearchTask]
) -> tuple[list[WebResearchResult], str, list[str]]:
    results: list[WebResearchResult] = []
    errors: list[str] = []
    failed = 0
    for outcome, task in zip(outcomes, tasks):
        if isinstance(outcome, BaseException):
            failed += 1
            errors.append(f"web research task {task.task_id}: {_error_text(outcome)}")
            results.append(_insufficient_web(task, outcome))
        else:
            results.append(outcome)
    if failed == 0:
        status = "success" if results else "success_no_matches"
    elif failed == len(tasks):
        status = "error"
    else:
        status = "partial_failure"
    return results, status, errors


def _demo_candidate(candidate: SourceCandidate, context: VideoContext) -> VisualWebCandidate:
    """Adapt a ``SourceCandidate`` at the boundary; origin survives in raw_provider_type."""
    frame_id = (
        candidate.matched_frame_ids[0]
        if candidate.matched_frame_ids
        else (context.keyframes[0].frame_id if context.keyframes else "unknown")
    )
    return VisualWebCandidate(
        candidate_id=candidate.source_id,
        frame_id=frame_id,
        candidate_type="visually_similar",
        url=candidate.url,
        page_url=candidate.url,
        page_title=candidate.title,
        raw_provider_type="demo_index",
    )


async def _visual_normalize(
    outcomes: list[Any], context: VideoContext, demo_index: DemoSearch
) -> tuple[list[VisualWebCandidate], str, list[str]]:
    candidates: list[VisualWebCandidate] = []
    errors: list[str] = []
    failed = 0
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            failed += 1
            errors.append(f"visual search task: {_error_text(outcome)}")
        else:
            candidates.extend(outcome)
    if outcomes and not candidates:  # empty or unavailable -> demo fallback (T27)
        try:
            demo_candidates = [
                _demo_candidate(c, context)
                for c in await asyncio.to_thread(
                    demo_index, [keyframe.local_path for keyframe in context.keyframes]
                )
            ]
        except Exception as exc:
            errors.append(f"demo index: {_error_text(exc)}")
            demo_candidates = []
        candidates.extend(demo_candidates)
        if demo_candidates:
            return candidates, "demo_fallback", errors
    if failed == 0:
        status = "success" if candidates else "success_no_matches"
    elif failed == len(outcomes):
        status = "unavailable"
    else:
        status = "partial_failure"
    return candidates, status, errors


def _failing_demo(error: BaseException) -> DemoSearch:
    """Demo index could not be built: search raises, normalization handles it."""

    def _search(frames: list[str]) -> list[SourceCandidate]:
        raise RuntimeError(f"demo index unavailable: {_error_text(error)}")

    return _search


def _default_runners(
    context: VideoContext,
    run_fact_check: FactRunner | None,
    investigate: WebRunner | None,
    run_visual: VisualRunner | None,
    demo_index: DemoSearch | None,
) -> tuple[FactRunner, WebRunner, VisualRunner, DemoSearch]:
    """Production wiring for any seam left out (lazy imports: provider-agnostic)."""
    if run_fact_check is None:
        from backend.services.validation.fact_check import run_fact_check_task

        run_fact_check = run_fact_check_task
    if investigate is None:
        from backend.providers.opencode import OpenCodeResearchProvider

        investigate = OpenCodeResearchProvider().investigate
    if run_visual is None:
        from backend.services.validation.vision_search import run_visual_task

        async def _visual(task: VisualSearchTask) -> list[VisualWebCandidate]:
            return await run_visual_task(task, context.keyframes)

        run_visual = _visual
    if demo_index is None:
        from backend.services.evidence.demo_index import DemoIndex

        try:
            demo_index = DemoIndex().search
        except Exception as exc:  # index missing/broken: fallback is simply unavailable
            demo_index = _failing_demo(exc)
    return run_fact_check, investigate, run_visual, demo_index


async def execute(
    context: VideoContext,
    plan: InvestigationPlan,
    *,
    run_fact_check: FactRunner | None = None,
    investigate: WebRunner | None = None,
    run_visual: VisualRunner | None = None,
    demo_index: DemoSearch | None = None,
) -> RawValidationBundle:
    """Plan -> RawValidationBundle: three concurrent branch groups, bounded tasks."""
    run_fact_check, investigate, run_visual, demo_index = _default_runners(
        context, run_fact_check, investigate, run_visual, demo_index
    )

    web_tasks = plan.web_research_tasks[:MAX_WEB_TASKS]
    errors: list[str] = []
    if len(plan.web_research_tasks) > MAX_WEB_TASKS:
        errors.append(
            f"Bounded investigation: web research tasks truncated to {MAX_WEB_TASKS} "
            f"(plan carried {len(plan.web_research_tasks)})."
        )

    fact_outcomes, web_outcomes, visual_outcomes = cast(
        tuple[list[Any], list[Any], list[Any]],
        await asyncio.gather(
            _run_group(
                [(run_fact_check, task) for task in plan.fact_check_tasks],
                FACT_CHECK_TIMEOUT_SEC,
            ),
            _run_group([(investigate, task) for task in web_tasks], WEB_TIMEOUT_SEC),
            _run_group(
                [(run_visual, task) for task in plan.visual_search_tasks],
                VISUAL_TIMEOUT_SEC,
            ),
            return_exceptions=True,
        ),
    )

    fact_checks, fact_status, fact_errors = _fact_normalize(fact_outcomes)
    web_research, web_status, web_errors = _web_normalize(web_outcomes, web_tasks)
    visual_candidates, visual_status, visual_errors = await _visual_normalize(
        visual_outcomes, context, demo_index
    )

    return RawValidationBundle(
        verification_id=context.verification_id,
        plan=plan,
        fact_checks=fact_checks,
        web_research=web_research,
        visual_candidates=visual_candidates,
        errors=[*errors, *fact_errors, *web_errors, *visual_errors],
        branch_status={
            "fact_check": fact_status,
            "web_research": web_status,
            "visual_search": visual_status,
        },
    )
