"""Test isolation from a developer's repo-root ``.env``.

pydantic-settings consults ``Settings.model_config["env_file"]`` at
``__init__`` time, so clearing it makes every ``Settings()`` in the test
suite ignore the gitignored developer ``.env`` — HANDOFF §25 default tests
(``test_config``, ``test_paddleocr``, ``test_whisper``, ...) stay hermetic no
matter what the local ``.env`` contains. Real env vars
(``monkeypatch.setenv``) still take priority, so env-override tests and
explicit ``Settings(workdir=...)`` constructions are unaffected.
"""

from collections.abc import Iterator

import pytest

from backend.config import Settings


@pytest.fixture(autouse=True)
def _ignore_developer_dotenv() -> Iterator[None]:
    original = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    yield
    Settings.model_config["env_file"] = original
