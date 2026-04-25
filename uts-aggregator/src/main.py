import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from typing import Union, List
from src.models import Event
from src.dedup_store import DedupStore
from src.queue_manager import QueueManager
from src.stats import Stats

logging.basicConfig(level=logging.INFO)

stats = Stats()
dedup_store = DedupStore(db_path=os.getenv("DEDUP_DB_PATH", "data/dedup.db"))
queue_manager = QueueManager(dedup_store=dedup_store, stats=stats)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    task = asyncio.create_task(queue_manager.start_consumer())
    yield
    # shutdown
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/publish")
async def publish(events: Union[List[Event], Event]):
    if isinstance(events, Event):
        events = [events]
    for event in events:
        await queue_manager.publish(event)
    return {"status": "queued", "count": len(events)}

@app.get("/events")
async def get_events(topic: str = Query(...)):
    return queue_manager.processed_events.get(topic, [])

@app.get("/stats")
async def get_stats():
    return stats.to_dict()