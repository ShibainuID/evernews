"""Structured LLM output parsing with one repair attempt (HANDOFF §28).

``parse_structured`` validates raw model output against a Pydantic schema and
allows exactly one repair attempt before raising ``StructuredOutputError``.
"""

import pytest

from backend.schemas.context import SpeechExtraction
from backend.utils.llm import StructuredOutputError, parse_structured


def test_valid_raw_json_parses_to_model():
    raw = '{"transcript": "hello world", "language": "en", "confidence": 0.9}'

    result = parse_structured(raw, SpeechExtraction)

    assert isinstance(result, SpeechExtraction)
    assert result.transcript == "hello world"
    assert result.language == "en"
    assert result.confidence == 0.9


def test_valid_raw_json_with_defaults():
    result = parse_structured('{"transcript": "only text"}', SpeechExtraction)

    assert result.transcript == "only text"
    assert result.language is None
    assert result.segments == []
    assert result.confidence is None


def test_fenced_json_block_is_coerced():
    raw = 'Here is the result:\n```json\n{"transcript": "fenced", "language": "en"}\n```'

    result = parse_structured(raw, SpeechExtraction)

    assert result.transcript == "fenced"
    assert result.language == "en"


def test_prose_before_json_block_is_coerced():
    raw = 'The validation error says fix it: {"transcript": "prose", "language": "id"} trailing prose'

    result = parse_structured(raw, SpeechExtraction)

    assert result.transcript == "prose"
    assert result.language == "id"


def test_invalid_then_repair_returns_model():
    raw = '{"transcript": "repaired",}'  # trailing comma: invalid JSON

    def repair_fn(raw_input, first_error):
        return '{"transcript": "repaired", "language": "id"}'

    result = parse_structured(raw, SpeechExtraction, repair_fn)

    assert isinstance(result, SpeechExtraction)
    assert result.transcript == "repaired"
    assert result.language == "id"


def test_invalid_twice_raises_structured_output_error():
    calls = []

    def repair_fn(raw_input, first_error):
        calls.append(1)
        return "still not json"

    with pytest.raises(StructuredOutputError) as excinfo:
        parse_structured("not json", SpeechExtraction, repair_fn)

    assert "SpeechExtraction" in str(excinfo.value)
    assert len(calls) == 1


def test_invalid_without_repair_raises():
    with pytest.raises(StructuredOutputError):
        parse_structured("not json", SpeechExtraction)


def test_repair_called_exactly_once():
    calls = []

    def repair_fn(raw_input, first_error):
        calls.append(1)
        return '{"transcript": "fixed"}'

    result = parse_structured("not json", SpeechExtraction, repair_fn)

    assert result.transcript == "fixed"
    assert calls == [1]


def test_repair_exception_raises_structured_output_error():
    def repair_fn(raw_input, first_error):
        raise RuntimeError("repair model failed")

    with pytest.raises(StructuredOutputError) as excinfo:
        parse_structured("not json", SpeechExtraction, repair_fn)

    assert "repair model failed" in str(excinfo.value)


def test_schema_validation_violation_is_repairable():
    raw = '{"transcript": 42}'  # valid JSON, wrong type for schema

    def repair_fn(raw_input, first_error):
        return '{"transcript": "ok after repair"}'

    result = parse_structured(raw, SpeechExtraction, repair_fn)

    assert result.transcript == "ok after repair"


def test_schema_violation_twice_raises():
    def repair_fn(raw_input, first_error):
        return '{"transcript": 42}'  # still wrong

    with pytest.raises(StructuredOutputError):
        parse_structured('{"transcript": 42}', SpeechExtraction, repair_fn)
