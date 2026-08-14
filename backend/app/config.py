"""
Central configuration for the backend.

All secrets come from environment variables (.env locally, or the
Render dashboard in production). Nothing sensitive is hard-coded.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- Supabase ---
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str  # backend-only, never sent to frontend

    # --- App ---
    environment: str = "development"
    cors_allowed_origins: str = "http://localhost:8080,https://your-netlify-site.netlify.app"

    # --- Data ingestion ---
    nse_request_timeout_seconds: int = 30
    nse_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    min_history_days_for_v1: int = 15

    # --- Scoring defaults (mirrored in DB scan_config; env values act as
    #     a fallback if the config table hasn't been seeded yet) ---
    default_score_strong_min: int = 6
    default_score_moderate_min: int = 4
    default_score_weak_min: int = 2

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
