"""Stage 2 investigation schema tests: HANDOFF §7.2, §8.4, §9.4, §10.4, §11.3, §12."""

import pytest
from pydantic import ValidationError

from backend.schemas.investigation import (
    FactCheckEvidence,
    FactCheckTask,
    InvestigationPlan,
    RawValidationBundle,
    VisualSearchTask,
    VisualWebCandidate,
    WebResearchResult,
    WebResearchTask,
)


def _fact_check_task() -> FactCheckTask:
    return FactCheckTask(
        task_id="fc_01",
        queries=["Jakarta flood August 2026 viral video"],
        goal="Find existing fact-checks discussing this claim",
    )


def _web_research_task() -> WebResearchTask:
    return WebResearchTask(
        task_id="web_01",
        question="Did flooding occur in Jakarta on 2026-08-15?",
        queries=["Jakarta flood August 15 2026"],
        preferred_source_types=["government", "reputable_news"],
    )


def _visual_search_task() -> VisualSearchTask:
    return VisualSearchTask(
        task_id="vis_01",
        frame_ids=["kf_01", "kf_03"],
        goal="Find earlier appearances of the same or cropped footage",
    )


def _plan() -> InvestigationPlan:
    return InvestigationPlan(
        verification_id="ver_123",
        fact_check_tasks=[_fact_check_task()],
        web_research_tasks=[_web_research_task()],
        visual_search_tasks=[_visual_search_task()],
        investigation_questions=["Did significant flooding occur in Jakarta on 2026-08-15?"],
        stop_conditions=[
            "At least two reputable textual sources answer the event question",
            "At least one high-strength earlier visual candidate is found",
        ],
    )


def _bundle() -> RawValidationBundle:
    return RawValidationBundle(
        verification_id="ver_123",
        plan=_plan(),
        fact_checks=[],
        web_research=[],
        visual_candidates=[],
    )


def test_fact_check_task_valid():
    task = _fact_check_task()
    assert task.task_id == "fc_01"
    assert task.language_codes == []


def test_fact_check_task_requires_queries():
    with pytest.raises(ValidationError):
        FactCheckTask(task_id="fc_01", goal="goal")


def test_web_research_task_default_max_searches_and_max_pages():
    task = _web_research_task()
    assert task.max_searches == 4
    assert task.max_pages == 6


def test_visual_search_task_default_max_candidates_per_frame():
    assert _visual_search_task().max_candidates_per_frame == 10


def test_investigation_plan_valid_from_handoff_72():
    plan = _plan()
    assert plan.verification_id == "ver_123"
    assert len(plan.fact_check_tasks) == 1
    assert len(plan.web_research_tasks) == 1
    assert len(plan.visual_search_tasks) == 1
    assert len(plan.investigation_questions) == 1
    assert len(plan.stop_conditions) == 2


def test_investigation_plan_has_no_unresolved_field():
    assert not hasattr(_plan(), "unresolved")


def test_investigation_plan_requires_tasks():
    with pytest.raises(ValidationError):
        InvestigationPlan(
            verification_id="ver_123",
            fact_check_tasks=[],
            web_research_tasks=[],
            visual_search_tasks=[],
            investigation_questions=[],
        )


def test_fact_check_evidence_keeps_textual_rating_verbatim_with_raw():
    ev = FactCheckEvidence(
        evidence_id="fc_ev_01",
        query="Jakarta flood August 2026",
        review_url="https://example.com/review",
        textual_rating="Misleading",
        raw={"claimReview": {"textualRating": "Misleading"}},
    )
    assert ev.textual_rating == "Misleading"
    assert ev.claim_text is None
    assert ev.relevance_score is None
    assert ev.raw["claimReview"]["textualRating"] == "Misleading"


def test_fact_check_evidence_requires_raw():
    with pytest.raises(ValidationError):
        FactCheckEvidence(
            evidence_id="fc_ev_01",
            query="q",
            review_url="https://example.com/review",
        )


def test_web_source_evidence_valid():
    src = WebResearchResult(
        task_id="web_01",
        question="Did flooding occur in Jakarta on 2026-08-15?",
        status="supported",
        finding="Flooding was reported in Jakarta on 2026-08-15.",
        evidence=[
            {
                "evidence_id": "web_ev_01",
                "url": "https://example.com/article",
                "publisher": "Example News",
                "retrieved_at": "2026-08-15T04:00:00Z",
                "source_type": "reputable_news",
                "supports_question": True,
            }
        ],
        unresolved=[],
        searches_used=2,
        pages_fetched=3,
    )
    assert src.status == "supported"
    assert src.searches_used == 2
    assert src.pages_fetched == 3
    assert src.evidence[0].publisher == "Example News"
    assert src.evidence[0].supports_question is True


def test_web_research_result_status_literal_allows_four_values():
    for status in ["supported", "contradicted", "mixed", "insufficient"]:
        result = WebResearchResult(
            task_id="web_01",
            question="q",
            status=status,
            finding="f",
            evidence=[],
            unresolved=[],
            searches_used=0,
            pages_fetched=0,
        )
        assert result.status == status


def test_web_research_result_rejects_invalid_status():
    with pytest.raises(ValidationError):
        WebResearchResult(
            task_id="web_01",
            question="q",
            status="maybe",
            finding="f",
            evidence=[],
            unresolved=[],
            searches_used=0,
            pages_fetched=0,
        )


def test_visual_web_candidate_candidate_type_allows_four_values():
    for ctype in ["page_match", "full_image_match", "partial_image_match", "visually_similar"]:
        candidate = VisualWebCandidate(
            candidate_id="c_01",
            frame_id="kf_01",
            candidate_type=ctype,
            url="https://example.com/img.jpg",
            raw_provider_type=ctype,
        )
        assert candidate.candidate_type == ctype


def test_visual_web_candidate_rejects_invalid_candidate_type():
    with pytest.raises(ValidationError):
        VisualWebCandidate(
            candidate_id="c_01",
            frame_id="kf_01",
            candidate_type="exact_match",
            url="https://example.com/img.jpg",
            raw_provider_type="exact_match",
        )


def test_visual_web_candidate_requires_raw_provider_type():
    with pytest.raises(ValidationError):
        VisualWebCandidate(
            candidate_id="c_01",
            frame_id="kf_01",
            candidate_type="page_match",
            url="https://example.com/img.jpg",
        )


def test_raw_validation_bundle_valid_from_handoff_12():
    bundle = _bundle()
    assert bundle.verification_id == "ver_123"
    assert bundle.plan.verification_id == "ver_123"
    assert bundle.fact_checks == []
    assert bundle.web_research == []
    assert bundle.visual_candidates == []


def test_raw_validation_bundle_requires_plan():
    with pytest.raises(ValidationError):
        RawValidationBundle(
            verification_id="ver_123",
            fact_checks=[],
            web_research=[],
            visual_candidates=[],
        )


def test_raw_validation_bundle_errors_and_branch_status_defaults():
    bundle = _bundle()
    assert bundle.errors == []
    assert bundle.branch_status == {}


def test_raw_validation_bundle_defaults_are_per_instance():
    a = _bundle()
    b = _bundle()
    a.errors.append("boom")
    a.branch_status["fact_check"] = "partial_failure"
    assert b.errors == []
    assert b.branch_status == {}
