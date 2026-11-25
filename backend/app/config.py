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
    https_enabled: bool = False
    json_logs: bool = True
    login_attempt_retention_days: int = 90
    patient_access_log_retention_days: int = 365
    # T11 protected clinical documents. Must be an absolute directory outside the repository
    # and outside any static/public directory; unset means document upload/download is disabled.
    document_storage_dir: str | None = None
    document_max_bytes: int = 20 * 1024 * 1024
    document_max_image_pixels: int = 50_000_000
    document_max_image_side: int = 12_000
    # T15 patient communication: the adapter is picked here; only "simulated" (sends nothing outside) ships.
    comms_adapter: str = "simulated"
    comms_max_attempts: int = 3
    comms_retry_minutes: int = 15
    import_max_bytes: int = 5 * 1024 * 1024
    import_max_rows: int = 2_000
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        forbidden = {"", "development-only-change-me", "change-me", "secret", "test-secret"}
        if self.environment.lower() not in {"development", "test"} and self.jwt_secret in forbidden:
            raise ValueError("JWT_SECRET must be a non-placeholder secret outside development")
        if self.document_max_bytes < 1 or self.document_max_image_pixels < 1 or self.document_max_image_side < 1:
            raise ValueError("Document limits must be positive")
        if self.import_max_bytes < 1 or self.import_max_rows < 1:
            raise ValueError("Import limits must be positive")
        if self.comms_max_attempts < 1 or self.comms_retry_minutes < 1:
            raise ValueError("Communication retry limits must be positive")
        if self.login_attempt_retention_days < 1 or self.patient_access_log_retention_days < 1:
            raise ValueError("Security history retention must be at least one day")
        if "*" in {origin.strip() for origin in self.cors_origins.split(",")}:
            raise ValueError("Wildcard CORS origins are incompatible with credentialed requests")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
