"""
2 performance tests: batch throughput and full 20K event load with 30% duplicates.
"""

import asyncio
import time
import uuid

import pytest

from src.consumer.processor import process_event
from src.models import EventIn, ProcessResult


def make_event(topic: str, event_id: str) -> EventIn:
    return EventIn(
        topic=topic,
        event_id=event_id,
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={"perf": True},
    )


@pytest.mark.asyncio
async def test_batch_throughput(db_pool, clean_db):
    """1000 unique events processed concurrently must complete in under 10 seconds."""
    events = [make_event("perf-batch", f"pb-{uuid.uuid4()}") for _ in range(1000)]

    start = time.perf_counter()
    await asyncio.gather(*[process_event(db_pool, e) for e in events])
    elapsed = time.perf_counter() - start

    row = await db_pool.fetchrow("SELECT unique_processed FROM stats WHERE id = 1")
    assert row["unique_processed"] == 1000
    assert elapsed < 10, f"Throughput too slow: {elapsed:.2f}s for 1000 events"


@pytest.mark.asyncio
async def test_20k_events_with_30pct_dup(db_pool, clean_db):
    """
    20 000 events (14 000 unique + 6 000 duplicates) processed concurrently.
    Asserts: unique_processed == 14000, duplicate_dropped == 6000, wall time < 120s.
    """
    unique_count = 14000
    dup_count = 6000

    unique_events = [make_event("perf-20k", f"u-{uuid.uuid4()}") for _ in range(unique_count)]
    dup_events = [make_event(e.topic, e.event_id) for e in unique_events[:dup_count]]
    all_events = unique_events + dup_events

    start = time.perf_counter()

    # Process in chunks to avoid overwhelming the pool
    chunk_size = 500
    for i in range(0, len(all_events), chunk_size):
        chunk = all_events[i : i + chunk_size]
        await asyncio.gather(*[process_event(db_pool, e) for e in chunk])

    elapsed = time.perf_counter() - start

    row = await db_pool.fetchrow("SELECT * FROM stats WHERE id = 1")
    assert row["unique_processed"] == unique_count, f"Expected {unique_count}, got {row['unique_processed']}"
    assert row["duplicate_dropped"] == dup_count, f"Expected {dup_count}, got {row['duplicate_dropped']}"
    assert elapsed < 120, f"Too slow: {elapsed:.2f}s"
    print(f"\n[perf] 20K events done in {elapsed:.2f}s ({20000/elapsed:.0f} ev/s)")
