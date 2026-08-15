"""Investigation planner (T31): VideoContext -> bounded InvestigationPlan via Luna.

One text-only structured Luna call renders ``prompts/planner.txt``; the
returned plan is then bounded deterministically (HANDOFF §7.2/§37):

- ``verification_id`` always comes from the input ``VideoContext``;
- at most 1 fact-check task, ``max_web_research_tasks`` web tasks, 1 visual
  task, and at most ``max_queries_per_task`` queries per task;
- per-task schema budgets (``max_searches`` / ``max_pages`` /
  ``max_candidates_per_frame``) are clamped to their schema defaults while
  every other task field is preserved verbatim;
- every truncation is recorded as an explicit "Bounded investigation" note
  appended to ``stop_conditions``, and empty ``stop_conditions`` get one
  deterministic default (the plan has no ``unresolved`` field — none is
  added);
- Indonesian claims (detected via deterministic token hints) gain missing
  id/en query variants and fact-check ``language_codes`` without duplicating
  variants the model already produced.

The plan schema is strict (``extra="forbid"``): a model-emitted verdict field
fails validation and goes through the provider's one schema-repair path
(``LunaProvider.structured`` / ``utils.llm.parse_structured`` semantics) — the
planner itself never repairs in a loop, never browses, never ranks, and never
picks a final source; its only I/O is the provider call.
"""

import re
import string
from pathlib import Path
from typing import TypeVar

from pydantic import ConfigDict

from backend.config import Settings
from backend.providers.base import LunaProvider
from backend.schemas.context import VideoContext
from backend.schemas.evidence import ContextClaim
from backend.schemas.investigation import (
    FactCheckTask,
    InvestigationPlan,
    VisualSearchTask,
    WebResearchTask,
)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "planner.txt"

_MAX_FACT_CHECK_TASKS = 1
_MAX_VISUAL_SEARCH_TASKS = 1
_MAX_SEARCHES = 4  # WebResearchTask.max_searches schema default
_MAX_PAGES = 6  # WebResearchTask.max_pages schema default
_MAX_CANDIDATES_PER_FRAME = 10  # VisualSearchTask.max_candidates_per_frame default

_DEFAULT_STOP_CONDITION = (
    "Stop when at least two reputable textual sources answer the event question, "
    "or no further distinct evidence is found"
)

# Deterministic Indonesian detection hints (event/function words only —
# toponyms excluded so "Jakarta flood" still classifies as English).
# ponytail: heuristic, not a language detector; extend the set if false
# negatives appear in real claims.
_ID_HINTS = frozenset(
    {
        "ada", "akan", "angin", "banjir", "bencana", "belum", "besar", "dan",
        "dari", "di", "diduga", "dengan", "erupsi", "gempa", "gunung", "hari",
        "hujan", "ini", "itu", "kebakaran", "kecelakaan", "korban", "longsor",
        "masih", "melanda", "oleh", "pada", "saat", "sebelum", "setelah",
        "sudah", "tentang", "terjadi", "tsunami", "untuk", "warga", "yang",
    }
)

T = TypeVar("T")


class _StrictPlan(InvestigationPlan):
    """Reject extra JSON fields — a model-emitted verdict can never pass."""

    model_config = ConfigDict(extra="forbid")


# --- deterministic language detection ---


def _looks_indonesian(text: str) -> bool:
    """True when the text contains an Indonesian event/function-word token."""
    tokens = re.findall(r"[a-z]+", text.lower())
    return any(token in _ID_HINTS for token in tokens)


def _is_indonesian_claim(context: VideoContext) -> bool:
    values = (
        context.event.value,
        context.location.value,
        context.time.value,
        context.transcript,
    )
    return any(value is not None and _looks_indonesian(value) for value in values)


def _id_query(context: VideoContext) -> str | None:
    """First Indonesian-sounding claim text — a real Indonesian query."""
    for value in (
        context.event.value,
        context.location.value,
        context.time.value,
        context.transcript,
    ):
        if value is not None and _looks_indonesian(value):
            return value
    return None


def _en_query(context: VideoContext, fallback: str | None = None) -> str | None:
    """Deterministic English query from entities + non-Indonesian keywords.

    Falls back to the task's English question when no English keyword exists.
    """
    parts = [
        *(e.strip() for e in context.entities if e.strip()),
        *(k for k in context.keywords if k.strip() and not _looks_indonesian(k)),
    ]
    candidate = " ".join(dict.fromkeys(parts)).strip()
    if not candidate and fallback and not _looks_indonesian(fallback):
        candidate = fallback.strip()
    return candidate or None


def _is_id_query(query: str) -> bool:
    return bool(query.strip()) and _looks_indonesian(query)


def _is_en_query(query: str) -> bool:
    return bool(query.strip()) and not _looks_indonesian(query)


# --- deterministic post-validation ---


def _cap_tasks(tasks: list[T], cap: int, label: str) -> tuple[list[T], str | None]:
    if len(tasks) <= cap:
        return tasks, None
    return tasks[:cap], f"Bounded investigation: {label} truncated to {cap}."


def _bound_queries(
    queries: list[str],
    cap: int,
    id_variant: str | None,
    en_variant: str | None,
) -> tuple[list[str], bool]:
    """Truncate to ``cap``; guarantee id+en variants when derivable.

    Appended variants survive the re-truncation (oldest model queries drop),
    so the id+en guarantee and the query cap hold simultaneously.
    """
    truncated = len(queries) > cap
    out = list(queries[:cap])
    if id_variant is not None and not any(_is_id_query(q) for q in out):
        out.append(id_variant)
    if en_variant is not None and not any(_is_en_query(q) for q in out):
        out.append(en_variant)
    if len(out) > cap:
        out = out[-cap:]
    return out, truncated


def _ensure_language_codes(codes: list[str], id_claim: bool) -> list[str]:
    if not id_claim:
        return list(codes)
    out = list(codes)
    for lang in ("id", "en"):
        if lang not in out:
            out.append(lang)
    return out


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


def _enforce_plan(
    plan: InvestigationPlan, context: VideoContext, settings: Settings
) -> InvestigationPlan:
    notes: list[str] = []
    id_claim = _is_indonesian_claim(context)
    id_variant = _id_query(context) if id_claim else None
    cap = settings.max_queries_per_task

    fc_tasks, note = _cap_tasks(
        plan.fact_check_tasks, _MAX_FACT_CHECK_TASKS, "fact check tasks"
    )
    if note:
        notes.append(note)
    web_tasks, note = _cap_tasks(
        plan.web_research_tasks, settings.max_web_research_tasks, "web research tasks"
    )
    if note:
        notes.append(note)
    vis_tasks, note = _cap_tasks(
        plan.visual_search_tasks, _MAX_VISUAL_SEARCH_TASKS, "visual search tasks"
    )
    if note:
        notes.append(note)

    fact_check: list[FactCheckTask] = []
    for fc_task in fc_tasks:
        queries, truncated = _bound_queries(
            fc_task.queries, cap, id_variant, _en_query(context)
        )
        if truncated:
            notes.append(
                f"Bounded investigation: queries in task {fc_task.task_id} "
                f"truncated to {cap}."
            )
        fact_check.append(
            fc_task.model_copy(
                update={
                    "queries": queries,
                    "language_codes": _ensure_language_codes(
                        fc_task.language_codes, id_claim
                    ),
                }
            )
        )

    web_research: list[WebResearchTask] = []
    for web_task in web_tasks:
        queries, truncated = _bound_queries(
            web_task.queries, cap, id_variant, _en_query(context, web_task.question)
        )
        if truncated:
            notes.append(
                f"Bounded investigation: queries in task {web_task.task_id} "
                f"truncated to {cap}."
            )
        web_research.append(
            web_task.model_copy(
                update={
                    "queries": queries,
                    "max_searches": _clamp(web_task.max_searches, 1, _MAX_SEARCHES),
                    "max_pages": _clamp(web_task.max_pages, 1, _MAX_PAGES),
                }
            )
        )

    visual_search: list[VisualSearchTask] = [
        vis_task.model_copy(
            update={
                "max_candidates_per_frame": _clamp(
                    vis_task.max_candidates_per_frame, 1, _MAX_CANDIDATES_PER_FRAME
                )
            }
        )
        for vis_task in vis_tasks
    ]

    stop_conditions = list(plan.stop_conditions)
    if not stop_conditions:
        stop_conditions = [_DEFAULT_STOP_CONDITION]
    if notes:
        stop_conditions.extend(notes)

    return plan.model_copy(
        update={
            "verification_id": context.verification_id,
            "fact_check_tasks": fact_check,
            "web_research_tasks": web_research,
            "visual_search_tasks": visual_search,
            "stop_conditions": stop_conditions,
        }
    )


# --- prompt rendering ---


def _claim_text(claim: ContextClaim) -> str:
    if claim.value is None:
        return "none"
    if claim.normalized_value and claim.normalized_value != claim.value:
        return f"{claim.value} (normalized: {claim.normalized_value})"
    return claim.value


def render_planner_prompt(context: VideoContext, settings: Settings | None = None) -> str:
    """Render ``prompts/planner.txt`` with the context and configured bounds."""
    settings = settings if settings is not None else Settings()
    template = string.Template(PROMPT_PATH.read_text())
    return template.substitute(
        verification_id=context.verification_id,
        event=_claim_text(context.event),
        location=_claim_text(context.location),
        time=_claim_text(context.time),
        entities=", ".join(context.entities) or "none",
        keywords=", ".join(context.keywords) or "none",
        transcript=context.transcript or "none",
        visual_summary=context.visual_summary or "none",
        visual_location_clues=", ".join(context.visual_location_clues) or "none",
        keyframes=", ".join(k.frame_id for k in context.keyframes) or "none",
        max_web_tasks=settings.max_web_research_tasks,
        max_queries=settings.max_queries_per_task,
    )


async def create_plan(
    context: VideoContext,
    luna_provider: LunaProvider,
    settings: Settings | None = None,
) -> InvestigationPlan:
    """One strict text-only Luna call, then deterministic bounding."""
    settings = settings if settings is not None else Settings()
    plan = await luna_provider.structured(
        render_planner_prompt(context, settings), _StrictPlan
    )
    return _enforce_plan(plan, context, settings)
