"""Fact check task runner (T28): ``FactCheckTask`` -> ``list[FactCheckEvidence]``.

Loops the task's query variants, passing each ``language_code`` into
``search_fact_checks`` (which already bounds pagination to 3 pages and applies
the 15s timeout / 5xx-429 retry policy). Reviews are deduplicated across all
queries and languages by ``review_url``, retaining the first occurrence while
keeping every distinct result. A raised provider error (timeout, 5xx after
retries, ...) propagates to the orchestrator (T32) for ``branch_status`` — it
is never turned into an empty result.
"""

import httpx

from backend.providers.google_factcheck import search_fact_checks
from backend.schemas.investigation import FactCheckEvidence, FactCheckTask


async def run_fact_check_task(
    task: FactCheckTask,
    api_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[FactCheckEvidence]:
    """Run every query variant in every language; dedupe reviews by ``review_url``.

    ``api_key`` defaults to ``Settings().google_fact_check_api_key``; the
    ``transport`` seam exists only for deterministic MockTransport tests.
    """
    seen: set[str] = set()
    results: list[FactCheckEvidence] = []
    if task.language_codes:
        languages: list[str | None] = list(task.language_codes)
    else:
        languages = [None]
    for query in task.queries:
        for language in languages:
            for evidence in await search_fact_checks(
                query, api_key=api_key, language=language, transport=transport
            ):
                if evidence.review_url not in seen:
                    seen.add(evidence.review_url)
                    results.append(evidence)
    return results
