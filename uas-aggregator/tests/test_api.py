"""
4 tests for API endpoint correctness.
Uses httpx AsyncClient backed by the FastAPI app (no real Redis needed for /events & /stats).
"""

import uuid

import pytest

from src.consumer.processor import process_event
from src.models import EventIn

VALID_EVENT = {
    "topic": "api-test",
    "event_id": "evt-api-001",
    "timestamp": "2024-06-01T12:00:00",
    "source": "pytest",
    "payload": {"key": "value"},
}


@pytest.mark.asyncio
async def test_post_single_event_returns_202(client, clean_db):
    """Single event POST must return HTTP 202 with queued == 1."""
    event = {**VALID_EVENT, "event_id": f"api-single-{uuid.uuid4()}"}
    resp = await client.post("/publish", json=event)
    assert resp.status_code == 202
    assert resp.json()["queued"] == 1


@pytest.mark.asyncio
async def test_post_batch_events_returns_202(client, clean_db):
    """Batch POST of 5 events must return HTTP 202 with queued == 5."""
    batch = [
        {**VALID_EVENT, "event_id": f"api-batch-{uuid.uuid4()}"}
        for _ in range(5)
    ]
    resp = await client.post("/publish", json=batch)
    assert resp.status_code == 202
    assert resp.json()["queued"] == 5


@pytest.mark.asyncio
async def test_get_events_filters_by_topic(client, db_pool, clean_db):
    """GET /events?topic=X returns only events for that topic."""
    e1 = EventIn(
        topic="topic-filter-a",
        event_id=f"fa-{uuid.uuid4()}",
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={},
    )
    e2 = EventIn(
        topic="topic-filter-b",
        event_id=f"fb-{uuid.uuid4()}",
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={},
    )
    await process_event(db_pool, e1)
    await process_event(db_pool, e2)

    resp = await client.get("/events?topic=topic-filter-a")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["topic"] == "topic-filter-a"


@pytest.mark.asyncio
async def test_get_stats_fields_present(client, db_pool, clean_db):
    """GET /stats must contain: received, unique_processed, duplicate_dropped, topics, uptime_seconds."""
    e = EventIn(
        topic="stats-topic",
        event_id=f"st-{uuid.uuid4()}",
        timestamp="2024-01-01T00:00:00",
        source="pytest",
        payload={},
    )
    await process_event(db_pool, e)

    resp = await client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()

    for field in ("received", "unique_processed", "duplicate_dropped", "topics", "uptime_seconds"):
        assert field in data, f"Missing field: {field}"

    assert data["received"] >= 1
    assert "stats-topic" in data["topics"]
