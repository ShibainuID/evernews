"""Deterministic source candidate ranking (T14): exact heuristic score, no LLM.

Ranking heuristic (HANDOFF 15.1, weights 0.45/0.20/0.10/0.10/0.10/0.05), not a
truth label. `candidate_score` fills `rank_score` + `score_breakdown`; `rank`
scores all candidates then sorts descending, stable for ties.
"""

from datetime import date, datetime

from backend.schemas.result import SourceCandidate
from backend.utils.dates import resolve_relative_date

_VISUAL_BY_MATCH_TYPE = {
    "full_image_match": 1.0,
    "partial_image_match": 0.8,
    "page_match": 0.6,
    "visually_similar": 0.3,
}

_WEIGHTS = (0.45, 0.20, 0.10, 0.10, 0.10, 0.05)
_COMPONENTS = ("visual", "precedence", "metadata", "context", "source_quality", "cross_frame")


def candidate_score(candidate: SourceCandidate, current_date: date | datetime) -> tuple[float, dict[str, float]]:
    """Exact weighted heuristic score plus per-component breakdown for one candidate.

    Precedence is 1.0 only when `published_at` resolves to a date strictly
    before `current_date`; missing/equal/later/unparseable dates score 0.0 —
    a missing date never implies precedence (design §7).
    """
    visual = max(
        (_VISUAL_BY_MATCH_TYPE[mt] for mt in candidate.match_types if mt in _VISUAL_BY_MATCH_TYPE),
        default=0.0,
    )
    resolved = resolve_relative_date(candidate.published_at, current_date)
    base = current_date.date() if isinstance(current_date, datetime) else current_date
    precedence = 1.0 if resolved is not None and resolved < base else 0.0

    context_fields = (candidate.event, candidate.location, candidate.time_context)
    present = sum(1 for value in context_fields if value is not None)
    context = 1.0 if present == 3 else 0.5 if present else 0.0

    components = {
        "visual": visual,
        "precedence": precedence,
        "metadata": candidate.metadata_completeness or 0.0,
        "context": context,
        "source_quality": candidate.source_quality or 0.0,
        "cross_frame": min(len(candidate.matched_frame_ids) / 3, 1.0),
    }
    score = sum(weight * components[name] for weight, name in zip(_WEIGHTS, _COMPONENTS))
    candidate.rank_score = score
    candidate.score_breakdown = components
    return score, components


def rank(candidates: list[SourceCandidate], current_date: date | datetime) -> list[SourceCandidate]:
    """Score every candidate in place, then return them sorted descending (stable)."""
    for candidate in candidates:
        candidate_score(candidate, current_date)
    return sorted(candidates, key=lambda c: c.rank_score or 0.0, reverse=True)
