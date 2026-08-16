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

The T39 demo query cache is an opt-in seam: ``execute(..., cache=query_cache)``
wraps the branch runners so repeated same-key calls skip the provider (24h
TTL, success-only entries, per-origin keys). ``cache=None`` (default) runs
with no caching at all, preserving the pre-cache behavior byte for byte.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Sequence, cast

from backend.schemas.context import VideoContext
from backend.schemas.evidence import KeyframeRef
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
from backend.services.validation.cache import (
    QueryCache,
    fact_check_key,
    frame_key_from_path,
    web_research_key,
)
from backend.utils.observability import log_event

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


def _cached_fact(runner: FactRunner, cache: QueryCache | None) -> FactRunner:
    """Cache seam: ``fc:{query}:{lang}`` keys, per-query partitions.

    Any miss runs the provider with the full task (provider protocol and
    dedupe scope unchanged); the returned evidence is then partitioned by
    ``query`` and cached under every (query, lang) key of the task. Only a
    successful return populates the cache — a raised provider error retries
    on the next execution.
    """

    async def _run(task: FactCheckTask) -> list[FactCheckEvidence]:
        assert cache is not None  # this closure exists only when cache is provided
        languages = task.language_codes or [""]
        keys = [fact_check_key(q, lang) for q in task.queries for lang in languages]
        cached = [cache.get(k) for k in keys]
        if cached and all(value is not None for value in cached):
            return [
                evidence for value in cached if value is not None for evidence in value
            ]
        evidence = await runner(task)
        # ponytail: evidence carries no language field, so partitions are per
        # query; keys stay per (query, lang). Fine for the demo, revisit if
        # per-language partitioning ever matters.
        for query in task.queries:
            partition = [e for e in evidence if e.query == query]
            for lang in languages:
                cache.set(fact_check_key(query, lang), partition)
        return evidence

    return runner if cache is None else _run


def _cached_web(runner: WebRunner, cache: QueryCache | None) -> WebRunner:
    """Cache seam: one whole-task ``web:{task_id}:{question}`` key."""

    async def _run(task: WebResearchTask) -> WebResearchResult:
        assert cache is not None  # this closure exists only when cache is provided
        key = web_research_key(task.task_id, task.question)
        cached = cache.get(key)
        if cached is not None:
            return cached
        result = await runner(task)
        cache.set(key, result)  # success only; a raised error retries next run
        return result

    return runner if cache is None else _run


def _cached_visual(
    runner: VisualRunner, cache: QueryCache | None, keyframes: list[KeyframeRef]
) -> VisualRunner:
    """Cache seam: per-frame ``vis:{frame_hash}`` keys with task narrowing.

    All-miss runs the runner with the full task (current behavior); when some
    frames are already cached the task is narrowed to the uncached frames
    only, and fresh candidates are partitioned by ``frame_id`` and cached per
    frame. Missing/unreadable frames have no key and always reach the runner.
    Cached partitions merge back in ``task.frame_ids`` order, matching a full
    run's output order.
    """

    async def _run(task: VisualSearchTask) -> list[VisualWebCandidate]:
        assert cache is not None  # this closure exists only when cache is provided
        if not task.frame_ids:
            return await runner(task)
        frame_keys: list[tuple[str, str | None]] = []
        by_id = {keyframe.frame_id: keyframe for keyframe in keyframes}
        for frame_id in task.frame_ids:
            keyframe = by_id.get(frame_id)
            if keyframe is None:
                frame_keys.append((frame_id, None))
                continue
            frame_keys.append(
                (frame_id, await asyncio.to_thread(frame_key_from_path, keyframe.local_path))
            )
        cached = [(fid, cache.get(key) if key is not None else None) for fid, key in frame_keys]
        if all(value is not None for _, value in cached):
            return [
                candidate
                for _, value in cached
                if value is not None
                for candidate in value
            ]
        missing = [fid for fid, value in cached if value is None]
        narrowed = (
            task
            if len(missing) == len(task.frame_ids)
            else task.model_copy(update={"frame_ids": missing})
        )
        candidates = await runner(narrowed)
        fresh: dict[str, list[VisualWebCandidate]] = {}
        for frame_id in missing:
            fresh[frame_id] = [c for c in candidates if c.frame_id == frame_id]
        for frame_id, key in frame_keys:
            if key is not None and frame_id in fresh:
                cache.set(key, fresh[frame_id])
        # ponytail: partitions of a deduped merged run; a frame-subset rerun
        # can miss URLs deduped across frames — recompute raw per-frame runs
        # if that ever matters.
        merged: list[VisualWebCandidate] = []
        for frame_id, value in cached:
            merged.extend(value if value is not None else fresh.get(frame_id, []))
        return merged

    return runner if cache is None else _run


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
) -> tuple[list[Any], float]:
    """One outcome per (runner, task): the value or its exception — never raises.

    Returns ``(outcomes, max_task_latency_ms)``: a group finishes when its
    slowest task does, so the max task duration is the group's effective
    wall-clock time (measured even on task failure).
    """
    durations: list[float] = []

    async def _bounded(runner, task):
        t0 = time.perf_counter()
        try:
            return await asyncio.wait_for(runner(task), timeout=timeout_sec)
        finally:
            durations.append((time.perf_counter() - t0) * 1000)

    outcomes = await asyncio.gather(
        *(_bounded(runner, task) for runner, task in runners), return_exceptions=True
    )
    return outcomes, round(max(durations, default=0.0), 3)


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
    cache: QueryCache | None = None,
) -> RawValidationBundle:
    """Plan -> RawValidationBundle: three concurrent branch groups, bounded tasks.

    ``cache`` (T39) is the demo query-cache seam: when provided, successful
    provider results are cached per key and repeated same-key calls skip the
    provider. ``None`` (the default) runs exactly as before with no caching —
    the process-local ``query_cache`` singleton is the intended demo wiring.
    """
    run_fact_check, investigate, run_visual, demo_index = _default_runners(
        context, run_fact_check, investigate, run_visual, demo_index
    )

    fact_runner = _cached_fact(run_fact_check, cache)
    web_runner = _cached_web(investigate, cache)
    visual_runner = _cached_visual(run_visual, cache, context.keyframes)

    web_tasks = plan.web_research_tasks[:MAX_WEB_TASKS]
    errors: list[str] = []
    if len(plan.web_research_tasks) > MAX_WEB_TASKS:
        errors.append(
            f"Bounded investigation: web research tasks truncated to {MAX_WEB_TASKS} "
            f"(plan carried {len(plan.web_research_tasks)})."
        )

    (
        (fact_outcomes, fact_latency_ms),
        (web_outcomes, web_latency_ms),
        (
            visual_outcomes,
            visual_latency_ms,
        ),
    ) = cast(
        tuple[
            tuple[list[Any], float],
            tuple[list[Any], float],
            tuple[list[Any], float],
        ],
        await asyncio.gather(
            _run_group(
                [(fact_runner, task) for task in plan.fact_check_tasks],
                FACT_CHECK_TIMEOUT_SEC,
            ),
            _run_group([(web_runner, task) for task in web_tasks], WEB_TIMEOUT_SEC),
            _run_group(
                [(visual_runner, task) for task in plan.visual_search_tasks],
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

    # T37: one structured event per branch; branch status carries failure
    # visibility (error / partial_failure / success_no_matches / demo_fallback).
    log_event(
        context.verification_id,
        "fact_check_search",
        "fact_check",
        fact_latency_ms,
        fact_status,
        tasks=len(plan.fact_check_tasks),
        evidence=len(fact_checks),
    )
    log_event(
        context.verification_id,
        "web_research",
        "web_research",
        web_latency_ms,
        web_status,
        tasks=len(web_tasks),
        searches=sum(result.searches_used for result in web_research),
        pages_fetched=sum(result.pages_fetched for result in web_research),
    )
    log_event(
        context.verification_id,
        "visual_source_search",
        "visual",
        visual_latency_ms,
        visual_status,
        tasks=len(plan.visual_search_tasks),
        candidates=len(visual_candidates),
        demo_fallback=visual_status == "demo_fallback",
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
