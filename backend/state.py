"""In-memory per-verification state store (T35): HANDOFF §22.2/§23.

The hackathon-simplest background-job state (HANDOFF §23): one
lock-protected dict, no external dependency. The API layer creates the
entry and polls ``store.get``; ``run_verification`` drives ``update``
calls through the HANDOFF §22.2 stage names.
"""

from dataclasses import dataclass
from threading import Lock
from typing import Any

from backend.schemas.result import VerificationResult

# HANDOFF §22.2 suggested stages, in pipeline order (``completed``/``failed``
# are terminal stages; ``status`` is the coarser lifecycle field).
STAGES: tuple[str, ...] = (
    "queued",
    "preprocessing",
    "extracting_context",
    "planning_investigation",
    "fact_check_search",
    "web_research",
    "visual_source_search",
    "synthesizing_evidence",
    "comparing_context",
    "completed",
    "failed",
)

# Status lifecycle: processing -> completed | failed (HANDOFF §22.1).
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class VerificationState:
    """One verification's observable state (HANDOFF §22.2)."""

    ver_id: str
    status: str = STATUS_PROCESSING
    stage: str = "queued"
    progress: float = 0.0
    error: str | None = None
    result: VerificationResult | None = None
    # T36 debug payloads: JSON-safe dumps, None until their stage produced them
    plan: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None


class VerificationStateStore:
    """Lock-protected ``{ver_id: VerificationState}`` map.

    ``create`` is idempotent so both the API layer and the pipeline can
    call it safely; ``update`` silently no-ops for unknown ids (the entry
    is always created before the run starts).
    """

    def __init__(self) -> None:
        self._states: dict[str, VerificationState] = {}
        self._lock = Lock()

    def create(self, ver_id: str) -> VerificationState:
        with self._lock:
            existing = self._states.get(ver_id)
            if existing is not None:
                return existing
            state = VerificationState(ver_id=ver_id)
            self._states[ver_id] = state
            return state

    def get(self, ver_id: str) -> VerificationState | None:
        with self._lock:
            return self._states.get(ver_id)

    def update(self, ver_id: str, **changes: Any) -> None:
        with self._lock:
            state = self._states.get(ver_id)
            if state is None:
                return
            for name, value in changes.items():
                setattr(state, name, value)

    def reset(self) -> None:
        with self._lock:
            self._states.clear()


# Module singleton: the API (T36) reads this; the pipeline writes it. Tests
# monkeypatch the ``store`` attribute, which pipeline.py looks up per call.
store = VerificationStateStore()
