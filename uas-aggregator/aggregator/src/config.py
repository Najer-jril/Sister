"""Application configuration loaded from environment variables via pydantic-settings."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://user:pass@localhost:5432/aggregatordb"
    redis_url: str = "redis://localhost:6379"
    num_workers: int = 4
    log_level: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
