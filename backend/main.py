"""FastAPI app bootstrap.

``create_app()`` is the app factory used by uvicorn (module-level ``app``)
and by TestClient; both get an isolated ``Settings`` via ``app.state.settings``.
"""

from contextlib import asynccontextmanager
import shutil
import time
from pathlib import Path

from fastapi import FastAPI

from backend.api.health import router
from backend.api.verification import router as verification_router
from backend.config import Settings

RETENTION_WINDOW_SEC = 24 * 60 * 60  # stale work/{ver_id}/ dirs older than this are removed on startup


def create_app() -> FastAPI:
    settings = Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        workdir = Path(settings.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - RETENTION_WINDOW_SEC
        for child in workdir.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass  # ponytail: dir raced away mid-scan; retried next startup
        yield

    app = FastAPI(title="AI Media Source Tracing", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(router)
    app.include_router(verification_router, prefix="/api/v1/verification")
    return app


app = create_app()
