"""POST /publish — accept single event or batch and push to Redis queue."""

import json
from typing import List, Union

from fastapi import APIRouter, Request

from src.broker import QUEUE_KEY
from src.models import EventIn

router = APIRouter()


@router.post("/publish", status_code=202)
async def publish(
    request: Request,
    body: Union[List[EventIn], EventIn],
):
    """Push event(s) to Redis queue for async processing by consumer workers."""
    events = body if isinstance(body, list) else [body]
    pipe = request.app.state.redis.pipeline()
    for event in events:
        pipe.lpush(QUEUE_KEY, event.model_dump_json())
    await pipe.execute()
    return {"queued": len(events)}
