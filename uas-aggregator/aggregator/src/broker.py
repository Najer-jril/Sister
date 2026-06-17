"""Redis client wrapper for the event queue broker."""

import redis.asyncio as aioredis

from src.config import settings

QUEUE_KEY = "event_queue"


async def create_redis() -> aioredis.Redis:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    await client.ping()
    return client


async def close_redis(client: aioredis.Redis) -> None:
    await client.aclose()
