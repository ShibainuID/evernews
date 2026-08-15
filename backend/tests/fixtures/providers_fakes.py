"""Scripted provider fakes for contract and downstream tests (T21).

Each fake consumes a script of outputs: ``Exception`` entries are raised as
provider failures, everything else is returned as scripted. The Luna fake
takes raw JSON strings that flow through ``parse_structured``, so it enforces
the requested schema the same way a real adapter would.
"""

from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from backend.schemas.context import OCRHit, SpeechExtraction
from backend.schemas.investigation import (
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)
from backend.utils.llm import parse_structured

T = TypeVar("T", bound=BaseModel)


def _next_scripted(script: list[Any], name: str) -> Any:
    if not script:
        raise AssertionError(f"{name} script exhausted")
    item = script.pop(0)
    if isinstance(item, Exception):
        raise item
    return item


class FakeSpeechProvider:
    """Scripted ``SpeechExtraction`` results or exceptions (HANDOFF §5.1)."""

    def __init__(self, script: list[SpeechExtraction | Exception]):
        self._script = list(script)

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        return _next_scripted(self._script, "FakeSpeechProvider")


class FakeOCRExtractor:
    """Scripted ``list[OCRHit]`` results or exceptions (HANDOFF §5.2)."""

    def __init__(self, script: list[list[OCRHit] | Exception]):
        self._script = list(script)

    def extract(self, frame_paths: list[str]) -> list[OCRHit]:
        return _next_scripted(self._script, "FakeOCRExtractor")


class FakeLunaProvider:
    """Scripted raw JSON responses validated through ``parse_structured``.

    ``repair_fn`` is optional: scripted raw that validates needs no repair;
    invalid raw with no repair raises ``StructuredOutputError``.
    """

    def __init__(
        self,
        script: list[str | Exception],
        repair_fn: Callable[[str, ValidationError], str] | None = None,
    ):
        self._script = list(script)
        self._repair_fn = repair_fn

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        image_paths: list[str] | None = None,
    ) -> T:
        item = _next_scripted(self._script, "FakeLunaProvider")
        if isinstance(item, Exception):
            raise item
        return parse_structured(item, schema, self._repair_fn)


class FakeVisionProvider:
    """Scripted ``list[VisualWebCandidate]`` results or exceptions (§10.4)."""

    def __init__(self, script: list[list[VisualWebCandidate] | Exception]):
        self._script = list(script)

    def web_detection(self, image_bytes: bytes) -> list[VisualWebCandidate]:
        return _next_scripted(self._script, "FakeVisionProvider")


class FakeWebResearchProvider:
    """Scripted ``WebResearchResult`` results or exceptions (HANDOFF §9.6)."""

    def __init__(self, script: list[WebResearchResult | Exception]):
        self._script = list(script)

    async def investigate(self, task: WebResearchTask) -> WebResearchResult:
        return _next_scripted(self._script, "FakeWebResearchProvider")
