from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./dentacare.db"
    jwt_secret: str = "development-only-change-me"
    environment: str = "development"
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    cors_origins: str = "http://localhost:5173,https://5173-iq44yzwn2tim322rhv7dl-e61fa79d.us1.manus.computer"
    # T11 protected clinical documents. Must be an absolute directory outside the repository
    # and outside any static/public directory; unset means document upload/download is disabled.
    document_storage_dir: str | None = None
    document_max_bytes: int = 20 * 1024 * 1024
    # T31: STL and DICOM files are larger than photos; the T11 limit above is unchanged for everything else.
    imaging_max_bytes: int = 100 * 1024 * 1024
    document_max_image_pixels: int = 50_000_000
    document_max_image_side: int = 12_000
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        forbidden = {"", "development-only-change-me", "change-me", "secret", "test-secret"}
        if self.environment.lower() not in {"development", "test"} and self.jwt_secret in forbidden:
            raise ValueError("JWT_SECRET must be a non-placeholder secret outside development")
        if self.document_max_bytes < 1 or self.imaging_max_bytes < 1 or self.document_max_image_pixels < 1 or self.document_max_image_side < 1:
            raise ValueError("Document limits must be positive")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
