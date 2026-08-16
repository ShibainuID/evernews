"""T29: OpenCode investigator branch tests (TDD red-green).

httpx MockTransport only — no network, no credentials. Pins the exact
``POST /session`` then ``POST /session/:id/message`` flow (HANDOFF §9.6),
HTTP Basic auth from ``OPENCODE_SERVER_USERNAME/PASSWORD``, one fresh session
per task (isolation), the ``agent=investigator`` message body, exactly one
schema-repair message before ``status="insufficient"`` (never a crash),
per-task budget enforcement (max 4 searches / 6 pages, HANDOFF §9.7),
conflict → ``mixed`` with both sides kept, blocked webfetch URLs retained in
``unresolved`` with snippets never promoted to ``relevant_excerpt``, and the
static assets: ``prompts/investigator.txt`` (no final-verdict bias),
``.opencode/agents/investigator.md`` (§9.3 frontmatter + rules), and the
schema-valid ``opencode.json`` (no secrets).
"""

import base64
import json
from pathlib import Path
from typing import Literal

import httpx
import pytest

from backend.providers.base import WebResearchProvider
from backend.providers.opencode import OpenCodeResearchProvider
from backend.services.validation.investigator import render_investigation_prompt
from backend.schemas.investigation import (
    WebResearchResult,
    WebResearchTask,
    WebSourceEvidence,
)
from backend.utils.llm import StructuredOutputError

ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = ROOT / "backend" / "prompts" / "investigator.txt"
AGENT_PATH = ROOT / ".opencode" / "agents" / "investigator.md"
CONFIG_PATH = ROOT / "opencode.json"

BASE_URL = "http://127.0.0.1:4096"


def _task(task_id: str = "web_01") -> WebResearchTask:
    return WebResearchTask(
        task_id=task_id,
        question="Did flooding occur in Jakarta on 2026-08-15?",
        queries=["Jakarta flood 2026"],
        preferred_source_types=["news"],
    )


def _provider(transport: httpx.AsyncBaseTransport) -> OpenCodeResearchProvider:
    return OpenCodeResearchProvider(
        url=BASE_URL,
        username="opencode",
        password="pw",
        timeout_sec=30,
        transport=transport,
    )


def _result_json(
    status: Literal["supported", "contradicted", "mixed", "insufficient"] = "supported",
    searches_used: int = 2,
    pages_fetched: int = 1,
    evidence: list[WebSourceEvidence] | None = None,
    unresolved: list[str] | None = None,
) -> str:
    result = WebResearchResult(
        task_id="web_01",
        question="Did flooding occur in Jakarta on 2026-08-15?",
        status=status,
        finding="A local news article confirms the flood.",
        evidence=evidence
        if evidence is not None
        else [
            WebSourceEvidence(
                evidence_id="ev_1",
                url="https://example.com/flood",
                retrieved_at="2026-08-15T10:00:00Z",
                relevant_excerpt="Flooding hit Jakarta on 2026-08-15.",
            )
        ],
        unresolved=[] if unresolved is None else unresolved,
        searches_used=searches_used,
        pages_fetched=pages_fetched,
    )
    return result.model_dump_json()


def _session_response(session_id: str = "sess_1") -> dict:
    return {"id": session_id, "title": f"verification:{session_id}"}


def _message_response(text: str) -> dict:
    return {"info": {"id": "msg_1"}, "parts": [{"type": "text", "text": text}]}


def _response(status: int, body: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", f"{BASE_URL}/session")
    if body is None:
        return httpx.Response(status, request=request)
    return httpx.Response(status, request=request, json=body)


def _scripted(responses: list[httpx.Response]):
    """MockTransport handler serving canned responses; records each request."""
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    return handler, calls


def _evidence(url: str, excerpt: str | None, evidence_id: str) -> WebSourceEvidence:
    return WebSourceEvidence(
        evidence_id=evidence_id,
        url=url,
        retrieved_at="2026-08-15T10:00:00Z",
        relevant_excerpt=excerpt,
    )


# --- contract ---


def test_provider_satisfies_web_research_provider_protocol():
    handler, _ = _scripted([])
    assert isinstance(_provider(httpx.MockTransport(handler)), WebResearchProvider)


# --- session flow, auth, and message shape ---


async def test_investigate_creates_fresh_session_and_sends_message_with_basic_auth():
    handler, calls = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json())),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert isinstance(result, WebResearchResult)
    assert result.status == "supported"
    assert result.evidence[0].url == "https://example.com/flood"
    assert len(calls) == 2
    session_call, message_call = calls
    assert session_call.method == "POST"
    assert session_call.url.path == "/session"
    assert json.loads(session_call.content) == {"title": "verification:web_01"}
    assert message_call.url.path == "/session/sess_1/message"
    body = json.loads(message_call.content)
    assert body["agent"] == "investigator"
    assert body["parts"][0]["type"] == "text"
    assert "Did flooding occur in Jakarta" in body["parts"][0]["text"]
    expected_auth = "Basic " + base64.b64encode(b"opencode:pw").decode()
    for call in calls:
        assert call.headers["authorization"] == expected_auth


async def test_investigate_creates_new_session_per_task_never_reuses():
    handler, calls = _scripted(
        [
            _response(200, _session_response("sess_a")),
            _response(200, _message_response(_result_json())),
            _response(200, _session_response("sess_b")),
            _response(200, _message_response(_result_json())),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    await provider.investigate(_task(task_id="web_01"))
    await provider.investigate(_task(task_id="web_02"))

    assert len(calls) == 4
    session_calls = calls[::2]
    assert [call.url.path for call in session_calls] == ["/session", "/session"]
    assert json.loads(session_calls[0].content)["title"] == "verification:web_01"
    assert json.loads(session_calls[1].content)["title"] == "verification:web_02"
    assert calls[1].url.path == "/session/sess_a/message"
    assert calls[3].url.path == "/session/sess_b/message"


# --- exactly one schema repair, then insufficient (never a crash) ---


async def test_malformed_json_repairs_exactly_once_then_insufficient():
    handler, calls = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response("not json at all")),
            _response(200, _message_response("still not json")),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "insufficient"
    assert result.evidence == []
    assert result.unresolved  # explicit unresolved note, not an empty list
    assert len(calls) == 3  # session + initial message + exactly one repair
    repair_body = json.loads(calls[2].content)
    repair_text = repair_body["parts"][0]["text"]
    assert "corrected JSON" in repair_text  # a schema-correction instruction
    assert "not json at all" in repair_text  # failed output included for context
    assert repair_body["agent"] == "investigator"
    assert calls[2].url.path == "/session/sess_1/message"  # same session, not a new one


async def test_repair_recovers_valid_result():
    handler, calls = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response('{"status": "not-a-valid-status"}')),
            _response(200, _message_response(_result_json())),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "supported"
    assert len(calls) == 3  # exactly one repair message, no more


# --- budgets: agent-driven exhaustion passes through; over-budget is forced ---


async def test_agent_reported_budget_exhaustion_passes_through():
    unresolved = ["No sufficiently direct source found within search budget"]
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(
                200,
                _message_response(_result_json(status="insufficient", searches_used=2, pages_fetched=3, unresolved=unresolved)),
            ),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "insufficient"
    assert result.unresolved == unresolved
    assert result.searches_used == 2  # at budget, unchanged
    assert result.pages_fetched == 3  # at budget, unchanged


async def test_over_budget_searches_force_insufficient_and_clamp():
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json(searches_used=7))),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "insufficient"  # supported claim is not trusted over budget
    assert any("budget" in note.lower() for note in result.unresolved)
    assert result.searches_used == 2  # clamped to the task cap
    assert result.pages_fetched == 1


async def test_over_budget_pages_force_insufficient_and_clamp():
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json(status="mixed", pages_fetched=9))),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "insufficient"  # budget violation wins over mixed
    assert result.pages_fetched == 3  # clamped to the task cap


async def test_within_budget_counters_reported_unchanged():
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json(searches_used=1, pages_fetched=2))),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "supported"
    assert result.searches_used == 1
    assert result.pages_fetched == 2


# --- conflicting evidence: mixed with both sides preserved ---


async def test_conflicting_evidence_forces_mixed_keeping_both_sides():
    evidence = [
        _evidence("https://example.com/supports", "Flooding occurred.", "ev_1"),
        _evidence("https://example.com/contradicts", "No flooding occurred.", "ev_2"),
    ]
    evidence[0].supports_question = True
    evidence[1].contradicts_question = True
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json(status="supported", evidence=evidence))),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "mixed"  # agent claimed supported; conflict forces mixed
    assert {e.url for e in result.evidence} == {
        "https://example.com/supports",
        "https://example.com/contradicts",
    }


async def test_agent_mixed_status_preserves_both_sides_verbatim():
    evidence = [
        _evidence("https://example.com/supports", "Flooding occurred.", "ev_1"),
        _evidence("https://example.com/contradicts", "No flooding occurred.", "ev_2"),
    ]
    evidence[0].supports_question = True
    evidence[1].contradicts_question = True
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(200, _message_response(_result_json(status="mixed", evidence=evidence))),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert result.status == "mixed"
    assert len(result.evidence) == 2  # both sides retained
    assert result.evidence[0].relevant_excerpt == "Flooding occurred."
    assert result.evidence[1].relevant_excerpt == "No flooding occurred."


# --- blocked webfetch: URL stays unresolved; snippet never becomes excerpt ---


async def test_blocked_webfetch_url_stays_unresolved_and_excerpt_never_used():
    blocked_url = "https://blocked.example.com/page"
    evidence = [
        _evidence(blocked_url, "Snippet text from a search result.", "ev_blocked"),
        _evidence("https://example.com/alternative", "Fetched article text.", "ev_alt"),
    ]
    handler, _ = _scripted(
        [
            _response(200, _session_response()),
            _response(
                200,
                _message_response(
                    _result_json(evidence=evidence, unresolved=[blocked_url])
                ),
            ),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.investigate(_task())

    assert blocked_url in result.unresolved  # retained for downstream visibility
    by_url = {e.url: e for e in result.evidence}
    assert by_url[blocked_url].relevant_excerpt is None  # snippet demoted, not quoted
    assert by_url["https://example.com/alternative"].relevant_excerpt == "Fetched article text."


# --- server/session failures propagate; they are not insufficient results ---


async def test_session_http_error_propagates_not_insufficient():
    handler, _ = _scripted([_response(500)])
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.investigate(_task())


async def test_message_http_error_propagates_not_insufficient():
    handler, _ = _scripted(
        [_response(200, _session_response()), _response(503)]
    )
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.investigate(_task())


async def test_empty_message_response_is_provider_error():
    handler, _ = _scripted(
        [_response(200, _session_response()), _response(200, {"info": {"id": "m"}, "parts": []})]
    )
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(StructuredOutputError, match="no text part"):
        await provider.investigate(_task())


# --- prompt rendering and static assets ---


def test_render_prompt_includes_task_details():
    prompt = render_investigation_prompt(_task())

    assert "Did flooding occur in Jakarta on 2026-08-15?" in prompt
    assert "Jakarta flood 2026" in prompt
    assert '["news"]' in prompt
    assert "2" in prompt and "3" in prompt  # search/page budgets rendered


def test_investigator_prompt_has_no_final_verdict_bias():
    text = PROMPT_PATH.read_text()

    assert "Do not determine whether the video itself is fake or real" in text
    assert "Do not infer that the uploaded footage depicts an event" in text
    assert "Search query snippets are not evidence" in text
    assert "Return structured JSON only" in text
    assert "untrusted evidence text" in text  # HANDOFF §31.1 injection guard
    assert "4" in text  # max agent steps communicated


def test_agent_file_frontmatter_and_rules_verbatim():
    text = AGENT_PATH.read_text()

    assert "mode: subagent" in text
    assert "temperature: 0.1" in text
    assert "steps: 4" in text
    assert "websearch: allow" in text
    assert "webfetch: allow" in text
    assert "edit: deny" in text
    assert "bash: deny" in text
    # §9.3 rules verbatim
    assert "Search query snippets are not evidence until the page is retrieved/read." in text
    assert "Record conflicting evidence instead of hiding it." in text
    assert "Do not determine whether the video itself is fake or real." in text
    assert "Every factual finding must point to one or more source URLs." in text
    assert "Return structured JSON only." in text


def test_opencode_json_is_schema_valid_and_enables_tools_without_secrets():
    config = json.loads(CONFIG_PATH.read_text())

    assert config["$schema"] == "https://opencode.ai/config.json"
    assert config["tools"]["websearch"] is True
    assert config["tools"]["webfetch"] is True

    def _contains_secret_key(value):
        if isinstance(value, dict):
            return any(
                _contains_secret_key(v)
                or any(word in str(k).lower() for word in ("password", "secret", "api_key", "token"))
                for k, v in value.items()
            )
        return False

    assert not _contains_secret_key(config)


# --- settings wiring ---


def test_constructor_defaults_from_settings(monkeypatch):
    monkeypatch.setenv("OPENCODE_SERVER_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("OPENCODE_SERVER_USERNAME", "env-user")
    monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "env-pw")

    provider = OpenCodeResearchProvider()

    assert provider._url == "http://127.0.0.1:9999"
    assert provider._username == "env-user"
    assert provider._password == "env-pw"
