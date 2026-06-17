"""asyncpg connection pool setup and PostgreSQL schema initialization."""

import asyncpg

from src.config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id            BIGSERIAL PRIMARY KEY,
    topic         TEXT        NOT NULL,
    event_id      TEXT        NOT NULL,
    timestamp     TIMESTAMPTZ NOT NULL,
    source        TEXT        NOT NULL,
    payload       JSONB       NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_topic_event_id UNIQUE (topic, event_id)
);

CREATE TABLE IF NOT EXISTS stats (
    id                  INT         PRIMARY KEY DEFAULT 1,
    received            BIGINT      NOT NULL DEFAULT 0,
    unique_processed    BIGINT      NOT NULL DEFAULT 0,
    duplicate_dropped   BIGINT      NOT NULL DEFAULT 0,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (id = 1)
);

INSERT INTO stats (id) VALUES (1) ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_received_at ON events(received_at DESC);
"""


async def create_pool() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    return pool


async def close_pool(pool: asyncpg.Pool) -> None:
    await pool.close()
