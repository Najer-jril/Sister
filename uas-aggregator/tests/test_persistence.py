"""
2 tests verifying data survives connection pool lifecycle (simulates restart).
"""

import uuid

import pytest
import asyncpg

from src.consumer.processor import process_event
from src.models import EventIn, ProcessResult
from src.database import create_pool, close_pool
from src.config import settings


def make_event(topic: str, event_id: str) -> EventIn:
    return EventIn(
        topic=topic,
        event_id=event_id,
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={"persist": True},
    )


@pytest.mark.asyncio
async def test_dedup_survives_app_restart(clean_db):
    """
    Store an event via pool-1, close pool-1, reopen pool-2, re-submit same event
    → must be DUPLICATE (data persisted in Postgres, not in-memory).
    """
    eid = f"persist-{uuid.uuid4()}"
    event = make_event("persist-topic", eid)

    pool1 = await create_pool()
    r1 = await process_event(pool1, event)
    await close_pool(pool1)

    pool2 = await create_pool()
    r2 = await process_event(pool2, event)
    await close_pool(pool2)

    assert r1 == ProcessResult.PROCESSED
    assert r2 == ProcessResult.DUPLICATE


@pytest.mark.asyncio
async def test_events_survive_pool_reconnect(clean_db):
    """
    Write 5 events, close pool, reconnect, query events table — all 5 must exist.
    """
    topic = "reconnect-topic"
    eids = [f"rc-{uuid.uuid4()}" for _ in range(5)]

    pool1 = await create_pool()
    for eid in eids:
        await process_event(pool1, make_event(topic, eid))
    await close_pool(pool1)

    pool2 = await create_pool()
    rows = await pool2.fetch("SELECT event_id FROM events WHERE topic = $1", topic)
    await close_pool(pool2)

    found_ids = {r["event_id"] for r in rows}
    assert found_ids == set(eids)
