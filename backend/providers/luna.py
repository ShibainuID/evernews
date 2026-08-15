"""OpenCode Go Responses provider (T26): ``LunaProvider`` adapter (HANDOFF §5.3, §41).

Structured VLM calls go to ``POST {base_url}/responses`` with
``model=gpt-5.6-luna`` — the Responses API, not /chat/completions (HANDOFF
§41.1.A). The request payload shape lives in one isolated function
(``build_responses_payload``) so a future API shape change (HANDOFF §42) is a
single localized edit; the ``LunaProvider.structured`` contract is unchanged.

Retries: at most 2 attempts for 5xx/429 via ``utils/retry.py`` (429 honors a
numeric ``Retry-After``). Response text comes from ``output_text`` with a
fallback over ``output[*].content[*].text``; missing text is a provider
error, never a valid empty output. Non-JSON / schema-invalid output triggers
exactly one schema-correction request, then ``StructuredOutputError``.
"""

import base64
from pathlib import Path
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from backend.config import Settings
from backend.utils.llm import StructuredOutputError, parse_structured
from backend.utils.retry import retry_async

T = TypeVar("T", bound=BaseModel)

_IMAGE_MIME = "image/jpeg"  # keyframes are ffmpeg-extracted JPEGs
_MAX_ATTEMPTS = 2  # bounded: one initial call plus one retry
_BASE_DELAY = 0.5


def _retryable(exc: Exception) -> bool:
    """Retry 5xx and 429; Retry-After delay selection lives in utils/retry.py."""
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code >= 500 or exc.response.status_code == 429
    )


def build_responses_payload(
    model: str, prompt: str, image_paths: list[str] | None = None
) -> dict[str, Any]:
    """Exact OpenCode Go Responses request body (shape pinned by tests).

    ``image_paths`` become base64 ``input_image`` parts
    (``data:image/jpeg;base64,<...>``) appended after the ``input_text`` part.
    """
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for image_path in image_paths or []:
        encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        content.append(
            {"type": "input_image", "image_url": f"data:{_IMAGE_MIME};base64,{encoded}"}
        )
    return {"model": model, "input": [{"role": "user", "content": content}]}


def _extract_output_text(data: dict[str, Any]) -> str:
    """``output_text`` first, else ``output[*].content[*].text``; empty is an error."""
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text
    parts: list[str] = []
    for item in data.get("output") or []:
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    joined = "".join(parts).strip()
    if not joined:
        raise StructuredOutputError("luna: response contains no output text")
    return joined


def _repair_prompt(raw: str, error: StructuredOutputError) -> str:
    """Schema-correction instruction for the single repair request."""
    return (
        "Your previous response failed structured-output validation.\n"
        f"Validation error: {error}\n"
        "Return only corrected JSON matching the requested schema.\n"
        f"Previous response:\n{raw}"
    )


class OpenCodeGoLunaProvider:
    """Luna VLM adapter over the OpenCode Go Responses API (HANDOFF §5.3)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_sec: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        settings = Settings()
        self._api_key = api_key if api_key is not None else settings.opencode_go_api_key
        self._base_url = base_url if base_url is not None else settings.opencode_go_base_url
        self._model = model if model is not None else settings.luna_model
        self._timeout_sec = (
            timeout_sec if timeout_sec is not None else settings.luna_timeout_sec
        )
        self._transport = transport

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        image_paths: list[str] | None = None,
    ) -> T:
        """One structured call; invalid output gets exactly one repair request."""
        raw = ""
        try:
            raw = await self._call(prompt, image_paths)
            return parse_structured(raw, schema)
        except StructuredOutputError as exc:
            repaired = await self._call(_repair_prompt(raw, exc), image_paths)
            return parse_structured(repaired, schema)

    async def _call(self, prompt: str, image_paths: list[str] | None) -> str:
        payload = build_responses_payload(self._model, prompt, image_paths)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_sec,
            transport=self._transport,
        ) as client:
            data = await retry_async(
                lambda: self._post(client, payload),
                attempts=_MAX_ATTEMPTS,
                base_delay=_BASE_DELAY,
                retry_for=_retryable,
            )
        return _extract_output_text(data)

    async def _post(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await client.post("responses", json=payload)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise StructuredOutputError(f"luna: non-JSON response: {exc}") from exc
