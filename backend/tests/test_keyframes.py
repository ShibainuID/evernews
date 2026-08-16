"""T20 keyframe & OCR frame selection (TDD red/green).

Fixtures are synthetic videos built locally with the system ffmpeg: solid
color segments whose luma differences make ``select='gt(scene,0.3)'`` fire
deterministically (scene score tracks mean luma change), plus a fully static
video where scene detection finds nothing (deterministic fallback). Reuses
T18's ``new_verification_id`` and T19's ``preprocess``; media lives only
under pytest tmp dirs, nothing is committed.
"""

import subprocess
from pathlib import Path

import pytest

from backend.config import Settings
from backend.schemas.evidence import KeyframeRef, OCRFrameRef
from backend.services.ingestion.video_ingestor import new_verification_id
from backend.services.preprocessing.ffmpeg import PreprocessingError, preprocess
from backend.services.preprocessing.frame_sampler import ocr_frame_refs, sample_ocr_frames
from backend.services.preprocessing.keyframes import (
    FALLBACK_REASON,
    SCENE_REASON,
    select_keyframes,
)
from backend.tests.fixtures.video_factory import make_corrupt_mp4, make_video, require_ffmpeg

require_ffmpeg()  # skips this whole module when ffmpeg/ffprobe are missing


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Isolated WORKDIR; default settings otherwise."""
    monkeypatch.delenv("WORKDIR", raising=False)
    return Settings(workdir=str(tmp_path / "work"))


def _concat_video(tmp_path: Path, name: str, colors: list[str], seg_sec: float = 1.5) -> Path:
    """Solid-color segments concatenated into one h264 MP4 with real cuts."""
    out = tmp_path / name
    inputs: list[str] = []
    labels: list[str] = []
    for i, color in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:size=640x360:rate=24:duration={seg_sec}"]
        labels.append(f"[{i}:v]")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            *inputs,
            "-filter_complex",
            f"{''.join(labels)}concat=n={len(colors)}:v=1:a=0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _static_video(tmp_path: Path, name: str = "static.mp4", duration: float = 6.0) -> Path:
    """Static gray video: scene detection always yields zero frames."""
    out = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=gray:size=640x360:rate=24:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def test_select_keyframes_scene_video_returns_3_6_sorted_reasons(settings, tmp_path):
    """4 solid colors → 3 scene cuts; every KeyframeRef field is populated."""
    video = _concat_video(tmp_path, "scenes.mp4", ["black", "white", "gray", "yellow"])

    kfs = select_keyframes(video, new_verification_id(), settings=settings)

    assert 3 <= len(kfs) <= 6
    assert all(isinstance(k, KeyframeRef) for k in kfs)
    assert all(k.frame_id and k.selection_reason for k in kfs)
    assert all(Path(k.local_path).exists() for k in kfs)
    timestamps = [k.timestamp_sec for k in kfs]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)
    # the scene cuts sit on the 1.5s color boundaries
    assert all(any(abs(t - boundary) < 0.25 for t in timestamps) for boundary in (1.5, 3.0, 4.5))
    assert all(k.selection_reason == SCENE_REASON for k in kfs)


def test_static_video_falls_back_to_uniform_sampling(settings, tmp_path):
    """No scene frames → deterministic uniform fallback, ~1 frame/s, capped 3–6."""
    video = _static_video(tmp_path)

    kfs = select_keyframes(video, new_verification_id(), settings=settings)

    assert 3 <= len(kfs) <= 6
    assert all(k.selection_reason == FALLBACK_REASON for k in kfs)
    assert all(Path(k.local_path).exists() for k in kfs)
    timestamps = [k.timestamp_sec for k in kfs]
    assert timestamps == sorted(timestamps)
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    mean_gap = sum(gaps) / len(gaps)
    assert all(abs(gap - mean_gap) < 0.25 * mean_gap + 0.1 for gap in gaps)


def test_single_scene_change_is_too_few_and_falls_back(settings, tmp_path):
    """1 scene candidate (< 3) is 'too few' → uniform fallback."""
    video = _concat_video(tmp_path, "two_scenes.mp4", ["black", "white"])

    kfs = select_keyframes(video, new_verification_id(), settings=settings)

    assert 3 <= len(kfs) <= 6
    assert all(k.selection_reason == FALLBACK_REASON for k in kfs)
    timestamps = [k.timestamp_sec for k in kfs]
    assert timestamps == sorted(timestamps)


def test_scene_candidates_capped_at_six_with_even_coverage(settings, tmp_path):
    """>6 distinct scene cuts (8 colors, dedupe disabled) → exactly 6 frames,
    evenly spaced across the candidates, and the output dir holds exactly the
    returned refs (no unreturned scene files left behind)."""
    video = _concat_video(
        tmp_path,
        "many_scenes.mp4",
        # hex colors whose limited-range luma deltas (219,154,64,104,169,40,64)
        # all exceed the scene threshold, so all 7 cuts fire deterministically
        ["0x000000", "0xFFFFFF", "0xFF0000", "0x00FF00", "0x0000FF", "0xFFFF00", "0x00FFFF", "0xFF00FF"],
    )
    ver_id = new_verification_id()

    kfs = select_keyframes(
        video, ver_id, settings=settings, is_duplicate=lambda candidate, selected: False
    )

    assert len(kfs) == 6
    timestamps = [k.timestamp_sec for k in kfs]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == len(timestamps)
    # early/late coverage: first cut (1.5s) and last cut (10.5s) both survive
    assert abs(timestamps[0] - 1.5) < 0.25
    assert abs(timestamps[-1] - 10.5) < 0.25
    # output dir contains exactly the returned frame files, nothing else
    keyframe_dir = Path(settings.workdir) / ver_id / "keyframes"
    assert {p.name for p in keyframe_dir.glob("*.jpg")} == {Path(k.local_path).name for k in kfs}
    assert all(Path(k.local_path).exists() for k in kfs)


def test_default_near_duplicate_filter_drops_repeated_scene_frame(settings, tmp_path):
    """A repeated color (white@1.5s and white@4.5s) is selected only once."""
    video = _concat_video(tmp_path, "repeated.mp4", ["black", "white", "gray", "white", "black"])

    kfs = select_keyframes(video, new_verification_id(), settings=settings)

    timestamps = [k.timestamp_sec for k in kfs]
    assert len(kfs) == 3
    assert all(any(abs(t - boundary) < 0.25 for t in timestamps) for boundary in (1.5, 3.0, 6.0))
    # the second, byte-identical white frame (first frame of segment 4) is dropped
    assert not any(abs(t - 4.5) < 0.25 for t in timestamps)


def test_injected_near_duplicate_filter_boundary_is_consulted(settings, tmp_path):
    """A stub that flags everything forces the uniform fallback: the helper
    boundary is part of the selection pipeline, not a no-op."""
    video = _concat_video(tmp_path, "scenes.mp4", ["black", "white", "gray", "yellow"])

    kfs = select_keyframes(
        video,
        new_verification_id(),
        settings=settings,
        is_duplicate=lambda candidate, selected: True,
    )

    assert 3 <= len(kfs) <= 6
    assert all(k.selection_reason == FALLBACK_REASON for k in kfs)


def test_frame_ids_and_paths_are_deterministic_across_runs(settings, tmp_path):
    video = _concat_video(tmp_path, "scenes.mp4", ["black", "white", "gray", "yellow"])
    ver_id = new_verification_id()

    first = select_keyframes(video, ver_id, settings=settings)
    second = select_keyframes(video, ver_id, settings=settings)

    assert [k.frame_id for k in first] == [k.frame_id for k in second]
    assert [k.local_path for k in first] == [k.local_path for k in second]


def test_ocr_frames_one_per_second_deterministic_and_bounded(settings, tmp_path):
    video = _static_video(tmp_path)
    ver_id = new_verification_id()

    first = sample_ocr_frames(video, ver_id, settings=settings)
    second = sample_ocr_frames(video, ver_id, settings=settings)

    assert first == second  # deterministic paths
    assert len(first) == 6  # ≈ 1 fps on a 6 s clip
    assert len(first) <= 15
    assert all(Path(p).exists() for p in first)
    assert all("ocr_frames" in p for p in first)


def test_ocr_frames_15s_video_capped_at_15(settings, tmp_path):
    video = _static_video(tmp_path, "static15.mp4", duration=15.0)

    ocr = sample_ocr_frames(video, new_verification_id(), settings=settings)

    assert len(ocr) == 15


def test_ocr_frame_refs_timestamps_follow_1fps_t0_contract(settings, tmp_path):
    """§4.4 seam: ref timestamps are the fps=1 / t=0 sample times (sample i at
    t=i), and the ref set matches the sampler's path set exactly."""
    video = _static_video(tmp_path)
    ver_id = new_verification_id()

    refs = ocr_frame_refs(video, ver_id, settings=settings)
    paths = sample_ocr_frames(video, ver_id, settings=settings)

    assert len(refs) == 6  # 6 s clip at 1 fps
    assert all(isinstance(ref, OCRFrameRef) for ref in refs)
    assert [ref.local_path for ref in refs] == paths  # same ordered set
    assert [ref.timestamp_sec for ref in refs] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert all("/ocr_frames/" in ref.local_path for ref in refs)


def test_ocr_frame_refs_deterministic_and_capped_at_15(settings, tmp_path):
    video = _static_video(tmp_path, "static15.mp4", duration=15.0)
    ver_id = new_verification_id()

    first = ocr_frame_refs(video, ver_id, settings=settings)
    second = ocr_frame_refs(video, ver_id, settings=settings)

    assert first == second
    assert len(first) == 15  # fps=1 from t=0 on 15 s: 15 samples, cap enforced
    assert [ref.timestamp_sec for ref in first] == [float(i) for i in range(15)]


def test_keyframe_and_ocr_frame_sets_are_disjoint(settings, tmp_path):
    video = _concat_video(tmp_path, "scenes.mp4", ["black", "white", "gray", "yellow"])
    ver_id = new_verification_id()

    kfs = select_keyframes(video, ver_id, settings=settings)
    ocr = sample_ocr_frames(video, ver_id, settings=settings)

    keyframe_paths = {k.local_path for k in kfs}
    assert keyframe_paths.isdisjoint(set(ocr))
    assert all("/keyframes/" in p for p in keyframe_paths)
    assert all("/ocr_frames/" in p for p in ocr)


def test_select_keyframes_and_ocr_consume_preprocess_output(settings, tmp_path):
    """Full T18→T19→T20 chain: normalized.mp4 feeds both selectors."""
    ver_id = new_verification_id()
    artifacts = preprocess(ver_id, make_video(tmp_path), settings=settings)

    kfs = select_keyframes(artifacts.normalized_path, ver_id, settings=settings)
    ocr = sample_ocr_frames(artifacts.normalized_path, ver_id, settings=settings)

    assert 3 <= len(kfs) <= 6
    assert all(Path(k.local_path).exists() for k in kfs)
    assert 1 <= len(ocr) <= 15


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "ver_id", "ver_", "ver_zzzz", "ver_" + "f" * 31, "VER_" + "f" * 32],
)
def test_rejects_unsafe_ver_id_before_any_subprocess(settings, tmp_path, monkeypatch, bad_id):
    def _forbidden(*args, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("unsafe ver_id must be rejected before any subprocess runs")

    # Build the fixture BEFORE patching: the factory itself runs subprocess.run.
    video = make_video(tmp_path)
    monkeypatch.setattr(subprocess, "run", _forbidden)

    for select in (select_keyframes, sample_ocr_frames):
        with pytest.raises(PreprocessingError) as exc:
            select(video, bad_id, settings=settings)
        assert exc.value.code == "unsafe_ver_id"


def test_failure_cleans_partial_output_dirs_keeps_t19_artifacts(settings, tmp_path):
    ver_id = new_verification_id()
    work_dir = Path(settings.workdir) / ver_id
    keyframes_dir = work_dir / "keyframes"
    ocr_dir = work_dir / "ocr_frames"
    for out_dir in (keyframes_dir, ocr_dir):
        out_dir.mkdir(parents=True)
    (keyframes_dir / "frame_001.jpg").write_bytes(b"stale partial")
    (ocr_dir / "frame_01.jpg").write_bytes(b"stale partial")
    normalized = work_dir / "normalized.mp4"
    normalized.write_bytes(b"t19-owned artifact")
    corrupt = make_corrupt_mp4(tmp_path)

    with pytest.raises(PreprocessingError) as exc:
        select_keyframes(corrupt, ver_id, settings=settings)
    assert exc.value.code == "undecodable"
    assert not keyframes_dir.exists()

    with pytest.raises(PreprocessingError) as exc:
        sample_ocr_frames(corrupt, ver_id, settings=settings)
    assert not ocr_dir.exists()

    # T19-owned artifacts are preserved for retry
    assert normalized.read_bytes() == b"t19-owned artifact"


def test_all_subprocess_calls_use_fixed_argv_no_shell(settings, tmp_path, monkeypatch):
    calls: list[tuple[list[str], dict]] = []
    real_run = subprocess.run

    def spy(args, **kwargs):
        calls.append((list(args), kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    video = _concat_video(tmp_path, "scenes.mp4", ["black", "white", "gray", "yellow"])
    ver_id = new_verification_id()
    kfs = select_keyframes(video, ver_id, settings=settings)
    ocr = sample_ocr_frames(video, ver_id, settings=settings)

    assert kfs and ocr
    assert calls
    known_paths = {str(video)} | {k.local_path for k in kfs} | set(ocr)
    for args, kwargs in calls:
        assert kwargs.get("shell") is not True
        for arg in args:
            assert isinstance(arg, str)
            # the only non-fixed tokens are the trusted paths; no user flags, no shell metachars
            assert arg in known_paths or not any(ch in arg for ch in (";", "&", "|", "$", "`", ".."))
