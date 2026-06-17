"""
4 tests covering deduplication correctness.
Dedup is enforced by Postgres UNIQUE (topic, event_id) — not application layer.
"""

import asyncio
import uuid

import pytest

from src.consumer.processor import process_event
from src.models import EventIn, ProcessResult

BASE_EVENT = {
    "topic": "test",
    "event_id": "evt-dedup-001",
    "timestamp": "2024-01-01T00:00:00",
    "source": "pytest",
    "payload": {"x": 1},
}


def make_event(**overrides) -> EventIn:
    data = {**BASE_EVENT, **overrides}
    return EventIn(**data)


@pytest.mark.asyncio
async def test_single_duplicate_dropped(db_pool, clean_db):
    """POST same event_id twice — second must be DUPLICATE; stats.duplicate_dropped == 1."""
    event = make_event(event_id=f"evt-{uuid.uuid4()}")

    r1 = await process_event(db_pool, event)
    r2 = await process_event(db_pool, event)

    assert r1 == ProcessResult.PROCESSED
    assert r2 == ProcessResult.DUPLICATE

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["duplicate_dropped"] == 1
    assert row["unique_processed"] == 1


@pytest.mark.asyncio
async def test_batch_with_duplicates(db_pool, clean_db):
    """10-event batch where 3 are duplicates → unique_processed == 7."""
    base_ids = [f"evt-{uuid.uuid4()}" for _ in range(7)]
    dup_ids = base_ids[:3]
    all_ids = base_ids + dup_ids

    for eid in all_ids:
        await process_event(db_pool, make_event(event_id=eid))

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["unique_processed"] == 7
    assert row["duplicate_dropped"] == 3


@pytest.mark.asyncio
async def test_dedup_across_topics(db_pool, clean_db):
    """Same event_id but different topic → both must be PROCESSED (not duplicates)."""
    eid = f"shared-{uuid.uuid4()}"
    e1 = make_event(topic="topic-alpha", event_id=eid)
    e2 = make_event(topic="topic-beta", event_id=eid)

    r1 = await process_event(db_pool, e1)
    r2 = await process_event(db_pool, e2)

    assert r1 == ProcessResult.PROCESSED
    assert r2 == ProcessResult.PROCESSED

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["unique_processed"] == 2
    assert row["duplicate_dropped"] == 0


@pytest.mark.asyncio
async def test_idempotent_under_concurrent_workers(db_pool, clean_db):
    """20 concurrent POSTs of the same event_id → unique_processed == 1, no DB error."""
    event = make_event(event_id=f"concurrent-{uuid.uuid4()}")

    results = await asyncio.gather(*[process_event(db_pool, event) for _ in range(20)])

    processed = sum(1 for r in results if r == ProcessResult.PROCESSED)
    duplicates = sum(1 for r in results if r == ProcessResult.DUPLICATE)

    assert processed == 1
    assert duplicates == 19

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["unique_processed"] == 1
    assert row["duplicate_dropped"] == 19
