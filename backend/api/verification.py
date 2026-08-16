"""T36: verification API endpoints (producer/consumer) + background wiring.

``POST /api/v1/verification`` is the trust boundary: the multipart upload is
validated synchronously by the T18 ingestor (bounded size, MP4 magic,
ffprobe duration) with a generated ``ver_<uuid4>`` id — a client filename can
never become a path, shell argument, or artifact key — and only then is the
pipeline scheduled via ``BackgroundTasks``. Pollers read the T35 state
store; the completed ``VerificationResult`` is served once, never fabricated.
``GET /{id}/debug`` is development-only and returns the verification's own
workdir artifacts plus JSON-safe summaries of the completed result; the T35
pipeline does not persist plan/bundle, so those are reported unavailable
rather than invented.
"""

import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from backend import state as state_module
from backend.config import Settings
from backend.schemas.result import VerificationResult
from backend.services import pipeline
from backend.services.ingestion.video_ingestor import (
    IngestionError,
    MediaProbeUnavailableError,
    new_verification_id,
    save_remote_video,
    save_upload,
)
from backend.services.validation.cache import query_cache
from backend.state import STATUS_COMPLETED, STATUS_FAILED, STATUS_PROCESSING

router = APIRouter()
logger = logging.getLogger(__name__)


def get_providers() -> pipeline.Providers:
    """Production provider bundle (T21 adapters, all lazy: no model load or
    network at construction). Tests override this dependency with fakes.

    The T39 demo query-cache singleton rides on the bundle: every production
    verification reuses cached provider results for identical queries/frames
    (24h TTL, process-local), making repeated demo runs fast and identical.
    """
    from backend.providers.google_vision import GoogleVisionProvider
    from backend.providers.luna import OpenCodeGoLunaProvider
    from backend.providers.opencode import OpenCodeResearchProvider
    from backend.providers.paddleocr import PaddleOCRProvider
    from backend.providers.serpapi import FallbackVisionProvider, SerpAPIVisionProvider
    from backend.providers.whisper import FasterWhisperSpeechProvider

    return pipeline.Providers(
        speech=FasterWhisperSpeechProvider(),
        ocr=PaddleOCRProvider(),
        luna=OpenCodeGoLunaProvider(),
        vision=FallbackVisionProvider(
            primary=GoogleVisionProvider(), fallback=SerpAPIVisionProvider()
        ),
        web_research=OpenCodeResearchProvider(),
        cache=query_cache,
    )


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _unknown(ver_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"unknown verification id {ver_id!r}")


def _mark_failed(ver_id: str, error: str) -> None:
    state_module.store.update(
        ver_id, status=STATUS_FAILED, stage="failed", progress=1.0, error=error
    )


@router.post("", status_code=202)
async def create_verification(
    request: Request,
    video: UploadFile | None = File(None),
    caption: str = Form(""),
    source_url: str | None = Form(None),
    video_url: str | None = Form(None),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    providers: pipeline.Providers = Depends(get_providers),
) -> dict[str, str]:
    """Accept a validated upload (file or remote URL) and schedule the pipeline."""
    settings = _settings(request)
    ver_id = new_verification_id()
    try:
        if video is not None:
            video_path = save_upload(video.file, ver_id, settings)
        elif video_url:
            if not settings.enable_url_input:
                # ENABLE_URL_INPUT also gates direct video URLs (design §10)
                raise HTTPException(
                    status_code=403,
                    detail=(
                        "video_url input is disabled: set ENABLE_URL_INPUT=true "
                        "to accept direct video URLs"
                    ),
                )
            video_path = await save_remote_video(video_url, ver_id, settings)
        else:
            raise HTTPException(
                status_code=400, detail="provide a video file or a video_url"
            )
    except IngestionError as exc:
        # trust-boundary reject: no state entry, no background job
        status_code = 503 if isinstance(exc, MediaProbeUnavailableError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    state_module.store.create(ver_id)
    verification_request = pipeline.VerificationRequest(
        video_path=video_path,
        caption=caption,
        # accepted but ignored unless the feature flag is on (design §10)
        source_url=source_url if settings.enable_url_input else None,
    )
    background_tasks.add_task(
        _run_background, ver_id, verification_request, providers, settings
    )
    return {"verification_id": ver_id, "status": STATUS_PROCESSING}


async def _run_background(
    ver_id: str,
    verification_request: pipeline.VerificationRequest,
    providers: pipeline.Providers,
    settings: Settings,
) -> None:
    """run_verification behind the response; it marks state failed itself."""
    try:
        await pipeline.run_verification(ver_id, verification_request, providers, settings)
    except Exception:  # noqa: BLE001 - state already reflects the failure
        logger.exception("verification %s failed", ver_id)


@router.get("/{ver_id}")
def get_status(ver_id: str, request: Request) -> dict:
    """Current state: status, stage, progress, error (T35 state store)."""
    state = state_module.store.get(ver_id)
    if state is None:
        raise _unknown(ver_id)
    return {
        "verification_id": state.ver_id,
        "status": state.status,
        "stage": state.stage,
        "progress": state.progress,
        "error": state.error,
    }


@router.get("/{ver_id}/result")
def get_result(ver_id: str) -> VerificationResult:
    """The completed ``VerificationResult``; never a partial/fabricated one."""
    state = state_module.store.get(ver_id)
    if state is None:
        raise _unknown(ver_id)
    if state.status != STATUS_COMPLETED or state.result is None:
        raise HTTPException(
            status_code=409,
            detail={
                "verification_id": state.ver_id,
                "status": state.status,
                "stage": state.stage,
                "progress": state.progress,
                "error": state.error,
                "message": "verification has not completed; no result is available",
            },
        )
    return state.result


@router.get("/{ver_id}/debug")
def get_debug(ver_id: str, request: Request) -> dict:
    """Development-only diagnostics: own workdir artifacts + result summaries."""
    if _settings(request).app_env != "development":
        raise HTTPException(status_code=404, detail="debug endpoint disabled")
    state = state_module.store.get(ver_id)
    if state is None:
        raise _unknown(ver_id)

    work_dir = Path(_settings(request).workdir) / ver_id
    artifacts = []
    if work_dir.is_dir():
        for path in sorted(work_dir.rglob("*")):
            if path.is_file():
                artifacts.append(
                    {
                        "name": str(path.relative_to(work_dir)),
                        "size_bytes": path.stat().st_size,
                        "modified_epoch": round(path.stat().st_mtime, 3),
                    }
                )

    result = state.result
    return {
        "verification_id": state.ver_id,
        "status": state.status,
        "stage": state.stage,
        "progress": state.progress,
        "artifacts": artifacts,
        "context": result.current_context.model_dump() if result is not None else None,
        "comparison": result.comparison.model_dump() if result is not None else None,
        "plan": state.plan,
        "bundle": state.bundle,
    }
