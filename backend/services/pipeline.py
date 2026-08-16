"""End-to-end verification pipeline (T35): HANDOFF §38 serial order, §22.2 stages.

``run_verification`` is the single producer entry point: a saved upload +
caption -> ``VerificationResult``, while driving the in-memory state store
(§23) through the exact §22.2 stage names. Provider wiring goes through the
injected ``Providers`` bundle of T21 protocol contracts — no concrete
provider adapter is imported here.

Heavy native providers (faster-whisper/ctranslate2, PaddlePaddle/PaddleOCR)
never execute in the pipeline's main Python process: when ``isolated`` (the
production default) the speech/OCR protocol calls are shipped to one spawned
child process each (stdlib ``multiprocessing``, spawn context) with a
bounded timeout. A child failure or timeout degrades to empty evidence and
the later OCR/visual stages continue (HANDOFF §26); tests disable the flag
so scripted fakes run deterministically in-process.

Failure policy (§26): preprocessing rejections fail the verification with
the stable machine ``code`` (e.g. ``video_too_long``); branch failures
(fact check, investigator, vision) are normalized by T32 into
``bundle.errors`` / ``branch_status`` and surfaced in the result's
``unresolved`` while the result is still built; a core LLM step (fusion,
plan, synthesis) failing after its own bounded repair is a failed
verification, never a fabricated result.
"""

import asyncio
import multiprocessing
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend import state as state_module
from backend.config import Settings
from backend.providers.base import (
    LunaProvider,
    OCRExtractor,
    SpeechProvider,
    VisionProvider,
    WebResearchProvider,
)
from backend.schemas.context import OCRHit, SpeechExtraction, VisualObservation
from backend.schemas.evidence import KeyframeRef, OCRFrameRef
from backend.schemas.investigation import RawValidationBundle, VisualSearchTask, VisualWebCandidate
from backend.schemas.result import VerificationResult
from backend.services.context import context_fuser, visual_extractor
from backend.services.evidence import comparator, normalizer, source_ranker, synthesizer
from backend.services.ingestion.video_ingestor import is_image_file
from backend.services.preprocessing import ffmpeg, frame_sampler, keyframes
from backend.services.result import result_builder
from backend.services.validation import orchestrator, planner
from backend.services.validation.cache import QueryCache
from backend.state import STATUS_COMPLETED, STATUS_FAILED
from backend.utils.fetch import SafeFetchResult
from backend.utils.observability import log_event, verification_scope

# Heavy-provider child budgets (HANDOFF §11.2 spirit). Module-level so tests
# can shorten them.
SPEECH_TIMEOUT_SEC = 120.0
OCR_TIMEOUT_SEC = 120.0
_CHILD_JOIN_SEC = 10.0

# HANDOFF §22.2 stages -> progress (monotonic, observable via state.get).
_STAGE_PROGRESS: dict[str, float] = {
    "queued": 0.0,
    "preprocessing": 0.1,
    "extracting_context": 0.25,
    "planning_investigation": 0.35,
    "fact_check_search": 0.45,
    "web_research": 0.55,
    "visual_source_search": 0.65,
    "synthesizing_evidence": 0.8,
    "comparing_context": 0.9,
    "completed": 1.0,
    "failed": 1.0,
}


@dataclass(frozen=True)
class VerificationRequest:
    """Producer request: a T18-validated saved upload plus optional context."""

    video_path: Path
    caption: str = ""
    source_url: str | None = None


@dataclass
class Providers:
    """T21 protocol-contract bundle injected into ``run_verification``.

    ``fact_check`` / ``demo_index`` / ``page_fetcher`` are optional branch
    seams (T28/T27/T13); T32's orchestrator lazy-loads production defaults
    for the first two when None. ``isolated`` moves the heavy native
    providers into spawned child processes; tests set it False so scripted
    fakes run deterministically in-process. ``cache`` (T39) is the demo
    query-cache seam forwarded to ``orchestrator.execute``; None (the
    default) keeps the pre-cache no-cache behavior — the production bundle
    (``backend/api/verification.get_providers``) supplies the process-local
    singleton so repeated demo runs reuse provider results.
    """

    speech: SpeechProvider
    ocr: OCRExtractor
    luna: LunaProvider
    vision: VisionProvider
    web_research: WebResearchProvider
    fact_check: orchestrator.FactRunner | None = None
    demo_index: orchestrator.DemoSearch | None = None
    page_fetcher: Callable[[str], Awaitable[SafeFetchResult]] | None = None
    isolated: bool = True
    cache: QueryCache | None = None


# --- heavy-provider process isolation (stdlib multiprocessing, spawn) ---


def _speech_worker(speech: SpeechProvider, audio_path: str) -> SpeechExtraction:
    """Child entry point: bridge the async protocol to the sync process."""
    return asyncio.run(speech.transcribe(audio_path))


def _ocr_worker(ocr: OCRExtractor, frames: list[OCRFrameRef]) -> list[OCRHit]:
    """Child entry point: OCR over the dedicated ~1 fps sampled frame set."""
    return ocr.extract(frames)


def _child_target(child, worker: Callable[..., Any], args: tuple[Any, ...]) -> None:
    """Child body: send the result, or the exception, back through the pipe."""
    try:
        child.send(("ok", worker(*args)))
    except BaseException as exc:  # noqa: BLE001 - the pipe is the only error channel
        try:
            child.send(("err", exc))
        except Exception:
            pass
    finally:
        child.close()


def _run_child(worker: Callable[..., Any], args: tuple[Any, ...], timeout_sec: float) -> Any:
    """Run ``worker(*args)`` in one spawned child; re-raise its exception.

    Spawn (not fork) so the heavy native libraries load fresh in the child
    and never inherit a parent process that may have imported them. A child
    that exceeds ``timeout_sec`` is terminated; one that dies without a
    reply raises ``RuntimeError`` (segfaulted C extension).
    """
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(False)
    proc = context.Process(
        target=_child_target, args=(child, worker, args), name="heavy-provider", daemon=True
    )
    proc.start()
    child.close()
    if not parent.poll(timeout_sec):
        parent.close()
        proc.terminate()
        proc.join(timeout=_CHILD_JOIN_SEC)
        raise TimeoutError(f"heavy provider exceeded {timeout_sec}s in child process")
    try:
        kind, payload = parent.recv()
    except EOFError as exc:
        raise RuntimeError("heavy provider child died without a result") from exc
    finally:
        parent.close()
    proc.join(timeout=_CHILD_JOIN_SEC)
    if kind == "err":
        raise payload
    return payload


async def _extract_speech(providers: Providers, audio_path: str) -> SpeechExtraction:
    if providers.isolated:
        return await asyncio.to_thread(
            _run_child, _speech_worker, (providers.speech, audio_path), SPEECH_TIMEOUT_SEC
        )
    return await providers.speech.transcribe(audio_path)


async def _extract_ocr(providers: Providers, frames: list[OCRFrameRef]) -> list[OCRHit]:
    if providers.isolated:
        return await asyncio.to_thread(
            _run_child, _ocr_worker, (providers.ocr, frames), OCR_TIMEOUT_SEC
        )
    return providers.ocr.extract(frames)


def _visual_runner(providers: Providers, keyframe_refs: list[KeyframeRef]):
    """Lazy T30 runner bound to the injected vision provider (no concrete import)."""

    from backend.services.validation.vision_search import run_visual_task

    async def _runner(task: VisualSearchTask) -> list[VisualWebCandidate]:
        return await run_visual_task(task, keyframe_refs, provider=providers.vision)

    return _runner


async def _default_fetcher(url: str) -> SafeFetchResult:
    """Production page-enrichment fetcher (T13); lazy so imports stay light."""
    from backend.utils.fetch import safe_fetch

    return await safe_fetch(url)


# --- state helpers ---


def _web_research_incomplete(bundle: RawValidationBundle) -> bool:
    """Deterministic failed/timeout web-branch detection (F35-1).

    T32/T29 encode a failed or timed-out web task as a schema-valid
    ``status="insufficient"`` ``WebResearchResult`` carrying unresolved error
    notes, and mark the branch ``error``/``partial_failure`` — while a
    legitimate no-result finding stays ``insufficient`` with no notes.
    """
    if bundle.branch_status.get("web_research") in ("error", "partial_failure"):
        return True
    return any(
        result.status == "insufficient" and bool(result.unresolved)
        for result in bundle.web_research
    )


def _enter(ver_id: str, stage: str) -> None:
    state_module.store.update(ver_id, stage=stage, progress=_STAGE_PROGRESS[stage])


def _fail(ver_id: str, error: str) -> None:
    state_module.store.update(
        ver_id,
        status=STATUS_FAILED,
        stage="failed",
        progress=_STAGE_PROGRESS["failed"],
        error=error,
    )


def _elapsed_ms(t0: float) -> float:
    """Wall-clock stage duration in ms (deterministic, clamped by log_event)."""
    return round((time.perf_counter() - t0) * 1000, 3)


# --- the pipeline (HANDOFF §38) ---


async def run_verification(
    ver_id: str,
    request: VerificationRequest,
    providers: Providers,
    settings: Settings | None = None,
) -> VerificationResult:
    """Run the §38 serial pipeline with §22.2 stage tracking; return the result.

    Raises on failure (so background-task callers can log it) after the
    state store has been moved to ``failed``.
    """
    settings = settings if settings is not None else Settings()
    state_module.store.create(ver_id)
    now = date.today()
    unresolved_notes: list[str] = []
    _t0 = time.perf_counter()  # earliest bound; every stage rebinds it before its work
    with verification_scope(ver_id):  # T37: all backend logs in this task carry the id
        try:
            # ----- Part 1: preprocessing (T19) + keyframes (T20) -----
            _enter(ver_id, "preprocessing")
            _t0 = time.perf_counter()
            # Images skip ffmpeg entirely: the image itself is the only keyframe.
            is_image = is_image_file(request.video_path)
            if is_image:
                keyframe_refs = [
                    KeyframeRef(
                        frame_id=f"{ver_id}_kf000",
                        timestamp_sec=0.0,
                        local_path=str(request.video_path),
                        selection_reason="single_image_upload",
                    )
                ]
            else:
                artifacts = ffmpeg.preprocess(ver_id, request.video_path, settings)
                keyframe_refs = keyframes.select_keyframes(artifacts.normalized_path, ver_id, settings)
            log_event(
                ver_id,
                "preprocessing",
                "ffmpeg",
                _elapsed_ms(_t0),
                "success",
                keyframes=len(keyframe_refs),
                media="image" if is_image else "video",
            )

            # ----- context extraction (T24/T25/T22/T23): heavy providers isolated -----
            _enter(ver_id, "extracting_context")
            if is_image:
                # images have no audio track: empty speech, not an error (§26)
                speech = SpeechExtraction()
            else:
                try:
                    _t0 = time.perf_counter()
                    speech = await _extract_speech(providers, str(artifacts.audio_path))
                    log_event(
                        ver_id,
                        "extracting_context",
                        "speech",
                        _elapsed_ms(_t0),
                        "success",
                        segments=len(speech.segments),
                    )
                except Exception as exc:
                    speech = SpeechExtraction()  # §26: whisper failure -> empty speech, continue
                    unresolved_notes.append(f"speech extraction failed: {exc}")
                    log_event(
                        ver_id, "extracting_context", "speech", _elapsed_ms(_t0), "error", degraded=True
                    )
            try:
                _t0 = time.perf_counter()
                # §4.4: OCR uses its own ~1 fps sampled set, never the visual
                # keyframes — overlay text changes while scenes stay static.
                # For an image the set is the image itself at t=0.
                ocr_refs = (
                    [OCRFrameRef(local_path=str(request.video_path), timestamp_sec=0.0)]
                    if is_image
                    else frame_sampler.ocr_frame_refs(artifacts.normalized_path, ver_id, settings)
                )
                ocr = await _extract_ocr(providers, ocr_refs)
                log_event(
                    ver_id,
                    "extracting_context",
                    "ocr",
                    _elapsed_ms(_t0),
                    "success",
                    frames=len(ocr_refs),
                    hits=len(ocr),
                )
            except Exception as exc:
                ocr = []  # §26: OCR failure -> no text, continue
                unresolved_notes.append(f"OCR extraction failed: {exc}")
                log_event(
                    ver_id, "extracting_context", "ocr", _elapsed_ms(_t0), "error", degraded=True
                )
            try:
                _t0 = time.perf_counter()
                visual = await visual_extractor.extract(keyframe_refs, providers.luna)
                log_event(
                    ver_id,
                    "extracting_context",
                    "visual",
                    _elapsed_ms(_t0),
                    "success",
                    frames=len(visual.evidence_frames),
                )
            except Exception as exc:
                visual = VisualObservation()  # every keyframe failed: evidence gap, continue
                unresolved_notes.append(f"visual extraction failed: {exc}")
                log_event(
                    ver_id, "extracting_context", "visual", _elapsed_ms(_t0), "error", degraded=True
                )

            context = await context_fuser.fuse(
                ver_id,
                request.caption,
                speech,
                ocr,
                visual,
                keyframe_refs,
                now,
                luna_provider=providers.luna,
            )
            if unresolved_notes:
                context.unresolved.extend(unresolved_notes)

            # ----- planning (T31) -----
            _enter(ver_id, "planning_investigation")
            _t0 = time.perf_counter()
            plan = await planner.create_plan(context, providers.luna, settings)
            state_module.store.update(ver_id, plan=plan.model_dump(mode="json"))
            log_event(
                ver_id,
                "planning_investigation",
                "luna",
                _elapsed_ms(_t0),
                "success",
                fact_check_tasks=len(plan.fact_check_tasks),
                web_tasks=len(plan.web_research_tasks),
                visual_tasks=len(plan.visual_search_tasks),
            )

            # ----- validation branches (T32; the three run concurrently inside) -----
            # ponytail: branch stages are entered sequentially and the last write
            # wins for pollers while T32's gather runs — the branches genuinely
            # overlap, so a single stage can only approximate the phase.
            for stage in ("fact_check_search", "web_research", "visual_source_search"):
                _enter(ver_id, stage)
            bundle = await orchestrator.execute(
                context,
                plan,
                run_fact_check=providers.fact_check,
                investigate=providers.web_research.investigate,
                run_visual=_visual_runner(providers, keyframe_refs),
                demo_index=providers.demo_index,
                cache=providers.cache,
            )
            state_module.store.update(ver_id, bundle=bundle.model_dump(mode="json"))
            if bundle.errors:
                context.unresolved.extend(
                    dict.fromkeys(bundle.errors)
                )  # §26: branch errors recorded
            if _web_research_incomplete(bundle):
                context.unresolved.append("web_research incomplete")  # F35-1: explicit gap marker

            # ----- synthesis (T13/T14/T33) -----
            _enter(ver_id, "synthesizing_evidence")
            _t0 = time.perf_counter()
            candidates = await normalizer.build_source_candidates(
                context, bundle, providers.page_fetcher or _default_fetcher
            )
            ranked = source_ranker.rank(candidates, now)
            synthesis = await synthesizer.synthesize(
                context, bundle, ranked, luna_provider=providers.luna
            )
            log_event(
                ver_id,
                "synthesizing_evidence",
                "luna",
                _elapsed_ms(_t0),
                "success",
                candidates=len(ranked),
                selected_source_id=synthesis.best_visual_source_id or "none",
            )

            # ----- comparison + result (T15/T34) -----
            _enter(ver_id, "comparing_context")
            _t0 = time.perf_counter()
            comparison = comparator.compare(context, synthesis.probable_source_context)
            result = result_builder.build(context, synthesis, comparison, ranked)
            log_event(
                ver_id,
                "comparing_context",
                "comparator",
                _elapsed_ms(_t0),
                "success",
                classification=result.classification,
            )

            state_module.store.update(
                ver_id,
                status=STATUS_COMPLETED,
                stage="completed",
                progress=_STAGE_PROGRESS["completed"],
                error=None,
                result=result,
            )
            log_event(
                ver_id,
                "completed",
                "pipeline",
                _elapsed_ms(_t0),
                "success",
                sources=len(result.sources),
            )
            return result
        except ffmpeg.PreprocessingError as exc:
            _fail(ver_id, f"{exc.code}: {exc}")  # e.g. "video_too_long: ..."
            log_event(
                ver_id,
                "failed",
                "pipeline",
                _elapsed_ms(_t0),
                "error",
                error_stage="preprocessing",
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            failing_state = state_module.store.get(ver_id)
            failing_stage = failing_state.stage if failing_state is not None else "unknown"
            _fail(ver_id, str(exc))
            log_event(
                ver_id,
                "failed",
                "pipeline",
                _elapsed_ms(_t0),
                "error",
                error_stage=failing_stage,
                error=str(exc),  # text is redacted by log_event
            )
            raise
