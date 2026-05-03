import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

@dataclass
class AppConfig:
    """
    Typed configuration dataclass storing all environmental settings needed for a node.
    """
    node_id: str
    node_host: str
    node_port: int
    redis_url: str
    metrics_port: int
    election_timeout_min_ms: int
    election_timeout_max_ms: int
    heartbeat_interval_ms: int


def load_config(env_file_path: Optional[str] = None) -> AppConfig:
    """
    Loads environment variables from a .env file and returns a populated AppConfig instance.

    Args:
        env_file_path: Optional path to a specific .env file.

    Returns:
        AppConfig: The typed configuration object with parsed values.
    """
    if env_file_path:
        load_dotenv(dotenv_path=env_file_path)
    else:
        load_dotenv()

    return AppConfig(
        node_id=os.getenv("NODE_ID", "node_default"),
        node_host=os.getenv("NODE_HOST", "0.0.0.0"),
        node_port=int(os.getenv("NODE_PORT", "8080")),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        metrics_port=int(os.getenv("METRICS_PORT", "9090")),
        election_timeout_min_ms=int(os.getenv("ELECTION_TIMEOUT_MIN_MS", "150")),
        election_timeout_max_ms=int(os.getenv("ELECTION_TIMEOUT_MAX_MS", "300")),
        heartbeat_interval_ms=int(os.getenv("HEARTBEAT_INTERVAL_MS", "50"))
    )