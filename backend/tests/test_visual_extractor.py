"""Visual extractor (T22): keyframes -> VisualObservation (Luna, 1 call/frame).

Covers: valid fake Luna output -> VisualObservation; hallucination guard
(extra="forbid", a guessed ``location`` rejected / repaired-then-corrected);
one retry per failing keyframe then skip-and-continue; all frames failing
raises the branch error; deterministic ordered merge with evidence frame
guarantees; prompt file guard text (no fake/hoax/verdict/web/fact language).
"""

from pathlib import Path
from typing import Any, Callable, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from backend.schemas.context import VisualObservation
from backend.schemas.evidence import KeyframeRef
from backend.services.context.visual_extractor import VisualExtractionError, extract
from backend.tests.fixtures.providers_fakes import FakeLunaProvider

T = TypeVar("T", bound=BaseModel)

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "visual_context.txt"


def _kf(frame_id: str) -> KeyframeRef:
    return KeyframeRef(
        frame_id=frame_id, timestamp_sec=1.0, local_path=f"/tmp/{frame_id}.png"
    )


class _RecordingLuna:
    """FakeLunaProvider wrapped with a call log (prompt/schema/image_paths)."""

    def __init__(
        self,
        script: list[str | Exception],
        repair_fn: Callable[[str, ValidationError], str] | None = None,
    ):
        self._inner = FakeLunaProvider(script, repair_fn)
        self.calls: list[tuple[str, Any, list[str] | None]] = []

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        image_paths: list[str] | None = None,
    ) -> T:
        self.calls.append((prompt, schema, image_paths))
        return await self._inner.structured(prompt, schema, image_paths)


# --- valid output + call discipline ---


async def test_valid_fake_luna_output_returns_merged_visual_observation():
    keyframes = [_kf("f1"), _kf("f2")]
    raw1 = (
        '{"scene_type": "city street", "objects": ["car", "street", "car"],'
        ' "evidence_frames": {"f1": ["car visible"], "other": ["sign"]}}'
    )
    raw2 = '{"scene_type": "beach", "objects": ["street", "tree"], "actions": ["walking"]}'
    provider = _RecordingLuna([raw1, raw2])

    result = await extract(keyframes, provider)

    assert isinstance(result, VisualObservation)
    assert result.scene_type == "city street"  # first non-empty scene wins
    assert result.objects == ["car", "street", "tree"]  # ordered, de-duplicated
    assert result.actions == ["walking"]
    assert result.evidence_frames == {
        "f1": ["car visible"],  # keyed by the returned frame ID
        "other": ["sign"],  # returned foreign key kept
        "f2": [],  # current frame ID ensured
    }
    # one bounded call per keyframe with image_paths=[keyframe.local_path]
    assert [call[2] for call in provider.calls] == [
        ["/tmp/f1.png"],
        ["/tmp/f2.png"],
    ]
    # strict schema passed to Luna (extra="forbid") and prompt from the file
    assert all(call[1].model_config.get("extra") == "forbid" for call in provider.calls)
    assert provider.calls[0][0] == PROMPT_PATH.read_text()


# --- deterministic merge ---


async def test_merge_is_deterministic_ordered_dedup_with_frame_guarantee():
    # frame 1: no scene type, duplicate objects, duplicate evidence notes;
    # frame 2: scene type present, overlapping objects;
    # frame 3: no evidence_frames and a foreign frame ID key.
    keyframes = [_kf("f1"), _kf("f2"), _kf("f3")]
    provider = FakeLunaProvider(
        [
            '{"objects": ["a", "b", "a"], "evidence_frames": {"f1": ["note1", "note1"]}}',
            '{"scene_type": "forest", "objects": ["b", "c"]}',
            '{"scene_type": "desert", "objects": ["c"], "evidence_frames": {"fX": ["x"]}}',
        ]
    )

    result = await extract(keyframes, provider)

    assert result.scene_type == "forest"  # first non-empty, not first frame
    assert result.objects == ["a", "b", "c"]  # ordered de-dup across frames
    assert result.evidence_frames == {
        "f1": ["note1"],
        "fX": ["x"],
        "f2": [],
        "f3": [],
    }


# --- hallucination guard (strict schema) ---


async def test_forbidden_location_field_rejected_and_frame_skipped():
    keyframes = [_kf("f1"), _kf("f2")]
    provider = _RecordingLuna(
        [
            '{"location": "Jakarta"}',  # extra field -> validation failure
            '{"location": "Jakarta"}',  # retry fails again -> f1 skipped
            '{"scene_type": "market"}',
        ]
    )

    result = await extract(keyframes, provider)

    assert result.scene_type == "market"
    assert result.evidence_frames == {"f2": []}  # f1 skipped, nothing fabricated
    assert len(provider.calls) == 3  # two attempts on f1, one on f2


async def test_forbidden_field_repaired_then_accepted():
    def strip_extra(raw_input, first_error):
        return '{"scene_type": "park"}'

    provider = FakeLunaProvider(['{"location": "Jakarta"}'], repair_fn=strip_extra)

    result = await extract([_kf("f1")], provider)

    assert result.scene_type == "park"
    assert result.evidence_frames == {"f1": []}


async def test_uncorrected_repair_still_rejected():
    def noop_repair(raw_input, first_error):
        return raw_input

    provider = FakeLunaProvider(
        ['{"location": "Jakarta"}', '{"location": "Jakarta"}'], repair_fn=noop_repair
    )

    with pytest.raises(VisualExtractionError):
        await extract([_kf("f1")], provider)


# --- bounded retry / skip / branch error ---


async def test_failed_keyframe_retried_once_then_remaining_frames_continue():
    keyframes = [_kf("f1"), _kf("f2"), _kf("f3")]
    provider = _RecordingLuna(
        [
            RuntimeError("luna down"),  # f1 first attempt fails
            '{"scene_type": "street"}',  # f1 retry succeeds
            '{"scene_type": "park"}',
            '{"scene_type": "harbor"}',
        ]
    )

    result = await extract(keyframes, provider)

    assert result.scene_type == "street"
    assert result.evidence_frames == {"f1": [], "f2": [], "f3": []}
    assert len(provider.calls) == 4  # one retry; remaining frames still processed


async def test_all_frames_failing_raises_branch_error():
    keyframes = [_kf("f1"), _kf("f2")]
    provider = FakeLunaProvider([RuntimeError("down")] * 4)  # 2 attempts x 2 frames

    with pytest.raises(VisualExtractionError, match="no observations"):
        await extract(keyframes, provider)


# --- prompt guard text ---


def test_prompt_file_has_neutral_observation_only_wording():
    prompt = PROMPT_PATH.read_text()

    assert "Describe only what is visibly supported by the supplied frame" in prompt


def test_prompt_avoids_fake_hoax_verdict_and_web_fact_language():
    prompt = PROMPT_PATH.read_text().lower()

    for banned in (
        "fake",
        "hoax",
        "verdict",
        "authentic",
        "conclusion",
        "conclude",
        "web",
        "fact",
        "classif",
    ):
        assert banned not in prompt, f"prompt must not contain {banned!r}"
