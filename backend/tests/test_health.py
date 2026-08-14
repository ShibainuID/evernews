"""Health endpoint + lifespan (workdir create, stale artifact cleanup) tests."""

import os
import time

from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_returns_ok():
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_creates_workdir(monkeypatch, tmp_path):
    workdir = tmp_path / "work"
    monkeypatch.setenv("WORKDIR", str(workdir))
    with TestClient(create_app()) as client:
        client.get("/health")
    assert workdir.is_dir()


def test_lifespan_removes_stale_artifact_dirs(monkeypatch, tmp_path):
    workdir = tmp_path / "work"
    stale = workdir / "old_ver"
    fresh = workdir / "fresh_ver"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    old = time.time() - 25 * 3600  # older than the 24h retention window
    os.utime(stale, (old, old))
    monkeypatch.setenv("WORKDIR", str(workdir))
    with TestClient(create_app()) as client:
        client.get("/health")
    assert not stale.exists()
    assert fresh.exists()
