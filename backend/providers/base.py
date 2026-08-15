"""Provider adapter contracts: HANDOFF §5.1 (speech), §5.2 (OCR), §5.3
(Luna), §9.6 (web research), §10.4 (vision).

Provider-agnostic ``Protocol`` definitions only — no external SDK imports.
Fakes and real adapters satisfy them via ``@runtime_checkable`` so contract
tests can ``isinstance`` them. ``LunaProvider.structured`` is generic: the
return type follows the requested Pydantic schema.
"""

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from backend.schemas.context import OCRHit, SpeechExtraction
from backend.schemas.investigation import (
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class SpeechProvider(Protocol):
    """Speech-to-text (HANDOFF §5.1). No audio → empty ``SpeechExtraction``."""

    async def transcribe(self, audio_path: str) -> SpeechExtraction: ...


@runtime_checkable
class OCRExtractor(Protocol):
    """Local OCR over frame images (HANDOFF §5.2). Empty list is valid."""

    def extract(self, frame_paths: list[str]) -> list[OCRHit]: ...


@runtime_checkable
class LunaProvider(Protocol):
    """Structured VLM calls (HANDOFF §5.3). Returns the requested schema."""

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        image_paths: list[str] | None = None,
    ) -> T: ...


@runtime_checkable
class VisionProvider(Protocol):
    """Google Cloud Vision web detection (HANDOFF §10.4)."""

    def web_detection(self, image_bytes: bytes) -> list[VisualWebCandidate]: ...


@runtime_checkable
class WebResearchProvider(Protocol):
    """OpenCode investigator session (HANDOFF §9.6)."""

    async def investigate(self, task: WebResearchTask) -> WebResearchResult: ...
