"""Dedup + transactional event processing logic backed by PostgreSQL."""

import json

import asyncpg

from src.models import EventIn, ProcessResult


async def process_event(pool: asyncpg.Pool, event: EventIn) -> ProcessResult:
    """
    Atomically insert event and update stats in a single READ COMMITTED transaction.
    The UNIQUE constraint on (topic, event_id) is the authoritative dedup mechanism —
    concurrent inserts from multiple workers are safe without application-level locking.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """
                INSERT INTO events (topic, event_id, timestamp, source, payload)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (topic, event_id) DO NOTHING
                """,
                event.topic,
                event.event_id,
                event.timestamp,
                event.source,
                json.dumps(event.payload),
            )
            inserted = result == "INSERT 0 1"

            if inserted:
                await conn.execute(
                    "UPDATE stats SET received = received + 1, "
                    "unique_processed = unique_processed + 1 WHERE id = 1"
                )
            else:
                await conn.execute(
                    "UPDATE stats SET received = received + 1, "
                    "duplicate_dropped = duplicate_dropped + 1 WHERE id = 1"
                )

            return ProcessResult.PROCESSED if inserted else ProcessResult.DUPLICATE
