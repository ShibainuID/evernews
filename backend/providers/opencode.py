"""OpenCode local server research provider (T29): HANDOFF §9.6, §41.2.

Thin HTTP adapter over ``opencode serve`` (port 4096): creates one session
per task (``POST /session``) and sends the rendered prompt to the
``investigator`` agent (``POST /session/{id}/message``), authenticated with
HTTP Basic from ``OPENCODE_SERVER_URL`` / ``OPENCODE_SERVER_USERNAME`` /
``OPENCODE_SERVER_PASSWORD``. Payload shapes are pinned by tests and isolated
in the ``build_*_payload`` builders so a runtime API shape change (HANDOFF
§9.6: inspect the server's ``/doc`` OpenAPI spec) is a single localized edit.

``investigate`` delegates render/parse/repair/budget orchestration to
``backend.services.validation.investigator``; this module only talks HTTP.
Server/network failures propagate as exceptions; only the agent's unparseable
output after one repair becomes a valid ``status="insufficient"`` result.
"""

from typing import Any

import httpx

from backend.config import Settings
from backend.schemas.investigation import WebResearchResult, WebResearchTask
from backend.services.validation.investigator import investigate_task
from backend.utils.llm import StructuredOutputError

_DEFAULT_TIMEOUT_SEC = 300.0  # an 8-step agentic research run needs headroom


def build_create_session_payload(title: str) -> dict[str, Any]:
    """Exact ``POST /session`` body (shape pinned by tests)."""
    return {"title": title}


def build_message_payload(agent: str, text: str) -> dict[str, Any]:
    """Exact ``POST /session/{id}/message`` body (shape pinned by tests)."""
    return {"agent": agent, "parts": [{"type": "text", "text": text}]}


def extract_message_text(data: dict[str, Any]) -> str:
    """Concatenate the response's text parts; empty response is a server error."""
    parts: list[str] = []
    for part in data.get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    joined = "".join(parts).strip()
    if not joined:
        raise StructuredOutputError("opencode: message response contains no text part")
    return joined


class OpenCodeResearchProvider:
    """WebResearchProvider over the local OpenCode server (HANDOFF §9.6, §41.2)."""

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_sec: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        settings = Settings()
        self._url = url if url is not None else settings.opencode_server_url
        self._username = (
            username if username is not None else settings.opencode_server_username
        )
        self._password = (
            password if password is not None else settings.opencode_server_password
        )
        self._timeout_sec = (
            timeout_sec if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        )
        self._transport = transport

    async def investigate(self, task: WebResearchTask) -> WebResearchResult:
        """Fresh session per task; render/parse/repair/budgets via the service."""
        return await investigate_task(task, self)

    async def create_session(self, title: str) -> str:
        async with self._client() as client:
            response = await client.post("session", json=build_create_session_payload(title))
            response.raise_for_status()
            data = _json(response)
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise StructuredOutputError("opencode: session response missing id")
        return session_id

    async def send_message(
        self, session_id: str, text: str, agent: str = "investigator"
    ) -> str:
        async with self._client() as client:
            response = await client.post(
                f"session/{session_id}/message", json=build_message_payload(agent, text)
            )
            response.raise_for_status()
            data = _json(response)
        return extract_message_text(data)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._url,
            auth=(self._username, self._password),
            timeout=self._timeout_sec,
            transport=self._transport,
        )


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise StructuredOutputError(f"opencode: non-JSON response: {exc}") from exc
