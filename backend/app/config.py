from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./dentacare.db"
    jwt_secret: str = "development-only-change-me"
    environment: str = "development"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    cors_origins: str = "http://localhost:5173"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        forbidden = {"", "development-only-change-me", "change-me", "secret", "test-secret"}
        if self.environment.lower() not in {"development", "test"} and self.jwt_secret in forbidden:
            raise ValueError("JWT_SECRET must be a non-placeholder secret outside development")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
