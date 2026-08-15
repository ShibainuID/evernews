"""Structured LLM output parsing with one repair attempt (HANDOFF §28).

``parse_structured`` validates raw model output against a Pydantic schema.
On the first ``ValidationError`` it may run ``repair_fn`` exactly once; if
the repaired output still fails, or ``repair_fn`` itself raises, it raises
``StructuredOutputError``. The error message carries the validation errors
but no probability/LLM label.
"""

from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(ValueError):
    """LLM output did not match the requested schema after one repair attempt."""


def parse_structured(
    raw: str,
    schema: type[T],
    repair_fn: Callable[[str, ValidationError], str] | None = None,
) -> T:
    """Parse ``raw`` as ``schema``, allowing exactly one ``repair_fn`` attempt.

    ``repair_fn`` receives the original raw string and the first validation
    exception and must return one repaired JSON string.
    """
    try:
        return schema.model_validate_json(raw)
    except ValidationError as first_error:
        if repair_fn is None:
            raise StructuredOutputError(
                f"{schema.__name__}: invalid structured output, no repair configured: {first_error}"
            ) from first_error
        try:
            repaired = repair_fn(raw, first_error)
        except Exception as repair_error:
            raise StructuredOutputError(
                f"{schema.__name__}: repair failed: {repair_error}"
            ) from repair_error
        try:
            return schema.model_validate_json(repaired)
        except ValidationError as final_error:
            raise StructuredOutputError(
                f"{schema.__name__}: structured output invalid after repair: {final_error}"
            ) from final_error
