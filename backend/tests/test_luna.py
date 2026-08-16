"""T26: OpenCodeGoLunaProvider tests (TDD red-green).

httpx MockTransport only — no network, no credentials. Pins the exact
Responses payload shape (HANDOFF §42) with the §28 low temperature, Bearer
auth, bounded 5xx/429 retry via ``utils/retry.py`` (max 3 attempts, i.e. two
retries, numeric Retry-After honored), the ``output_text`` ->
``output[*].content[*].text`` extraction order, and the exactly-one
schema-correction request before ``StructuredOutputError``.
"""

import asyncio
import base64
import json

import httpx
import pytest

from backend.config import Settings
from backend.providers.base import LunaProvider
from backend.providers.luna import OpenCodeGoLunaProvider, build_responses_payload
from backend.schemas.context import VisualObservation
from backend.utils.llm import StructuredOutputError

def _provider(transport: httpx.MockTransport | None = None) -> OpenCodeGoLunaProvider:
    return OpenCodeGoLunaProvider(
        api_key="test-key",
        base_url="https://example.test/v1",
        model="gpt-5.6-luna",
        timeout_sec=60,
        transport=transport,
    )


def _response(status: int, body: dict | None = None, retry_after: str | None = None):
    request = httpx.Request("POST", "https://example.test/v1/responses")
    headers = {"Retry-After": retry_after} if retry_after else {}
    if body is None:
        return httpx.Response(status, request=request, headers=headers)
    return httpx.Response(status, request=request, headers=headers, json=body)


def _scripted(responses: list[httpx.Response]):
    """MockTransport handler serving canned responses; records each request."""
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses.pop(0)

    return handler, calls


def _fake_sleep(record: list[float]):
    async def sleep(delay: float) -> None:
        record.append(delay)

    return sleep


def _luna_json(scene_type: str) -> dict:
    return {"output_text": json.dumps({"scene_type": scene_type})}


# --- contract ---


def test_provider_satisfies_luna_provider_protocol():
    assert isinstance(_provider(), LunaProvider)


# --- request shape, auth, and response parsing ---


async def test_structured_sends_exact_responses_payload_with_bearer_auth():
    handler, calls = _scripted([_response(200, _luna_json("urban street"))])
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert isinstance(result, VisualObservation)
    assert result.scene_type == "urban street"
    (request,) = calls
    assert request.method == "POST"
    assert request.url.path == "/v1/responses"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.content) == {
        "model": "gpt-5.6-luna",
        "temperature": 0.1,  # HANDOFF §28: low temperature for structured output
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "describe"}]}],
    }


async def test_image_paths_become_base64_input_image_parts(tmp_path):
    image = tmp_path / "keyframe.jpg"
    image.write_bytes(b"fake-jpeg-bytes-123")
    handler, calls = _scripted([_response(200, _luna_json("market"))])
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("look", VisualObservation, image_paths=[str(image)])

    assert result.scene_type == "market"
    (request,) = calls
    expected_url = "data:image/jpeg;base64," + base64.b64encode(b"fake-jpeg-bytes-123").decode()
    assert json.loads(request.content) == {
        "model": "gpt-5.6-luna",
        "temperature": 0.1,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "look"},
                    {"type": "input_image", "image_url": expected_url},
                ],
            }
        ],
    }


def test_build_responses_payload_isolates_request_shape(tmp_path):
    assert build_responses_payload("gpt-5.6-luna", "hi") == {
        "model": "gpt-5.6-luna",
        "temperature": 0.1,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
    }

    image = tmp_path / "k.jpg"
    image.write_bytes(b"\xff\xd8raw")
    payload = build_responses_payload("gpt-5.6-luna", "hi", [str(image), str(image)])
    parts = payload["input"][0]["content"]
    expected_url = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8raw").decode()
    assert parts == [
        {"type": "input_text", "text": "hi"},
        {"type": "input_image", "image_url": expected_url},
        {"type": "input_image", "image_url": expected_url},
    ]


async def test_falls_back_to_output_content_text_when_output_text_missing():
    handler, _ = _scripted(
        [_response(200, {"output": [{"content": [{"text": '{"scene_type": "harbor"}'}]}]})]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert result.scene_type == "harbor"


async def test_output_text_takes_precedence_over_output_content():
    handler, _ = _scripted(
        [
            _response(
                200,
                {
                    "output_text": '{"scene_type": "preferred"}',
                    "output": [{"content": [{"text": '{"scene_type": "fallback"}'}]}],
                },
            )
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert result.scene_type == "preferred"


# --- missing output text is a provider error, not valid empty output ---


async def test_missing_output_text_is_provider_error_with_one_repair():
    handler, calls = _scripted([_response(200, {}), _response(200, {})])
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(StructuredOutputError, match="no output text"):
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 2  # initial + exactly one schema-correction request


# --- bounded retry via utils/retry.py ---


async def test_5xx_retries_twice_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted(
        [_response(503), _response(503), _response(200, _luna_json("street"))]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert result.scene_type == "street"
    assert len(calls) == 3  # max 3 attempts: one initial call plus two retries
    assert sleeps == [0.5, 1.0]  # base_delay * 2**attempt


async def test_5xx_exhausts_three_attempts_then_raises(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted([_response(500), _response(500), _response(500)])
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 3  # bounded: never a fourth attempt
    assert sleeps == [0.5, 1.0]


async def test_429_honors_numeric_retry_after_within_three_attempts(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted(
        [_response(429, retry_after="7"), _response(429, retry_after="7"), _response(429, retry_after="7")]
    )
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 3
    assert sleeps == [7.0, 7.0]  # Retry-After honored on every inter-attempt sleep


async def test_429_recovers_on_retry(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(asyncio, "sleep", _fake_sleep(sleeps))
    handler, calls = _scripted([_response(429, retry_after="3"), _response(200, _luna_json("plaza"))])
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert result.scene_type == "plaza"
    assert len(calls) == 2
    assert sleeps == [3.0]


async def test_non_retryable_4xx_propagates_immediately():
    handler, calls = _scripted([_response(400)])
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 1


# --- exactly one schema-correction request ---


async def test_non_json_output_repairs_once_then_raises():
    handler, calls = _scripted(
        [
            _response(200, {"output_text": "not json at all"}),
            _response(200, {"output_text": "still not json"}),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(StructuredOutputError) as excinfo:
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 2  # exactly one repair request, no more
    assert "VisualObservation" in str(excinfo.value)


async def test_invalid_schema_repair_request_recovers():
    handler, calls = _scripted(
        [
            _response(200, {"output_text": '{"scene_type": 42}'}),  # valid JSON, wrong type
            _response(200, _luna_json("fixed")),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    result = await provider.structured("describe", VisualObservation)

    assert result.scene_type == "fixed"
    assert len(calls) == 2
    repair_body = json.loads(calls[1].content)
    repair_text = repair_body["input"][0]["content"][0]["text"]
    assert "corrected JSON" in repair_text  # a schema-correction instruction, not the prompt
    assert '{"scene_type": 42}' in repair_text  # previous output included for context


async def test_repair_result_still_invalid_raises():
    handler, calls = _scripted(
        [
            _response(200, {"output_text": '{"scene_type": 42}'}),
            _response(200, {"output_text": '{"scene_type": 43}'}),
        ]
    )
    provider = _provider(httpx.MockTransport(handler))

    with pytest.raises(StructuredOutputError):
        await provider.structured("describe", VisualObservation)

    assert len(calls) == 2


# --- settings wiring ---


def test_constructor_defaults_from_settings(monkeypatch):
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "env-key")
    monkeypatch.setenv("OPENCODE_GO_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LUNA_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LUNA_TIMEOUT_SEC", "30")

    provider = OpenCodeGoLunaProvider()

    assert provider._api_key == "env-key"
    assert provider._base_url == "https://env.example/v1"
    assert provider._model == "gpt-5.6-luna"
    assert provider._timeout_sec == 30


def test_timeout_defaults_to_60_seconds(monkeypatch):
    monkeypatch.delenv("LUNA_TIMEOUT_SEC", raising=False)
    assert Settings().luna_timeout_sec == 60
