from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OTW_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://ordertowork:ordertowork@localhost:55432/ordertowork"
    app_url: str = "http://localhost:5173"
    auth_mode: str = "development"
    session_cookie: str = "otw_session"
    session_hours: int = 12
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""
    platform_admin_subjects: str = ""
    agent_mode: str = "bedrock"
    aws_region: str = "us-east-1"
    bedrock_model_id: str = ""
    storage_mode: str = "local"
    s3_bucket: str = ""
    data_dir: Path = Path(".data")
    worker_poll_seconds: float = 2
    worker_lease_seconds: int = 180
    max_job_attempts: int = 3
    agent_timeout_seconds: int = 120
    max_daily_agent_jobs: int = 100
    max_upload_bytes: int = 5 * 1024 * 1024
    frontend_dist: Path = Path("frontend/dist")

    @model_validator(mode="after")
    def production_is_explicit(self):
        if self.environment == "production":
            if self.auth_mode != "cognito":
                raise ValueError("Production requires Cognito authentication")
            if not self.app_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS app URL")
            if self.storage_mode != "s3":
                raise ValueError("Production requires private S3 storage")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
