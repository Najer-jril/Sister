"""
3 tests verifying ACID transaction behaviour and concurrent worker correctness.
"""

import asyncio
import uuid

import pytest

from src.consumer.processor import process_event
from src.models import EventIn, ProcessResult

TOPIC = "tx-test"


def make_event(event_id: str) -> EventIn:
    return EventIn(
        topic=TOPIC,
        event_id=event_id,
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={"v": 1},
    )


@pytest.mark.asyncio
async def test_concurrent_workers_no_double_process(db_pool, clean_db):
    """4 workers, 100 events (50 unique + 50 dup) → received==100, unique==50."""
    unique_ids = [f"u-{uuid.uuid4()}" for _ in range(50)]
    dup_ids = unique_ids[:50]
    all_ids = unique_ids + dup_ids

    results = await asyncio.gather(*[process_event(db_pool, make_event(eid)) for eid in all_ids])

    processed = sum(1 for r in results if r == ProcessResult.PROCESSED)
    duplicates = sum(1 for r in results if r == ProcessResult.DUPLICATE)

    assert processed + duplicates == 100
    assert processed == 50

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["received"] == 100
    assert row["unique_processed"] == 50
    assert row["duplicate_dropped"] == 50


@pytest.mark.asyncio
async def test_stats_consistency_under_load(db_pool, clean_db):
    """1000 events through concurrent workers: received == unique_processed + duplicate_dropped."""
    ids = [f"load-{uuid.uuid4()}" for _ in range(700)]
    dup_pool = ids[:300]
    all_ids = ids + dup_pool

    await asyncio.gather(*[process_event(db_pool, make_event(eid)) for eid in all_ids])

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["received"] == row["unique_processed"] + row["duplicate_dropped"]
    assert row["received"] == 1000


@pytest.mark.asyncio
async def test_atomic_stats_no_lost_update(db_pool, clean_db):
    """
    Parallel increments via direct DB process_event calls.
    SQL arithmetic (count + 1) prevents lost-update without application-level locking.
    """
    n = 200
    events = [make_event(f"atomic-{uuid.uuid4()}") for _ in range(n)]

    await asyncio.gather(*[process_event(db_pool, e) for e in events])

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    # All events are unique, so received == unique_processed == n
    assert row["received"] == n
    assert row["unique_processed"] == n
    assert row["duplicate_dropped"] == 0
