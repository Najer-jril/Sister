import pytest
import asyncio
import random
import time
import uuid
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Query
from typing import Union, List
from src.models import Event
from src.dedup_store import DedupStore
from src.stats import Stats
from src.queue_manager import QueueManager

VALID_EVENT = {
    "topic": "test",
    "event_id": "evt-001",
    "timestamp": "2024-01-01T00:00:00",
    "source": "pytest",
    "payload": {"msg": "hello"}
}

def make_app(db_path=":memory:"):
    """Buat fresh app instance untuk setiap test."""
    _stats = Stats()
    _dedup = DedupStore(db_path=db_path)
    _qm = QueueManager(dedup_store=_dedup, stats=_stats)

    _app = FastAPI()

    @_app.post("/publish")
    async def publish(events: Union[List[Event], Event]):
        if isinstance(events, Event):
            events = [events]
        for event in events:
            await _qm.publish(event)
        return {"status": "queued", "count": len(events)}

    @_app.get("/events")
    async def get_events(topic: str = Query(...)):
        return _qm.processed_events.get(topic, [])

    @_app.get("/stats")
    async def get_stats():
        return _stats.to_dict()

    return _app, _qm, _stats, _dedup

#  1. Validasi skema
@pytest.mark.asyncio
async def test_schema_valid():
    app, qm, _, _ = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/publish", json=VALID_EVENT)
        assert res.status_code == 200
        assert res.json()["status"] == "queued"

@pytest.mark.asyncio
async def test_schema_missing_topic():
    app, _, _, _ = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = {k: v for k, v in VALID_EVENT.items() if k != "topic"}
        res = await client.post("/publish", json=bad)
        assert res.status_code == 422

@pytest.mark.asyncio
async def test_schema_missing_event_id():
    app, _, _, _ = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = {k: v for k, v in VALID_EVENT.items() if k != "event_id"}
        res = await client.post("/publish", json=bad)
        assert res.status_code == 422

@pytest.mark.asyncio
async def test_schema_invalid_timestamp():
    app, _, _, _ = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        bad = {**VALID_EVENT, "timestamp": "bukan-tanggal"}
        res = await client.post("/publish", json=bad)
        assert res.status_code == 422

#  2. Deduplication
@pytest.mark.asyncio
async def test_dedup_drops_duplicate():
    app, qm, stats, _ = make_app()
    task = asyncio.create_task(qm.start_consumer())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(3):
                await client.post("/publish", json=VALID_EVENT)
            await asyncio.sleep(0.5)
            assert stats.unique_processed == 1
            assert stats.duplicate_dropped == 2
            assert stats.received == 3
    finally:
        task.cancel()
        await asyncio.sleep(0)

#  3. GET /events konsisten ──
@pytest.mark.asyncio
async def test_events_returns_unique_only():
    app, qm, _, _ = make_app()
    task = asyncio.create_task(qm.start_consumer())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/publish", json=VALID_EVENT)
            await client.post("/publish", json=VALID_EVENT)
            await client.post("/publish", json={**VALID_EVENT, "event_id": "evt-002"})
            await asyncio.sleep(0.5)

            res = await client.get("/events?topic=test")
            assert res.status_code == 200
            assert len(res.json()) == 2
    finally:
        task.cancel()
        await asyncio.sleep(0)

#  4. GET /stats konsisten 
@pytest.mark.asyncio
async def test_stats_consistency():
    app, qm, stats, _ = make_app()
    task = asyncio.create_task(qm.start_consumer())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/publish", json=VALID_EVENT)
            await client.post("/publish", json=VALID_EVENT)
            await client.post("/publish", json={**VALID_EVENT, "event_id": "evt-002"})
            await asyncio.sleep(0.5)

            res = await client.get("/stats")
            data = res.json()
            assert data["received"] == 3
            assert data["unique_processed"] == 2
            assert data["duplicate_dropped"] == 1
            assert "test" in data["topics"]
            assert data["uptime_seconds"] >= 0
    finally:
        task.cancel()
        await asyncio.sleep(0)
#5 . DedupStore persistence
def test_dedup_store_persistence(tmp_path):
    db_path = str(tmp_path / "test_dedup.db")

    # Instance pertama — simpan event
    store1 = DedupStore(db_path=db_path)
    result = store1.register_event("topic-a", "evt-persist-001")
    assert result is True  # event baru, berhasil disimpan

    # Instance kedua — simulasi restart
    store2 = DedupStore(db_path=db_path)
    result_dup = store2.register_event("topic-a", "evt-persist-001")
    assert result_dup is False  # sudah ada, duplikat

    result_new = store2.register_event("topic-a", "evt-belum-ada")
    assert result_new is True  # event baru, berhasil

# 6. Batch publish
@pytest.mark.asyncio
async def test_batch_publish():
    app, qm, stats, _ = make_app()
    task = asyncio.create_task(qm.start_consumer())
    try:
        batch = [{**VALID_EVENT, "event_id": f"evt-batch-{i}"} for i in range(10)]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/publish", json=batch)
            assert res.status_code == 200
            assert res.json()["count"] == 10
            await asyncio.sleep(0.5)
            assert stats.unique_processed == 10
    finally:
        task.cancel()
        await asyncio.sleep(0)

#  7. Stress kecil─
@pytest.mark.asyncio
async def test_stress_small():
    app, qm, stats, _ = make_app()
    task = asyncio.create_task(qm.start_consumer())
    try:
        unique_ids = [str(uuid.uuid4()) for _ in range(80)]
        dup_ids = unique_ids[:20]
        all_ids = unique_ids + dup_ids
        random.shuffle(all_ids)

        batch = [
            {**VALID_EVENT, "event_id": eid, "topic": "stress"}
            for eid in all_ids
        ]

        start = time.time()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/publish", json=batch)
            assert res.status_code == 200

        await asyncio.sleep(1)
        elapsed = time.time() - start

        assert stats.unique_processed == 80
        assert stats.duplicate_dropped == 20
        assert elapsed < 5
    finally:
        task.cancel()
        await asyncio.sleep(0)