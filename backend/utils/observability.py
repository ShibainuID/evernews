"""T37: structured per-stage observability — one stdlib logging seam.

``log_event`` is the public producer contract (HANDOFF §32):
``log_event(ver_id, stage, provider, latency_ms, status, **extra)``. Every
event is a JSON line carrying ``verification_id``, ``stage``, ``provider``,
``latency_ms`` (numeric, clamped non-negative) and ``status``; extras carry
only safe counts/identifiers. Sensitive extras (``api_key``, ``password``,
``token``, ``secret``, ``authorization``, ...) are dropped deterministically
and secret-shaped tokens (``sk-*``, ``Bearer *``, AWS ``AKIA*``) inside
string values are redacted, so untrusted provider error text is never
emitted verbatim.

``verification_scope`` (a ``contextvars`` task-local, never global mutable
state) makes every backend log record emitted inside the scope carry the
current ``verification_id`` via a wrapped ``LogRecord`` factory — explicit
``log_event`` arguments stay authoritative; the scope only fills records that
carry no id.
"""

import contextlib
import contextvars
import json
import logging
import re
from typing import Any, Iterator

_LOGGER = logging.getLogger("backend.observability")

# Keys whose value is never emitted (matched case-insensitively after
# normalizing "-" to "_").
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "credential",
)

# Secret-shaped tokens inside otherwise-untrusted text (provider errors).
_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{6,}|AKIA[0-9A-Z]{16}")

# Sensitive key/value pairs inside otherwise-untrusted text (F37-1): a
# sensitive key (case-insensitive; `_`/`-`/nothing separated, so compound
# keys like ``auth_token=`` are covered via the lookbehind) followed by
# ``=`` or ``:`` and a bounded non-whitespace value; surrounding text is
# preserved. Applied after token redaction so ``Authorization: Bearer xyz``
# is fully covered either way.
_KEY_VALUE_SECRET = re.compile(
    r"(?<![A-Za-z0-9])(?:api[_-]?key|password|passwd|token|secret|authorization|credential)"
    r"\s*[=:]\s*[^\s,;&\"'<>]+",
    re.IGNORECASE,
)

_REDACTED = "<redacted>"

_verification_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "verification_id", default=None
)


def _redact_text(value: str) -> str:
    value = _SECRET_TOKEN.sub(_REDACTED, value)
    return _KEY_VALUE_SECRET.sub(_REDACTED, value)


def _jsonable(key: str, value: Any) -> Any:
    """Recursively redact sensitive keys and secret-shaped text; JSON-safe leaves."""
    if _sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {k: _jsonable(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(key, item) for item in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))


def _sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def log_event(
    ver_id: str,
    stage: str,
    provider: str,
    latency_ms: float,
    status: str,
    **extra: Any,
) -> None:
    """Emit one structured stage event (JSON); latency clamps to >= 0."""
    event: dict[str, Any] = {
        "verification_id": ver_id,
        "stage": stage,
        "provider": provider,
        "latency_ms": max(0.0, float(latency_ms)),
        "status": status,
    }
    for key, value in extra.items():
        if key in event:
            continue  # explicit arguments stay authoritative
        event[key] = _jsonable(key, value)
    _LOGGER.info(json.dumps(event, sort_keys=True))


# --- verification_id context injection (contextvar, task-local) ---


@contextlib.contextmanager
def verification_scope(ver_id: str) -> Iterator[None]:
    """Run with ``verification_id`` attached to every log record in this task."""
    token = _verification_id.set(ver_id)
    try:
        yield
    finally:
        _verification_id.reset(token)


_ORIGINAL_RECORD_FACTORY = logging.getLogRecordFactory()


def _record_with_verification_id(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _ORIGINAL_RECORD_FACTORY(*args, **kwargs)
    ver_id = _verification_id.get()
    if ver_id is not None and not hasattr(record, "verification_id"):
        record.verification_id = ver_id
    return record


logging.setLogRecordFactory(_record_with_verification_id)
