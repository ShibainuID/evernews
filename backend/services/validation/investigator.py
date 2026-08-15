"""Web research task runner over the OpenCode investigator agent (T29).

``investigate_task`` renders a ``WebResearchTask`` through
``prompts/investigator.txt`` and drives the OpenCode session flow (HANDOFF
§9.6: one fresh session per task via ``POST /session``, then ``POST
/session/{id}/message`` with ``agent=investigator``) through any client
exposing ``create_session`` / ``send_message`` (the HTTP adapter is
``backend.providers.opencode.OpenCodeResearchProvider``).

The agent output is parsed as a structured ``WebResearchResult`` with exactly
one schema-repair message (``utils/llm.parse_structured`` semantics), and the
per-task budget from HANDOFF §9.7 is enforced afterwards: reported
``searches_used`` / ``pages_fetched`` are clamped to the task caps, and an
over-budget result is downgraded to ``status="insufficient"`` with an
explicit ``unresolved`` note (max 8 agent steps is enforced by the agent
frontmatter ``steps: 8``, not observable here). Two evidence guards apply:

- conflicting evidence (``supports_question`` and ``contradicts_question``
  both present) forces ``status="mixed"`` with both sides preserved;
- a URL still listed in ``unresolved`` was never successfully fetched, so its
  ``relevant_excerpt`` is dropped — a search snippet can never masquerade as
  fetched-page evidence.

Server/network failures (HTTP errors, timeouts, empty responses) propagate
to the orchestrator (T32) for ``branch_status``; only an unparseable agent
output after the one repair attempt becomes a valid ``status="insufficient"``
result — never an exception.
"""

import json
import string
from pathlib import Path
from typing import Protocol

from backend.schemas.investigation import WebResearchResult, WebResearchTask
from backend.utils.llm import StructuredOutputError, parse_structured

AGENT = "investigator"
_MAX_STEPS = 8  # mirrored in .opencode/agents/investigator.md frontmatter

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "investigator.txt"


class OpenCodeClient(Protocol):
    """Minimal HTTP surface the orchestration needs from the adapter."""

    async def create_session(self, title: str) -> str: ...

    async def send_message(
        self, session_id: str, text: str, agent: str = AGENT
    ) -> str: ...


def render_investigation_prompt(task: WebResearchTask) -> str:
    """Render the task through ``prompts/investigator.txt`` ($-placeholders)."""
    template = string.Template(_PROMPT_PATH.read_text())
    return template.substitute(
        question=task.question,
        queries=json.dumps(task.queries, ensure_ascii=False),
        preferred_types=json.dumps(task.preferred_source_types, ensure_ascii=False),
        max_searches=task.max_searches,
        max_pages=task.max_pages,
    )


def _repair_message_text(raw: str, error: StructuredOutputError) -> str:
    """Schema-correction instruction for the single repair message."""
    return (
        "Your previous response failed structured-output validation.\n"
        f"Validation error: {error}\n"
        "Return only corrected JSON matching the requested schema.\n"
        f"Previous response:\n{raw}"
    )


def _insufficient_result(
    task: WebResearchTask,
    note: str,
    *,
    searches_used: int = 0,
    pages_fetched: int = 0,
) -> WebResearchResult:
    """A valid ``insufficient`` result — never an exception (HANDOFF §9.7)."""
    return WebResearchResult(
        task_id=task.task_id,
        question=task.question,
        status="insufficient",
        finding="No validated web evidence could be produced for this task.",
        evidence=[],
        unresolved=[note],
        searches_used=searches_used,
        pages_fetched=pages_fetched,
    )


def _enforce_budgets_and_guards(
    result: WebResearchResult, task: WebResearchTask
) -> WebResearchResult:
    """Budget caps (§9.7), mixed-status conflict rule, and the snippet guard."""
    supports = any(e.supports_question is True for e in result.evidence)
    contradicts = any(e.contradicts_question is True for e in result.evidence)
    if supports and contradicts:
        result.status = "mixed"  # conflicting evidence is preserved, both sides
    if result.searches_used > task.max_searches or result.pages_fetched > task.max_pages:
        result.status = "insufficient"
        result.unresolved.append(
            f"Research budget exceeded: {result.searches_used} searches / "
            f"{result.pages_fetched} pages used (max {task.max_searches} / "
            f"{task.max_pages})."
        )
    result.searches_used = min(result.searches_used, task.max_searches)
    result.pages_fetched = min(result.pages_fetched, task.max_pages)
    unresolved_urls = set(result.unresolved)
    for evidence in result.evidence:
        if evidence.url in unresolved_urls:
            # Never fetched (still unresolved) -> snippet text is not evidence.
            evidence.relevant_excerpt = None
    return result


async def investigate_task(
    task: WebResearchTask, client: OpenCodeClient
) -> WebResearchResult:
    """One fresh session per task; parse, one repair, budgets, guards."""
    session_id = await client.create_session(f"verification:{task.task_id}")
    raw = await client.send_message(
        session_id, render_investigation_prompt(task), agent=AGENT
    )
    try:
        result = parse_structured(raw, WebResearchResult)
    except StructuredOutputError as first_error:
        repaired_raw = await client.send_message(
            session_id, _repair_message_text(raw, first_error), agent=AGENT
        )
        try:
            result = parse_structured(repaired_raw, WebResearchResult)
        except StructuredOutputError as final_error:
            return _insufficient_result(
                task,
                "Investigator output could not be validated after one repair "
                f"attempt: {final_error}",
            )
    return _enforce_budgets_and_guards(result, task)
