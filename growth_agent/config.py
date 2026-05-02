from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    testing: bool = False
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://growth:growth@localhost:5432/growth_agent"

    growth_agent_api_key: str = ""
    safe_public_reads: bool = False
    auto_apply_tentative_rules: bool = False

    scheduling_dry_run: bool = True
    postiz_base_url: str = ""
    postiz_api_key: str = ""
    postiz_x_integration_id: str = ""
    test_x_account_handle: str = ""
    owned_domains: str = ""

    x_api_base_url: str = "https://api.x.com"
    x_bearer_token: str = ""
    x_user_id: str = ""
    x_reconcile_lookback_hours: int = Field(default=48, ge=1, le=24 * 30)
    x_reconcile_text_similarity_threshold: float = Field(default=0.82, ge=0.5, le=1)

    request_timeout_seconds: float = Field(
        default=10,
        ge=1,
        le=60,
        validation_alias=AliasChoices("REQUEST_TIMEOUT_SECONDS", "HTTP_TIMEOUT_SECONDS"),
    )
    max_external_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        validation_alias=AliasChoices("MAX_EXTERNAL_RETRIES", "HTTP_MAX_RETRIES"),
    )
    duplicate_similarity_threshold: float = Field(default=0.88, ge=0.5, le=1)
    auto_schedule_score_threshold: int = Field(default=80, ge=0, le=100)

    @property
    def owned_domain_list(self) -> tuple[str, ...]:
        return tuple(
            domain.strip().lower().removeprefix("www.")
            for domain in self.owned_domains.split(",")
            if domain.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
