from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    APP_ENV: Literal["development", "test", "production"] = "development"
    APP_URL: str = "http://localhost:8080"
    DATABASE_URL: str = "postgresql+psycopg://cinema:cinema@postgres:5432/cinema"
    REDIS_URL: str = "redis://redis:6379/0"

    DISCORD_CLIENT_ID: str = ""
    DISCORD_CLIENT_SECRET: str = ""
    DISCORD_REDIRECT_URI: str = "http://localhost:8080/api/auth/discord/callback"
    INITIAL_SUPERADMIN_DISCORD_ID: str = ""

    PLEX_URL: str = ""
    PLEX_TOKEN: str = ""
    PLEX_SCAN_INTERVAL_MINUTES: int = 30
    MOCK_PLEX: bool = False

    SESSION_SECRET: str = "development-only-change-me"
    TOKEN_ENCRYPTION_KEY: str = "development-only-change-me-too"
    PLAYBACK_TOKEN_LIFETIME_MINUTES: int = 360

    COOKIE_SECURE: bool = False
    TRUSTED_HOSTS: str = "localhost,127.0.0.1"
    CORS_ORIGINS: str = "http://localhost:8080,http://localhost:5173"

    @field_validator("PLEX_SCAN_INTERVAL_MINUTES", "PLAYBACK_TOKEN_LIFETIME_MINUTES")
    @classmethod
    def positive_minutes(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be positive")
        return value

    @model_validator(mode="after")
    def production_safety(self) -> "Settings":
        if self.APP_ENV == "production":
            if self.MOCK_PLEX:
                raise ValueError("MOCK_PLEX cannot be enabled in production")
            if len(self.SESSION_SECRET) < 32 or len(self.TOKEN_ENCRYPTION_KEY) < 32:
                raise ValueError("production secrets must be at least 32 characters")
            if not self.COOKIE_SECURE:
                raise ValueError("COOKIE_SECURE must be true in production")
        return self

    @property
    def trusted_hosts(self) -> list[str]:
        return [item.strip() for item in self.TRUSTED_HOSTS.split(",") if item.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.CORS_ORIGINS.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
