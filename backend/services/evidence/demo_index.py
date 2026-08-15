"""T27 demo source index fallback: local visual retrieval over ``data/demo_sources/``.

Guarantees demo candidates appear even when external APIs change: the
orchestrator passes producer keyframes to ``DemoIndex.search`` and gets
``SourceCandidate``s with ``origin="demo_index"``, metadata from each source's
``metadata.json``, and ``rank_score``/``score_breakdown`` left empty for the
T14 ranker to fill. Source hashes are precomputed at index build time; search
only hashes the query frames.
"""

import json
import os
from pathlib import Path

from backend.schemas.result import SourceCandidate
from backend.utils.urls import canonicalize
from backend.utils.visual_hash import frame_average_hash, hamming_distance

_ORIGIN_DEMO_INDEX = "demo_index"
_THRESHOLD_ENV = "DEMO_INDEX_HAMMING_THRESHOLD"
_DEFAULT_THRESHOLD = 24
_METADATA_FIELDS = (
    "publisher",
    "title",
    "published_at",
    "event",
    "location",
    "time_context",
    "description",
)


def _default_index_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "demo_sources" / "_index.json"


class DemoIndex:
    """Searchable index of committed demo sources, built by ``index_demo_sources``."""

    def __init__(
        self,
        index_path: str | Path | None = None,
        hamming_threshold: int | None = None,
    ):
        self.index_path = Path(index_path) if index_path is not None else _default_index_path()
        # ponytail: average-hash Hamming is a naive similarity proxy — two
        # frames within threshold are *similar*, not proven the same footage.
        # Demo fallback only; upgrade path is ENABLE_LOCAL_VISUAL_EMBEDDINGS
        # (Tier 2, outside MVP).
        self.hamming_threshold = (
            hamming_threshold
            if hamming_threshold is not None
            else int(os.environ.get(_THRESHOLD_ENV, str(_DEFAULT_THRESHOLD)))
        )
        self._sources = self._load()

    def _load(self) -> list[dict]:
        if not self.index_path.is_file():
            raise FileNotFoundError(
                f"demo source index not found at {self.index_path}; run "
                "`python -m backend.scripts.index_demo_sources` first"
            )
        payload = json.loads(self.index_path.read_text())
        return payload["sources"]

    def search(self, frames: list[str]) -> list[SourceCandidate]:
        """Return demo sources whose precomputed hash is within threshold of any query frame."""
        if not frames:
            return []
        query_hashes = [frame_average_hash(frame) for frame in frames]
        candidates = []
        for entry in self._sources:
            if not entry["frame_hashes"]:  # a frameless entry can never match
                continue
            matched = [
                Path(frame).name
                for frame, query_hash in zip(frames, query_hashes)
                if min(hamming_distance(query_hash, source_hash) for source_hash in entry["frame_hashes"].values())
                <= self.hamming_threshold
            ]
            if matched:
                candidates.append(self._to_candidate(entry, matched))
        return candidates

    @staticmethod
    def _to_candidate(entry: dict, matched_frames: list[str]) -> SourceCandidate:
        metadata = entry["metadata"]
        source_url = metadata.get("source_url") or ""
        return SourceCandidate(
            source_id=entry["source_id"],
            url=source_url,
            canonical_url=canonicalize(source_url),
            **{field: metadata.get(field) for field in _METADATA_FIELDS},
            matched_frame_ids=matched_frames,
            match_types=["visually_similar", "hash:average_hamming"],
            earliest_known_date=metadata.get("published_at"),
            origin=_ORIGIN_DEMO_INDEX,
            # rank_score / score_breakdown: left None / {} — filled by ranker T14
        )
