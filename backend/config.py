"""Application settings, loaded from env vars / optional .env (see .env.example).

All keys mirror HANDOFF §25. Every service/API consumes ``Settings`` (via
``app.state.settings``); no module constructs its own. Cloud-vision paths
also propagate into ``os.environ`` because google-auth (ADC) reads real
environment variables, never the .env file.
"""

import os

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

    # SerpAPI Google Lens (vision fallback; API key only, no billing account)
    serpapi_api_key: str = ""

    # Feature flags (all default false)
    enable_url_input: bool = False
    enable_factcheck_image_search: bool = False
    enable_local_visual_embeddings: bool = False
    enable_local_feature_matching: bool = False

    # Planner bounds (T31); lowered from 4 to keep live web research ~40-60s
    max_web_research_tasks: int = 3
    max_queries_per_task: int = 2

    def model_post_init(self, __context: object) -> None:
        # google-auth ADC reads env vars, not .env; make .env values effective.
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", self.google_application_credentials)
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
