"""Application settings, loaded from env vars / optional .env (see .env.example).

All keys mirror HANDOFF §25. Every service/API consumes ``Settings`` (via
``app.state.settings``); no module constructs its own.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: str = "development"
    max_video_duration_sec: int = 15
    max_video_size_mb: int = 50
    workdir: str = "./data/work"

    # Speech (faster-whisper)
    whisper_model_size: str = "tiny"

    # OCR (PaddleOCR)
    ocr_lang: str = "en"

    # GPT-5.6 Luna through OpenCode Go
    opencode_go_api_key: str = ""
    opencode_go_base_url: str = "https://opencode.ai/zen/go/v1"
    luna_model: str = "gpt-5.6-luna"
    luna_timeout_sec: int = 60

    # Local OpenCode research runtime
    opencode_server_url: str = "http://127.0.0.1:4096"
    opencode_server_username: str = "opencode"
    opencode_server_password: str = ""

    # Google Fact Check
    google_fact_check_api_key: str = ""

    # Google Cloud Vision
    google_cloud_project: str = ""
    google_application_credentials: str = "/path/to/service-account.json"

    # Feature flags (all default false)
    enable_url_input: bool = False
    enable_factcheck_image_search: bool = False
    enable_local_visual_embeddings: bool = False
    enable_local_feature_matching: bool = False

    # Planner bounds (T31)
    max_web_research_tasks: int = 3
    max_queries_per_task: int = 4
