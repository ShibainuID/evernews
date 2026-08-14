"""Shared evidence schema tests: HANDOFF §3.1-3.4 enums and models, §19 ResultClassification."""

import pytest
from pydantic import ValidationError

from backend.schemas.evidence import (
    ComparisonStatus,
    ConfidenceLabel,
    ContextClaim,
    EvidenceAtom,
    EvidenceType,
    KeyframeRef,
    ResultClassification,
)


def test_confidence_label_values():
    assert [m.value for m in ConfidenceLabel] == ["low", "medium", "high"]


def test_comparison_status_values():
    assert [m.value for m in ComparisonStatus] == ["consistent", "mismatch", "unknown"]


def test_evidence_type_seven_values_from_handoff_31():
    assert [m.value for m in EvidenceType] == [
        "user_caption",
        "speech",
        "ocr",
        "visual",
        "fact_check",
        "web_article",
        "visual_web_match",
    ]


def test_result_classification_five_values_from_handoff_19():
    assert [m.value for m in ResultClassification] == [
        "possible_false_context",
        "context_consistent_with_source",
        "claim_conflict_found",
        "source_match_with_incomplete_context",
        "insufficient_evidence",
    ]


def test_evidence_atom_valid_without_optional_fields():
    atom = EvidenceAtom(evidence_id="speech_01", type="speech", value="banjir")
    assert atom.evidence_id == "speech_01"
    assert atom.type is EvidenceType.SPEECH
    assert atom.confidence is None
    assert atom.frame_id is None
    assert atom.source_url is None
    assert atom.notes == []


def test_evidence_atom_rejects_invalid_type():
    with pytest.raises(ValidationError):
        EvidenceAtom(evidence_id="x_01", type="gossip", value="v")


def test_keyframe_ref_valid():
    kf = KeyframeRef(frame_id="kf_01", timestamp_sec=3.5, local_path="work/kf_01.jpg")
    assert kf.public_url is None
    assert kf.selection_reason is None


def test_context_claim_requires_evidence_ids():
    with pytest.raises(ValidationError):
        ContextClaim(value="flood", confidence=0.9, explicitly_claimed=True)


def test_context_claim_valid_with_evidence_ids():
    claim = ContextClaim(
        value="flood",
        normalized_value="flood",
        confidence=0.96,
        evidence_ids=["speech_01", "visual_01"],
        explicitly_claimed=True,
    )
    assert claim.evidence_ids == ["speech_01", "visual_01"]
    assert claim.explicitly_claimed is True
