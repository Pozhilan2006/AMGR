from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AMGR API"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://amgr:amgr_dev_only@localhost:5432/amgr"
    ai_operator_provider: str = Field(
        default="openai", validation_alias="AI_OPERATOR_PROVIDER"
    )
    ai_operator_model: str = Field(
        default="gpt-4o-mini", validation_alias="AI_OPERATOR_MODEL"
    )
    ai_operator_base_url: str | None = Field(
        default=None, validation_alias="AI_OPERATOR_BASE_URL"
    )
    ai_operator_api_key: SecretStr | None = Field(
        default=None, validation_alias="AI_OPERATOR_API_KEY"
    )
    ai_operator_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        validation_alias="AI_OPERATOR_TIMEOUT_SECONDS",
    )
    ai_operator_max_response_tokens: int = Field(
        default=1200,
        gt=0,
        le=8192,
        validation_alias="AI_OPERATOR_MAX_RESPONSE_TOKENS",
    )
    ai_operator_temperature: float = Field(
        default=0.2, ge=0, le=2, validation_alias="AI_OPERATOR_TEMPERATURE"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()