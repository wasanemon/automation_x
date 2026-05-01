from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://growth:growth@localhost:5432/growth_agent"

    postiz_base_url: str = "http://localhost:5000/public/v1"
    postiz_api_key: str = ""
    postiz_x_integration_id: str = ""

    x_api_base_url: str = "https://api.x.com"
    x_bearer_token: str = ""
    x_user_id: str = ""

    http_timeout_seconds: float = Field(default=10, ge=1, le=60)
    http_max_retries: int = Field(default=2, ge=0, le=5)
    duplicate_similarity_threshold: float = Field(default=0.88, ge=0.5, le=1)
    auto_schedule_score_threshold: int = Field(default=80, ge=0, le=100)


@lru_cache
def get_settings() -> Settings:
    return Settings()
