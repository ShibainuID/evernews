"""Settings tests: HANDOFF §25 defaults, env overrides, optional .env."""

from backend.config import Settings

_ALL_ENV_KEYS = (
    "APP_ENV",
    "MAX_VIDEO_DURATION_SEC",
    "MAX_VIDEO_SIZE_MB",
    "WORKDIR",
    "WHISPER_MODEL_SIZE",
    "OCR_LANG",
    "OPENCODE_GO_API_KEY",
    "OPENCODE_GO_BASE_URL",
    "LUNA_MODEL",
    "LUNA_TIMEOUT_SEC",
    "OPENCODE_SERVER_URL",
    "OPENCODE_SERVER_USERNAME",
    "OPENCODE_SERVER_PASSWORD",
    "GOOGLE_FACT_CHECK_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ENABLE_URL_INPUT",
    "ENABLE_FACTCHECK_IMAGE_SEARCH",
    "ENABLE_LOCAL_VISUAL_EMBEDDINGS",
    "ENABLE_LOCAL_FEATURE_MATCHING",
    "MAX_WEB_RESEARCH_TASKS",
    "MAX_QUERIES_PER_TASK",
)


def test_defaults_from_handoff_25(monkeypatch):
    # No .env exists in the repo, so a clean-construction Settings() also
    # proves the .env file is optional.
    for key in _ALL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.app_env == "development"
    assert settings.max_video_duration_sec == 15
    assert settings.max_video_size_mb == 50
    assert settings.workdir == "./data/work"
    assert settings.whisper_model_size == "tiny"
    assert settings.ocr_lang == "en"
    assert settings.opencode_go_api_key == ""
    assert settings.opencode_go_base_url == "https://opencode.ai/zen/go/v1"
    assert settings.luna_model == "gpt-5.6-luna"
    assert settings.luna_timeout_sec == 60
    assert settings.opencode_server_url == "http://127.0.0.1:4096"
    assert settings.opencode_server_username == "opencode"
    assert settings.opencode_server_password == ""
    assert settings.google_fact_check_api_key == ""
    assert settings.google_cloud_project == ""
    assert settings.google_application_credentials == "/path/to/service-account.json"
    assert settings.enable_url_input is False
    assert settings.enable_factcheck_image_search is False
    assert settings.enable_local_visual_embeddings is False
    assert settings.enable_local_feature_matching is False
    assert settings.max_web_research_tasks == 3
    assert settings.max_queries_per_task == 4


def test_env_override_readable(monkeypatch):
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "20")
    assert Settings().max_video_duration_sec == 20


def test_planner_bounds_env_override(monkeypatch):
    monkeypatch.setenv("MAX_WEB_RESEARCH_TASKS", "2")
    monkeypatch.setenv("MAX_QUERIES_PER_TASK", "6")
    settings = Settings()
    assert settings.max_web_research_tasks == 2
    assert settings.max_queries_per_task == 6
