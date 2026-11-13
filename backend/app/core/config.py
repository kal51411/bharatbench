"""
Core configuration for BharatBench.
All settings read from environment variables with defaults for local development.
"""
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
import secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "BharatBench"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Security
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./bharatbench.db"
    DATABASE_ECHO: bool = False

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    # If Redis unavailable, run tasks synchronously
    CELERY_TASK_ALWAYS_EAGER: bool = True

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    # Upload
    MAX_UPLOAD_SIZE_MB: int = 10
    UPLOAD_DIR: str = "./uploads"

    # LLM Provider API Keys
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 60

    # Pagination
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    # Experiment
    DEFAULT_CONCURRENCY_LIMIT: int = 5
    DEFAULT_REQUEST_TIMEOUT: int = 60
    DEFAULT_MAX_RETRIES: int = 3

    # Admin seed user (created on first startup)
    ADMIN_USERNAME: str = "admin"
    ADMIN_EMAIL: str = "admin@bharatbench.local"
    ADMIN_PASSWORD: str = ""
    AUTO_CREATE_TABLES: bool = True

    @property
    def cors_origins(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="after")
    def validate_security_defaults(self):
        if self.ENVIRONMENT.lower() not in {"development", "test"}:
            if len(self.SECRET_KEY) < 32:
                raise ValueError("SECRET_KEY must be configured with at least 32 characters outside development")
            if len(self.ADMIN_PASSWORD) < 12:
                raise ValueError("ADMIN_PASSWORD must be configured outside development")
            self.AUTO_CREATE_TABLES = False
        else:
            if not self.SECRET_KEY:
                self.SECRET_KEY = secrets.token_urlsafe(32)
            if not self.ADMIN_PASSWORD:
                self.ADMIN_PASSWORD = "BharatBench@2024"
        return self


settings = Settings()
