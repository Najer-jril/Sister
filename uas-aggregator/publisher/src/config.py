"""Publisher configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    target_url: str = "http://localhost:8080/publish"
    total_events: int = 20000
    duplicate_rate: float = 0.30
    concurrency: int = 10
    batch_size: int = 50

    class Config:
        env_file = ".env"


settings = Settings()
