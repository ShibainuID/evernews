"""T39: in-memory demo query cache (stdlib only, process-local).

``QueryCache`` is a lock-protected TTL dict: ``get`` returns the stored value
or ``None`` (missing, or expired — expired entries are removed on touch);
``set`` stores with a monotonic-clock expiry (default exactly 24 hours).
Only successful provider results are ever stored: the orchestrator wrappers
call ``set`` after the runner returns, so an exception never populates the
cache and the failed call retries on the next execution. Successful empty
results (``[]`` / a valid result object) are cacheable — ``get`` uses
``None`` as "no entry", never as a stored value.

Key builders separate the three branch origins deterministically:
fact check ``fc:{query}:{lang}``, web research ``web:{task_id}:{question}``,
visual ``vis:{sha256(frame bytes)}``. A missing/unreadable frame file yields
no key (``None``) so the caller treats that frame as a cache miss and calls
the provider — no invented cache hit.

``query_cache`` is the process-local singleton intended for demo runs; tests
clear it via ``clear()`` before/after cases.

ponytail: per-process, no persistence, lazy expiry on read. Upgrade path:
disk/Redis persistence or a size cap if restart-persistence (design §2) or
entry growth ever matters.
"""

import threading
import time
from hashlib import sha256
from typing import Any

DEFAULT_TTL_SECONDS = 24 * 60 * 60  # exactly 24 hours


def fact_check_key(query: str, language_code: str = "") -> str:
    """Key for one fact-check query variant: ``fc:{query}:{lang}``."""
    return f"fc:{query}:{language_code}"


def web_research_key(task_id: str, question: str) -> str:
    """Key for one web research task: ``web:{task_id}:{question}``."""
    return f"web:{task_id}:{question}"


def frame_key_from_path(local_path: str) -> str | None:
    """Deterministic ``vis:{sha256}`` key from ``KeyframeRef.local_path`` bytes.

    Returns ``None`` for a missing/unreadable file: that frame has no key, so
    the orchestrator treats it as a cache miss (never an invented hit).
    """
    try:
        with open(local_path, "rb") as f:
            return f"vis:{sha256(f.read()).hexdigest()}"
    except OSError:
        return None


class QueryCache:
    """Lock-protected in-memory TTL cache. ``None`` always means "no entry"."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if now >= expires_at:  # lazy expiry removal on touch
                del self._entries[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._entries[key] = (time.monotonic() + self._ttl_seconds, value)

    def clear(self) -> None:
        """Drop every entry — test/demo seam (also resets the singleton)."""
        with self._lock:
            self._entries.clear()


# Process-local singleton for demo runs; pass explicitly to execute(cache=...).
query_cache = QueryCache()
