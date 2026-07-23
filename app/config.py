"""Environment-backed application settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./polymarket_dashboard.db"
    dashboard_password_hash: str = ""
    dashboard_dev_password: str = "admin"
    session_secret: str = "development-session-secret-change-me"
    live_trading_enabled: bool = False
    auto_claim_enabled: bool = False
    claim_poll_seconds: int = 5
    claim_resolution_retry_seconds: int = 30
    claim_retry_base_seconds: int = 15
    claim_max_attempts: int = 5
    claim_reconcile_seconds: int = 600
    service_role: str = "web"
    timezone: str = Field(default="Asia/Ho_Chi_Minh", alias="TZ")
    event_retention_days: int = 30
    session_max_age: int = 43_200

    @property
    def production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
