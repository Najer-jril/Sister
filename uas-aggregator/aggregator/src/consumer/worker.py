"""Async worker pool: each worker BLPOPs from Redis and processes events via processor."""

import asyncio
import logging

from src.broker import QUEUE_KEY
from src.consumer.processor import process_event
from src.models import EventIn

logger = logging.getLogger(__name__)


async def worker_loop(app_state, worker_id: int) -> None:
    """BLPOP one event at a time → process → repeat. No ack needed: Postgres UNIQUE is source of truth."""
    while True:
        try:
            raw = await app_state.redis.blpop(QUEUE_KEY, timeout=1)
            if raw is None:
                continue
            _, data = raw
            event = EventIn.model_validate_json(data)
            result = await process_event(app_state.db_pool, event)
            logger.info(
                "worker=%d event_id=%s topic=%s result=%s",
                worker_id,
                event.event_id,
                event.topic,
                result.value,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("worker=%d error=%s", worker_id, exc)
            await asyncio.sleep(0.1)


async def start_workers(app_state) -> None:
    """Spawn NUM_WORKERS concurrent consumer tasks attached to app_state."""
    from src.config import settings

    tasks = [
        asyncio.create_task(worker_loop(app_state, worker_id=i))
        for i in range(settings.num_workers)
    ]
    app_state.worker_tasks = tasks
    logger.info("Started %d consumer workers", settings.num_workers)


async def stop_workers(app_state) -> None:
    """Cancel all worker tasks and wait for clean shutdown."""
    tasks = getattr(app_state, "worker_tasks", [])
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All consumer workers stopped")
