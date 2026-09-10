from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="OTW_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://ordertowork:ordertowork@localhost:55432/ordertowork"
    app_url: str = "http://localhost:5173"
    auth_mode: str = "development"
    session_cookie: str = "otw_session"
    session_hours: int = 12
    demo_enabled: bool = False
    demo_session_minutes: int = Field(default=60, ge=5, le=120)
    max_daily_demo_sessions: int = Field(default=50, ge=0, le=200)
    max_demo_agent_jobs: int = Field(default=2, ge=0, le=5)
    max_daily_demo_bedrock_attempts: int = Field(default=20, ge=0, le=100)
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    cognito_domain: str = ""
    platform_admin_subjects: str = ""
    agent_mode: str = "bedrock"
    aws_region: str = "us-east-1"
    bedrock_model_id: str = ""
    bedrock_endpoint: Literal["runtime", "mantle"] = "runtime"
    bedrock_mantle_project_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,128}$")
    bedrock_max_output_tokens: int = Field(default=1024, ge=256, le=4096)
    agent_max_turns: int = Field(default=5, ge=2, le=10)
    agent_max_total_tokens: int = Field(default=18000, ge=2000, le=50000)
    max_daily_bedrock_attempts: int = Field(default=100, ge=0, le=1000)
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
