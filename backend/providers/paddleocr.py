"""PaddleOCR 3.x OCR provider (T25): ``OCRExtractor`` adapter (HANDOFF §5.2).

The model is loaded lazily on the first real extraction (deferred import and
construction, so unit tests never touch the SDK and weights only download
when extracting). Paddle engine with document/textline orientation disabled
for the MVP. Raw hits are kept as-is: low-confidence OCR is still reported —
the fuser caps OCR-only claims, this provider never turns low confidence into
a high-confidence claim. Repeated overlay text (same normalized text) is
deduplicated to the first raw hit; case and whitespace differences collapse.
"""

import re
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.schemas.context import OCRHit
from backend.schemas.evidence import OCRFrameRef


class OCRFrameError(Exception):
    """Deterministic OCR failure; message names the cause."""


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Case-folded, whitespace-collapsed key used for overlay dedupe."""
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _as_boxes(raw_boxes: Any) -> list[list[float]]:
    """PaddleOCR ``rec_boxes`` -> plain ``list[list[float]]`` (``[N, 4]``).
    Numpy's truthiness is ambiguous on empty arrays, so never ``or``-fallback
    on it; ``np.array([]).tolist()`` yields ``[]`` directly."""
    if raw_boxes is None or len(raw_boxes) == 0:
        return []
    return raw_boxes.tolist() if hasattr(raw_boxes, "tolist") else raw_boxes


def _default_model_factory(lang: str) -> Any:
    # Deferred import: paddleocr is a heavy SDK and unit tests inject a stub.
    from paddleocr import PaddleOCR

    return PaddleOCR(
        engine="paddle",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        lang=lang,
    )


class PaddleOCRProvider:
    """Lazy PaddleOCR adapter: no model exists until a real extraction."""

    def __init__(
        self,
        lang: str | None = None,
        model_factory: Any | None = None,
    ):
        self._lang = lang if lang is not None else Settings().ocr_lang
        self._model_factory = model_factory or _default_model_factory
        self._model: Any = None

    def extract(self, frames: list[OCRFrameRef]) -> list[OCRHit]:
        paths = [Path(frame.local_path) for frame in frames]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise OCRFrameError(f"frame not found: {missing[0]}")
        if self._model is None:
            self._model = self._model_factory(self._lang)
        hits: list[OCRHit] = []
        prev_frame_keys: set[str] = set()  # normalized keys of the previous frame
        for ref in frames:
            frame_keys: set[str] = set()
            for raw in self._model.predict(ref.local_path) or []:
                texts = raw.get("rec_texts") or []
                scores = raw.get("rec_scores") or []
                boxes = _as_boxes(raw.get("rec_boxes"))
                for i in range(len(texts)):
                    hit = OCRHit(
                        frame_id=Path(ref.local_path).stem,
                        timestamp_sec=ref.timestamp_sec,  # sampling-contract time (§4.4), not index
                        text=str(texts[i]),
                        confidence=float(scores[i]),
                        bbox=[boxes[i]],
                    )
                    key = _normalize_text(hit.text)
                    if key:
                        # The overlay is still on screen even when its hit is
                        # suppressed, so propagate the key to the next frame.
                        frame_keys.add(key)
                    if key and key in prev_frame_keys:
                        continue  # repeated overlay text: keep the first raw hit
                    hits.append(hit)
            prev_frame_keys = frame_keys
        return hits
