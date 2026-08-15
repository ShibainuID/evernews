"""T39: demo query cache tests (TDD red-green).

Covers the stdlib-only ``QueryCache`` (24h default TTL, lock-protected
get/set, lazy expiry removal) and its orchestrator integration: a repeated
same-key call skips the provider, a different key calls it, failed calls are
never cached and retry on the next execution, successful empty results are
valid cache entries, fact/web/vision keys stay separated by origin, visual
frame keys hash ``KeyframeRef.local_path`` bytes deterministically, demo
fallback candidates never enter the cache under web/vision keys, and Case A
run three times produces identical bundles with provider counters proving
reuse (design §15 DoD). The process-local singleton is cleared before and
after every test to avoid cross-test contamination.
"""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.schemas.context import VideoContext
from backend.schemas.evidence import ContextClaim, KeyframeRef
from backend.schemas.investigation import (
    FactCheckEvidence,
    FactCheckTask,
    InvestigationPlan,
    VisualSearchTask,
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)
from backend.schemas.result import SourceCandidate
from backend.services.validation.cache import (
    QueryCache,
    DEFAULT_TTL_SECONDS,
    fact_check_key,
    frame_key_from_path,
    query_cache,
    web_research_key,
)
from backend.services.validation.orchestrator import execute
from backend.tests.fixtures.golden_cases import case_a


# --- singleton hygiene: no state leaks between tests ---


@pytest.fixture(autouse=True)
def _clear_cache():
    query_cache.clear()
    yield
    query_cache.clear()


# --- scripted + counting branch fakes (T39 seams) ---


class CountingFactRunner:
    """Scripted ``list[FactCheckEvidence]`` or ``Exception``; counts calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.tasks: list[FactCheckTask] = []

    async def __call__(self, task: FactCheckTask) -> list[FactCheckEvidence]:
        self.calls += 1
        self.tasks.append(task)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CountingWebRunner:
    """Scripted ``WebResearchResult`` or ``Exception``; counts calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def __call__(self, task: WebResearchTask) -> WebResearchResult:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CountingVisualRunner:
    """Scripted ``list[VisualWebCandidate]`` or ``Exception``; counts calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.tasks: list[VisualSearchTask] = []

    async def __call__(self, task: VisualSearchTask) -> list[VisualWebCandidate]:
        self.calls += 1
        self.tasks.append(task)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class CountingDemoIndex:
    """Scripted ``list[SourceCandidate]`` or ``Exception``; counts calls."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def search(self, frames: list[str]) -> list[SourceCandidate]:
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# --- fixtures ---


def _context(frame_path: str) -> VideoContext:
    return VideoContext(
        verification_id="ver_cache",
        event=ContextClaim(value="flood", confidence=0.9, evidence_ids=["ev_1"], explicitly_claimed=True),
        location=ContextClaim(value="Jakarta", confidence=0.9, evidence_ids=["ev_1"], explicitly_claimed=True),
        time=ContextClaim(value="today", confidence=0.9, evidence_ids=["ev_1"], explicitly_claimed=True),
        evidence=[],
        keyframes=[KeyframeRef(frame_id="kf_01", timestamp_sec=0.0, local_path=frame_path)],
    )


def _plan(
    *,
    fact_query: str = "cache-alpha",
    web_question: str = "cache-Q-alpha",
    frame_path: str = "nofile.png",
    fact_langs: list[str] | None = None,
) -> InvestigationPlan:
    return InvestigationPlan(
        verification_id="ver_cache",
        fact_check_tasks=[
            FactCheckTask(
                task_id="fc_c_00",
                queries=[fact_query],
                language_codes=list(fact_langs or []),
                goal="find fact checks",
            )
        ],
        web_research_tasks=[
            WebResearchTask(
                task_id="web_c_00",
                question=web_question,
                queries=[fact_query],
                preferred_source_types=["news"],
            )
        ],
        visual_search_tasks=[
            VisualSearchTask(task_id="vis_c_00", frame_ids=["kf_01"], goal="trace footage")
        ],
        investigation_questions=["Q?"],
        stop_conditions=["stop"],
    )


def _fact_evidence(query: str, evidence_id: str = "fc_c_ev") -> FactCheckEvidence:
    return FactCheckEvidence(
        evidence_id=evidence_id, query=query, review_url="https://factcheck.example/c", raw={}
    )


def _web_result(question: str) -> WebResearchResult:
    return WebResearchResult(
        task_id="web_c_00",
        question=question,
        status="supported",
        finding="found",
        evidence=[],
        unresolved=[],
        searches_used=1,
        pages_fetched=1,
    )


def _visual_candidate(frame_id: str = "kf_01", url: str = "https://x/a.jpg") -> VisualWebCandidate:
    return VisualWebCandidate(
        candidate_id="vw_c_1",
        frame_id=frame_id,
        candidate_type="full_image_match",
        url=url,
        raw_provider_type="full_matching_images",
    )


def _demo_source() -> SourceCandidate:
    return SourceCandidate(
        source_id="demo_c_1",
        url="https://demo.example/c",
        canonical_url="https://demo.example/c",
        matched_frame_ids=["kf_01"],
        match_types=["visually_similar"],
        origin="demo_index",
    )


async def _run(
    fact: CountingFactRunner,
    web: CountingWebRunner,
    visual: CountingVisualRunner,
    demo: CountingDemoIndex,
    plan: InvestigationPlan,
    context: VideoContext,
):
    return await execute(
        context,
        plan,
        run_fact_check=fact,
        investigate=web,
        run_visual=visual,
        demo_index=demo.search,
        cache=query_cache,
    )


# --- QueryCache unit tests ---


def test_query_cache_get_set_roundtrip_and_default_ttl():
    qc = QueryCache()
    assert DEFAULT_TTL_SECONDS == 24 * 60 * 60  # exactly 24 hours
    assert qc.get("missing") is None
    qc.set("k", "v")
    assert qc.get("k") == "v"
    qc.set("k", "v2")  # overwrite in place
    assert qc.get("k") == "v2"


def test_query_cache_ttl_expiry_removes_entry():
    qc = QueryCache(ttl_seconds=0.05)
    qc.set("k", "v")
    assert qc.get("k") == "v"
    time.sleep(0.08)  # past the 24h-equivalent TTL window
    assert qc.get("k") is None  # expired entry removed on touch
    assert qc.get("k") is None  # stays removed


def _thread_worker(qc: QueryCache, worker_id: int) -> None:
    for j in range(50):
        qc.set(f"k:{worker_id}:{j}", (worker_id, j))


def test_query_cache_lock_protected_concurrent_access():
    qc = QueryCache()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_thread_worker, qc, i) for i in range(8)]
        for future in futures:
            future.result()
    for i in range(8):
        for j in range(50):
            assert qc.get(f"k:{i}:{j}") == (i, j)  # no lost/raced entries


# --- key builders ---


def test_key_builders_by_origin_and_visual_frame_hash(tmp_path):
    assert fact_check_key("q", "en") == "fc:q:en"
    assert fact_check_key("q") == "fc:q:"
    assert web_research_key("t", "Q") == "web:t:Q"
    # origin prefixes never collide with each other
    assert "fc:q:" != "web:t:Q"
    assert not frame_key_from_path("missing.png")

    frame = tmp_path / "kf.png"
    frame.write_bytes(b"frame-bytes")
    key = frame_key_from_path(str(frame))
    assert key is not None and key.startswith("vis:")
    assert key == frame_key_from_path(str(frame))  # deterministic
    frame.write_bytes(b"other-frame-bytes")
    assert frame_key_from_path(str(frame)) != key  # content changes the hash


# --- orchestrator integration ---


async def test_same_key_skips_provider_second_run(tmp_path):
    frame = tmp_path / "kf.png"
    frame.write_bytes(b"same-frame")
    context = _context(str(frame))
    plan = _plan(frame_path=str(frame))

    fact = CountingFactRunner([[_fact_evidence("cache-alpha")]])
    web = CountingWebRunner([_web_result("cache-Q-alpha")])
    visual = CountingVisualRunner([[_visual_candidate()]])
    demo = CountingDemoIndex([])

    bundle_a = await _run(fact, web, visual, demo, plan, context)
    bundle_b = await _run(fact, web, visual, demo, plan, context)

    assert (fact.calls, web.calls, visual.calls, demo.calls) == (1, 1, 1, 0)
    assert bundle_a.model_dump() == bundle_b.model_dump()


async def test_different_key_calls_provider_again(tmp_path):
    frame = tmp_path / "kf.png"
    frame.write_bytes(b"frame-A")
    context = _context(str(frame))

    fact = CountingFactRunner(
        [[_fact_evidence("cache-alpha")], [_fact_evidence("cache-beta", "fc_c_ev2")]]
    )
    web = CountingWebRunner([_web_result("cache-Q-alpha"), _web_result("cache-Q-beta")])
    visual = CountingVisualRunner(
        [[_visual_candidate(url="https://x/a.jpg")], [_visual_candidate(url="https://x/b.jpg")]]
    )
    demo = CountingDemoIndex([])

    plan_a = _plan(fact_query="cache-alpha", web_question="cache-Q-alpha", frame_path=str(frame))
    await _run(fact, web, visual, demo, plan_a, context)

    frame.write_bytes(b"frame-B")  # new frame bytes -> new vis key
    plan_b = _plan(fact_query="cache-beta", web_question="cache-Q-beta", frame_path=str(frame))
    bundle_b = await _run(fact, web, visual, demo, plan_b, context)

    assert (fact.calls, web.calls, visual.calls) == (2, 2, 2)  # every key differs -> provider called
    assert [e.evidence_id for e in bundle_b.fact_checks] == ["fc_c_ev2"]


async def test_failed_result_not_cached_and_retried_next_execution(tmp_path):
    frame = tmp_path / "kf.png"
    frame.write_bytes(b"retry-frame")
    context = _context(str(frame))
    plan = _plan(frame_path=str(frame))

    fact = CountingFactRunner([RuntimeError("fact check down"), [_fact_evidence("cache-alpha")]])
    web = CountingWebRunner([_web_result("cache-Q-alpha")])
    visual = CountingVisualRunner([[_visual_candidate()]])
    demo = CountingDemoIndex([])

    bundle_failed = await _run(fact, web, visual, demo, plan, context)
    assert fact.calls == 1
    assert bundle_failed.branch_status["fact_check"] == "error"
    assert any("fact check down" in e for e in bundle_failed.errors)

    bundle_ok = await _run(fact, web, visual, demo, plan, context)
    assert fact.calls == 2  # failure was not cached -> retried
    assert bundle_ok.branch_status["fact_check"] == "success"

    bundle_cached = await _run(fact, web, visual, demo, plan, context)
    assert fact.calls == 2  # now cached -> provider skipped
    assert bundle_ok.model_dump() == bundle_cached.model_dump()


async def test_successful_empty_results_are_valid_cache_entries(tmp_path):
    frame = tmp_path / "kf.png"
    frame.write_bytes(b"empty-frame")
    context = _context(str(frame))
    plan = _plan(fact_query="cache-empty", frame_path=str(frame))

    fact = CountingFactRunner([[]])
    web = CountingWebRunner([_web_result("cache-Q-alpha")])
    visual = CountingVisualRunner([[]])  # zero-match frame: valid no-match
    demo = CountingDemoIndex([[_demo_source()], [_demo_source()]])

    bundle_a = await _run(fact, web, visual, demo, plan, context)
    assert fact.calls == 1 and visual.calls == 1 and demo.calls == 1
    assert bundle_a.branch_status["fact_check"] == "success_no_matches"
    assert bundle_a.branch_status["visual_search"] == "demo_fallback"

    bundle_b = await _run(fact, web, visual, demo, plan, context)
    assert fact.calls == 1 and visual.calls == 1  # empty results cached -> skipped
    assert demo.calls == 2  # empty visual still triggers the demo fallback (contract intact)
    assert bundle_a.model_dump() == bundle_b.model_dump()


async def test_demo_fallback_origin_stays_out_of_cache(tmp_path):
    frame = tmp_path / "kf.png"
    frame.write_bytes(b"demo-frame")
    context = _context(str(frame))
    plan = _plan(frame_path=str(frame))

    fact = CountingFactRunner([[_fact_evidence("cache-alpha")]])
    web = CountingWebRunner([_web_result("cache-Q-alpha")])
    visual = CountingVisualRunner([RuntimeError("vision api down"), RuntimeError("vision api down")])
    demo = CountingDemoIndex([[_demo_source()], [_demo_source()]])

    bundle_a = await _run(fact, web, visual, demo, plan, context)
    bundle_b = await _run(fact, web, visual, demo, plan, context)

    assert (fact.calls, web.calls, visual.calls, demo.calls) == (1, 1, 2, 2)
    assert bundle_a.branch_status["visual_search"] == "demo_fallback"
    assert bundle_b.branch_status["visual_search"] == "demo_fallback"
    assert bundle_a.model_dump() == bundle_b.model_dump()
    # failures were never cached -> nothing under the vis key; demo candidates
    # (raw_provider_type="demo_index") never contaminated any origin key.
    assert query_cache.get(frame_key_from_path(str(frame))) is None
    assert query_cache.get(fact_check_key("cache-alpha")) == bundle_a.fact_checks
    assert query_cache.get(web_research_key("web_c_00", "cache-Q-alpha")) == bundle_a.web_research[0]
    assert any(c.raw_provider_type == "demo_index" for c in bundle_b.visual_candidates)


async def test_case_a_three_runs_identical_with_provider_counters(tmp_path):
    """Case A (Jakarta flood 2026 vs Bangkok 2022): 3 runs, identical output,
    provider counters prove cache reuse (design §15 DoD)."""
    frame = tmp_path / "kf_a.png"
    frame.write_bytes(b"case-a-frame")
    case = case_a()
    context = case.video_context.model_copy(
        update={"keyframes": [KeyframeRef(frame_id="kf_01", timestamp_sec=3.5, local_path=str(frame))]}
    )
    plan = InvestigationPlan(
        verification_id="ver_a",
        fact_check_tasks=[
            FactCheckTask(
                task_id="fc_a_01",
                queries=["Jakarta banjir 2026"],
                goal="Find existing fact checks",
            )
        ],
        web_research_tasks=[
            WebResearchTask(
                task_id="web_a_01",
                question="When was this footage first published?",
                queries=["flood footage Jakarta"],
                preferred_source_types=["news"],
            )
        ],
        visual_search_tasks=[
            VisualSearchTask(task_id="vis_a_01", frame_ids=["kf_01"], goal="trace footage")
        ],
        investigation_questions=["Did the claimed event happen when and where stated?"],
        stop_conditions=["Stop when at least two reputable sources answer the question."],
    )

    fact = CountingFactRunner([case.bundle.fact_checks])
    web = CountingWebRunner([case.bundle.web_research[0]])
    visual = CountingVisualRunner([case.bundle.visual_candidates])
    demo = CountingDemoIndex([])

    bundles = [await _run(fact, web, visual, demo, plan, context) for _ in range(3)]

    assert bundles[0].model_dump() == bundles[1].model_dump() == bundles[2].model_dump()
    assert (fact.calls, web.calls, visual.calls, demo.calls) == (1, 1, 1, 0)  # reuse proven
    assert [e.evidence_id for e in bundles[0].fact_checks] == ["fc_a_01"]
    assert bundles[0].fact_checks[0].textual_rating == "old footage"
    assert bundles[0].web_research[0].evidence[0].location == "Bangkok"
    assert bundles[0].visual_candidates[0].url == "https://example.com/article/bangkok-flood"
    assert bundles[0].errors == []
    assert bundles[0].branch_status == {
        "fact_check": "success",
        "web_research": "success",
        "visual_search": "success",
    }
