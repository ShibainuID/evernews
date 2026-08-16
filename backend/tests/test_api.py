"""T36: verification API endpoints + background job wiring (TDD red-green).

TestClient + scripted provider fakes injected through
``app.dependency_overrides[get_providers]`` — no models, no network, no API
keys. TestClient awaits background tasks inside ``post()``, so mid-run polling
tests run the POST in a worker thread against a gate-blocked fake provider
and poll from a second TestClient over the same app; the gate also proves the
create response is not held by the pipeline (<1s to background start).

Golden A (HANDOFF §36) is scripted with ``test_pipeline._case_a`` — the same
scripted bundle the T35 end-to-end tests use.
"""

import asyncio
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import state as state_module
from backend.api.verification import get_providers
from backend.main import create_app
from backend.schemas.context import SpeechExtraction
from backend.services.ingestion.video_ingestor import new_verification_id
from backend.tests.fixtures.video_factory import make_video, make_video_20s, require_ffmpeg
from backend.tests.test_pipeline import _case_a

BASE = "/api/v1/verification"
FAKE_ID = "ver_" + "0" * 32


class GatedSpeech:
    """Blocks on ``gate`` after capturing the run's ver_id from its audio path.

    The pipeline hands the speech provider the ``WORKDIR/{ver_id}/audio.wav``
    path, so the parent dir name is the generated id the API chose — the test
    learns it without any private state-store access. The wait runs via
    ``asyncio.to_thread`` so the fake never blocks its event loop (the poll
    client has its own loop).
    """

    def __init__(self, started: threading.Event, gate: threading.Event, seen: dict):
        self._started = started
        self._gate = gate
        self._seen = seen

    async def transcribe(self, audio_path: str) -> SpeechExtraction:
        self._seen["ver_id"] = Path(audio_path).parent.name
        self._started.set()
        assert await asyncio.to_thread(self._gate.wait, 20), "gate never released"
        return SpeechExtraction(transcript="banjir Jakarta pagi ini semakin parah", language="id")


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Isolated WORKDIR + development env; provider overrides registered per test."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("WORKDIR", str(tmp_path / "work"))
    application = create_app()
    state_module.store.reset()
    yield application
    application.dependency_overrides.clear()


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """6s synthetic MP4 with audio, encoded once per module."""
    require_ffmpeg()
    return make_video(tmp_path_factory.mktemp("media"))


def _inject(app, providers) -> None:
    """Keep the dependency seam exercised: every POST test runs on fakes."""
    app.dependency_overrides[get_providers] = lambda: providers


def _start_gated_post(post_client: TestClient, video: Path, started: threading.Event, seen: dict) -> threading.Thread:
    """POST in a worker thread; returns once the pipeline hits the gated provider."""
    def _post() -> None:
        with video.open("rb") as f:
            seen["resp"] = post_client.post(
                BASE, files={"video": ("clip.mp4", f, "video/mp4")}
            )

    thread = threading.Thread(target=_post)
    thread.start()
    assert started.wait(timeout=20), "pipeline never reached the gated provider"
    return thread


def test_create_returns_202_with_processing_state(app, video: Path):
    _inject(app, _case_a(new_verification_id()))
    with TestClient(app) as client:
        with video.open("rb") as f:
            resp = client.post(
                BASE, files={"video": ("clip.mp4", f, "video/mp4")},
                data={"caption": "Jakarta banjir"},
            )
    assert resp.status_code == 202
    body = resp.json()
    assert set(body) == {"verification_id", "status"}
    assert body["status"] == "processing"
    assert body["verification_id"].startswith("ver_")

    final = state_module.store.get(body["verification_id"])
    assert final is not None
    assert final.status == "completed"
    assert final.result is not None


def test_unknown_ids_are_404(app):
    with TestClient(app) as client:
        assert client.get(f"{BASE}/{FAKE_ID}").status_code == 404
        assert client.get(f"{BASE}/{FAKE_ID}/result").status_code == 404
        assert client.get(f"{BASE}/{FAKE_ID}/debug").status_code == 404


def test_polling_through_processing_and_golden_a_result(
    app, video: Path, monkeypatch: pytest.MonkeyPatch
):
    # The API generates the ver_id internally; pin it (valid generated format)
    # so the scripted plan's frame_ids match the real keyframes, like T35.
    from backend.api import verification as verification_api

    monkeypatch.setattr(verification_api, "new_verification_id", lambda: FAKE_ID)
    gate = threading.Event()
    started = threading.Event()
    seen: dict = {}
    providers = _case_a(FAKE_ID)
    providers.speech = GatedSpeech(started, gate, seen)
    _inject(app, providers)

    with TestClient(app) as post_client, TestClient(app) as poll_client:
        t0 = time.monotonic()
        thread = _start_gated_post(post_client, video, started, seen)
        create_latency = time.monotonic() - t0
        ver_id = seen["ver_id"]

        # mid-run poll: processing, stuck at the first heavy stage
        mid = poll_client.get(f"{BASE}/{ver_id}")
        assert mid.status_code == 200
        body = mid.json()
        assert body["verification_id"] == ver_id
        assert body["status"] == "processing"
        assert body["stage"] == "extracting_context"
        assert body["progress"] == 0.25
        assert body["error"] is None

        # result-before-complete is a clear 409 carrying the processing state
        early = poll_client.get(f"{BASE}/{ver_id}/result")
        assert early.status_code == 409
        early_body = early.json()["detail"]
        assert early_body["status"] == "processing"
        assert early_body["stage"] == "extracting_context"

        gate.set()
        thread.join(timeout=30)
        assert create_latency < 1.0, "create must not hold the request"
        assert seen["resp"].status_code == 202

        done = poll_client.get(f"{BASE}/{ver_id}")
        assert done.status_code == 200
        assert done.json()["status"] == "completed"
        assert done.json()["stage"] == "completed"
        assert done.json()["progress"] == 1.0
        assert done.json()["error"] is None

        result_resp = poll_client.get(f"{BASE}/{ver_id}/result")
        assert result_resp.status_code == 200
        result = result_resp.json()
        assert result["verification_id"] == ver_id
        assert result["classification"] == "possible_false_context"
        assert result["source_context"]["location"] == "Bangkok"
        assert result["source_context"]["date"] == "2022-10-03"
        assert "location_changed" in result["manipulation_types"]
        assert "old_footage_reused" in result["manipulation_types"]
        assert result["sources"], "golden A must present the matched source"


def test_video_too_long_rejected_before_background(app, tmp_path: Path):
    _inject(app, _case_a(new_verification_id()))
    long_video = make_video_20s(tmp_path)
    with TestClient(app) as client:
        with long_video.open("rb") as f:
            resp = client.post(BASE, files={"video": ("long.mp4", f, "video/mp4")})
    assert resp.status_code == 400
    assert "MAX_VIDEO_DURATION_SEC" in resp.json()["detail"]
    # never accepted into the store: no background job was scheduled
    assert state_module.store._states == {}


def test_upload_without_video_is_422(app):
    with TestClient(app) as client:
        resp = client.post(BASE, data={"caption": "no video here"})
    assert resp.status_code == 422


def test_debug_returns_artifacts_and_result_summaries(app, video: Path):
    _inject(app, _case_a(new_verification_id()))
    with TestClient(app) as client:
        with video.open("rb") as f:
            created = client.post(BASE, files={"video": ("clip.mp4", f, "video/mp4")})
        ver_id = created.json()["verification_id"]

        resp = client.get(f"{BASE}/{ver_id}/debug")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification_id"] == ver_id
    assert body["status"] == "completed"
    names = [artifact["name"] for artifact in body["artifacts"]]
    assert "original.mp4" in names
    assert "normalized.mp4" in names
    assert "audio.wav" in names
    assert any(name.startswith("keyframes/") for name in names)
    for artifact in body["artifacts"]:
        name = artifact["name"]
        assert not Path(name).is_absolute()
        assert ".." not in name
        assert artifact["size_bytes"] >= 0
    assert body["context"] is not None
    assert body["context"]["event"]["value"] == "flood"
    assert body["comparison"] is not None
    assert body["plan"] is not None
    assert body["plan"]["verification_id"] == ver_id
    assert body["bundle"] is not None
    assert body["bundle"]["verification_id"] == ver_id


def test_debug_disabled_outside_development(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKDIR", str(tmp_path / "work"))
    application = create_app()
    with TestClient(application) as client:
        resp = client.get(f"{BASE}/{FAKE_ID}/debug")
    assert resp.status_code == 404
