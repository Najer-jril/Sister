"""
Pytest fixtures: asyncpg pool, FastAPI test client, and clean_db helper.
Tests assume a running PostgreSQL instance reachable via DATABASE_URL env var.
"""

import os
import sys

# Support both: running inside container (/app) and from host (../aggregator)
_aggregator_path = os.path.join(os.path.dirname(__file__), "..", "aggregator")
if os.path.isdir(os.path.join(_aggregator_path, "src")):
    sys.path.insert(0, _aggregator_path)
else:
    sys.path.insert(0, "/app")

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@storage:5432/aggregatordb")
os.environ.setdefault("REDIS_URL", "redis://broker:6379")
os.environ.setdefault("NUM_WORKERS", "4")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.database import create_pool
from src.main import app


@pytest_asyncio.fixture
async def db_pool():
    pool = await create_pool()
    yield pool
    await pool.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_pool):
    """Truncate events and reset stats before every test."""
    await db_pool.execute("TRUNCATE TABLE events RESTART IDENTITY CASCADE")
    await db_pool.execute(
        "UPDATE stats SET received=0, unique_processed=0, duplicate_dropped=0 WHERE id=1"
    )
    yield


@pytest_asyncio.fixture
async def client(db_pool):
    """httpx AsyncClient backed by the FastAPI app with shared DB pool."""
    app.state.db_pool = db_pool

    import redis.asyncio as aioredis
    redis_client = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    app.state.redis = redis_client
    app.state.worker_tasks = []

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await redis_client.aclose()
