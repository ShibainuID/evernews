"""T25: PaddleOCRProvider tests (TDD red-green).

paddleocr/paddlex are never imported here: the model factory is injected, so
unit tests neither import the SDK nor download weights. Stub results mirror
PaddleOCR 3.x ``predict`` output: one dict per input with ``rec_texts``
(list[str]), ``rec_scores`` (list[float]) and ``rec_boxes`` (``[N, 4]``
numpy float array of left/top/right/bottom, or ``np.array([])`` when empty).
"""

import numpy as np
import pytest

from backend.config import Settings
from backend.providers.base import OCRExtractor
from backend.providers.paddleocr import OCRFrameError, PaddleOCRProvider


class _StubModel:
    """Duck-typed PaddleOCR: ``predict(path) -> list[dict]`` (one dict per frame)."""

    def __init__(self, *frame_results: dict):
        self._frame_results = list(frame_results)
        self.predict_calls: list[str] = []

    def predict(self, frame_path: str):
        self.predict_calls.append(frame_path)
        if len(self._frame_results) == 1:
            result = self._frame_results[0]
        else:
            result = self._frame_results.pop(0)
        return [result]


class _ScriptedFactory:
    """Model factory stub: yields scripted models; the last outcome repeats."""

    def __init__(self, outcomes: list):
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def __call__(self, lang: str):
        self.calls.append(lang)
        outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(texts=None, scores=None, boxes=None) -> dict:
    """A PaddleOCR 3.x per-frame result dict; defaults to one empty hit list."""
    return {
        "rec_texts": list(texts or []),
        "rec_scores": list(scores or []),
        # Real engine: rec_boxes is an [N, 4] float ndarray (left/top/right/
        # bottom) or np.array([]) when empty. Shape-preserving conversion is
        # the provider's job; the stub passes the same raw shapes through.
        "rec_boxes": np.array(boxes, dtype=float) if boxes is not None else np.array([]),
    }


def _frame_paths(tmp_path, count: int) -> list[str]:
    """frame_sampler-style paths: ``ocr_frames/frame_NN.jpg``, 1 fps from t=0."""
    ocr_dir = tmp_path / "ocr_frames"
    ocr_dir.mkdir(exist_ok=True)
    paths = []
    for i in range(1, count + 1):
        frame = ocr_dir / f"frame_{i:02d}.jpg"
        frame.write_bytes(b"fake-jpeg")
        paths.append(str(frame))
    return paths


# --- contract ---


def test_provider_satisfies_ocr_extractor_protocol():
    assert isinstance(PaddleOCRProvider(), OCRExtractor)


# --- missing frame: clear error, no model load ---


def test_extract_missing_frame_raises_clear_error_without_model_load(tmp_path):
    factory = _ScriptedFactory([_StubModel()])
    provider = PaddleOCRProvider(model_factory=factory)

    with pytest.raises(OCRFrameError, match="not found"):
        provider.extract([str(tmp_path / "ocr_frames" / "frame_01.jpg")])

    assert factory.calls == []


# --- empty text: no hits, pipeline continues ---


def test_extract_frames_without_text_yields_empty_list(tmp_path):
    model = _StubModel(_result())
    provider = PaddleOCRProvider(model_factory=_ScriptedFactory([model]))

    assert provider.extract(_frame_paths(tmp_path, 2)) == []
    assert len(model.predict_calls) == 2


# --- repeated overlay text: dedupe consecutive, raw hit kept ---


def test_extract_dedupes_consecutive_repeated_overlay_text(tmp_path):
    jakarta_box = [[10.0, 5.0, 200.0, 60.0]]
    model = _StubModel(
        _result(
            ["JAKARTA", "BANJIR"],
            [0.97, 0.91],
            [[10.0, 5.0, 200.0, 60.0], [10.0, 70.0, 200.0, 120.0]],
        ),
        _result(
            ["jakarta", "KOORDINAT"],
            [0.93, 0.88],
            [jakarta_box[0], [10.0, 130.0, 200.0, 180.0]],
        ),
        _result(["JAKARTA"], [0.95], [jakarta_box[0]]),
    )
    provider = PaddleOCRProvider(model_factory=_ScriptedFactory([model]))

    hits = provider.extract(_frame_paths(tmp_path, 3))

    # "JAKARTA" appears in every frame; normalized it is the same overlay, so
    # only the first raw hit is kept (case/whitespace differences collapse).
    assert [h.text for h in hits] == ["JAKARTA", "BANJIR", "KOORDINAT"]
    assert [h.frame_id for h in hits] == ["frame_01", "frame_01", "frame_02"]
    assert hits[0].confidence == 0.97  # raw hit preserved
    assert hits[0].bbox == [[10.0, 5.0, 200.0, 60.0]]  # bbox passed through


def test_extract_keeps_overlay_reappearing_after_a_gap(tmp_path):
    """Non-consecutive repeats are raw evidence: dedupe only applies while the
    overlay keeps appearing on consecutive frames."""
    box = [[10.0, 5.0, 200.0, 60.0]]
    model = _StubModel(
        _result(["JAKARTA"], [0.97], [box[0]]),
        _result(["BANJIR"], [0.91], [box[0]]),
        _result(["JAKARTA"], [0.95], [box[0]]),
    )
    provider = PaddleOCRProvider(model_factory=_ScriptedFactory([model]))

    hits = provider.extract(_frame_paths(tmp_path, 3))

    assert [h.text for h in hits] == ["JAKARTA", "BANJIR", "JAKARTA"]
    assert hits[-1].frame_id == "frame_03"  # re-appearance kept as raw evidence


# --- low confidence: reported as-is, never upgraded ---


def test_extract_reports_low_confidence_hit_unmodified(tmp_path):
    model = _StubModel(
        _result(["JAKARTA"], [0.35], [[10.0, 5.0, 200.0, 60.0]])
    )
    provider = PaddleOCRProvider(model_factory=_ScriptedFactory([model]))

    hits = provider.extract(_frame_paths(tmp_path, 1))

    assert len(hits) == 1  # low-confidence OCR is evidence, kept for the fuser's policy
    assert hits[0].text == "JAKARTA"
    assert hits[0].confidence == 0.35  # not raised, not dropped


# --- bbox pass-through ---


def test_extract_passes_bounding_boxes_through(tmp_path):
    boxes = [[10.0, 5.0, 200.0, 60.0], [10.0, 70.0, 200.0, 120.0]]
    model = _StubModel(_result(["JAKARTA", "BANJIR"], [0.97, 0.91], boxes))
    provider = PaddleOCRProvider(model_factory=_ScriptedFactory([model]))

    hits = provider.extract(_frame_paths(tmp_path, 1))

    # One hit per detected line; each hit carries that line's single box.
    assert [h.bbox for h in hits] == [[boxes[0]], [boxes[1]]]


# --- lang: Settings default vs explicit constructor override ---


def test_provider_defaults_lang_from_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("OCR_LANG", raising=False)
    factory = _ScriptedFactory([_StubModel(_result())])
    provider = PaddleOCRProvider(model_factory=factory)  # no explicit lang

    provider.extract(_frame_paths(tmp_path, 1))

    assert Settings().ocr_lang == "en"
    assert factory.calls == ["en"]


def test_explicit_lang_overrides_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("OCR_LANG", "ch")
    factory = _ScriptedFactory([_StubModel(_result())])
    provider = PaddleOCRProvider(lang="id", model_factory=factory)

    provider.extract(_frame_paths(tmp_path, 1))

    assert factory.calls == ["id"]
