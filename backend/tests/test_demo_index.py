"""T27 demo source index fallback: local visual retrieval over data/demo_sources/.

Covers: index generation (metadata + frame hashes, deterministic rebuild),
crop/resize source match Top-1 under the hamming threshold, non-matching
frames returning [], origin/metadata mapping onto SourceCandidate, and
missing-metadata sources skipped with a warning. Whole module skips when
ffmpeg is unavailable (fixtures are real media, never faked).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.schemas.result import SourceCandidate
from backend.scripts.index_demo_sources import build_index
from backend.services.evidence.demo_index import DemoIndex
from backend.tests.fixtures.video_factory import require_ffmpeg
from backend.utils.urls import canonicalize

require_ffmpeg()  # skips this whole module when ffmpeg is missing

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "data" / "demo_sources"

DEMO_INDEX_THRESHOLD = "DEMO_INDEX_HAMMING_THRESHOLD"

SOURCE_IDS = {
    "src_bangkok_flood_2022",
    "src_protest_2023",
    "src_jakarta_flood_2026",
}


def _ffmpeg(args: list[str], out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, str(out)], check=True, capture_output=True)


def _query_variant(frame: Path, vf: str, out: Path) -> Path:
    """Derive a crop/resize variant of a committed source frame (deterministic)."""
    _ffmpeg(["-i", str(frame), "-vf", vf, "-frames:v", "1"], out)
    return out


@pytest.fixture()
def index_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(DEMO_INDEX_THRESHOLD, "24")
    out = tmp_path / "_index.json"
    build_index(DEMO_ROOT, out)
    return out


# --- index generation -----------------------------------------------------


def test_build_index_writes_deterministic_index(tmp_path):
    index_path = tmp_path / "_index.json"
    entries = build_index(DEMO_ROOT, index_path)

    assert {e["source_id"] for e in entries} == SOURCE_IDS
    assert all(len(e["frame_hashes"]) == 2 for e in entries)
    assert all(0 <= h < 2**256 for e in entries for h in e["frame_hashes"].values())

    payload = json.loads(index_path.read_text())
    assert payload["version"] == 1
    assert payload["sources"] == entries

    first = index_path.read_bytes()
    build_index(DEMO_ROOT, index_path)  # rebuild must be byte-identical (deterministic)
    assert index_path.read_bytes() == first


def test_index_skips_source_without_metadata_and_warns(tmp_path):
    root = tmp_path / "demo"
    (root / "src_ok").mkdir(parents=True)
    (root / "src_bad").mkdir()
    bangkok = DEMO_ROOT / "src_bangkok_flood_2022"
    shutil.copy(bangkok / "metadata.json", root / "src_ok" / "metadata.json")
    for frame in bangkok.glob("frame_*.jpg"):
        shutil.copy(frame, root / "src_ok" / frame.name)

    with pytest.warns(UserWarning, match="src_bad"):
        entries = build_index(root, tmp_path / "_index.json")

    assert [e["source_id"] for e in entries] == ["src_ok"]


# --- search: match / non-match -------------------------------------------


def test_search_crop_resize_match_is_top1(index_path, tmp_path):
    src_frame = DEMO_ROOT / "src_bangkok_flood_2022" / "frame_01.jpg"
    queries = [
        _query_variant(src_frame, "crop=320:180:160:90,scale=640:360", tmp_path / "crop.jpg"),
        _query_variant(src_frame, "scale=320:180", tmp_path / "resize.jpg"),
    ]

    candidates = DemoIndex(index_path).search([str(q) for q in queries])

    assert candidates
    assert candidates[0].source_id == "src_bangkok_flood_2022"
    assert all(isinstance(c, SourceCandidate) for c in candidates)


def test_search_non_matching_frame_returns_empty(index_path, tmp_path):
    noise = tmp_path / "noise.jpg"
    _ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=1:duration=1", "-frames:v", "1"], noise)

    assert DemoIndex(index_path).search([str(noise)]) == []


def test_search_empty_frames_returns_empty(index_path):
    assert DemoIndex(index_path).search([]) == []


# --- origin / metadata mapping --------------------------------------------


def test_search_candidate_origin_and_metadata_mapping(index_path, tmp_path):
    src_frame = DEMO_ROOT / "src_bangkok_flood_2022" / "frame_01.jpg"
    query = _query_variant(src_frame, "scale=320:180", tmp_path / "q.jpg")
    meta = json.loads((DEMO_ROOT / "src_bangkok_flood_2022" / "metadata.json").read_text())

    candidates = DemoIndex(index_path).search([str(query)])

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.origin == "demo_index"
    assert cand.source_id == "src_bangkok_flood_2022"
    assert cand.url == meta["source_url"]
    assert cand.canonical_url == canonicalize(meta["source_url"])
    assert cand.published_at == meta["published_at"]
    assert cand.location == meta["location"]
    assert cand.event == meta["event"]
    assert cand.publisher == meta["publisher"]
    assert cand.matched_frame_ids == ["q.jpg"]
    assert cand.rank_score is None  # filled by ranker T14
    assert cand.score_breakdown == {}  # filled by ranker T14
