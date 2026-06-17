"""Async event simulator: generates TOTAL_EVENTS with DUPLICATE_RATE fraction of duplicates."""

import asyncio
import random
import time
import uuid
from datetime import datetime, timezone

import httpx

from src.config import settings

TOPICS = ["auth", "payment", "inventory", "user", "order", "notification"]


def make_event(topic: str, event_id: str, batch: int) -> dict:
    return {
        "topic": topic,
        "event_id": event_id,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "publisher",
        "payload": {"value": random.random(), "batch": batch},
    }


async def send_batch(client: httpx.AsyncClient, events: list, semaphore: asyncio.Semaphore) -> int:
    async with semaphore:
        try:
            resp = await client.post(settings.target_url, json=events, timeout=30.0)
            resp.raise_for_status()
            return len(events)
        except Exception as exc:
            print(f"[publisher] send error: {exc}")
            return 0


async def main() -> None:
    unique_count = settings.total_events
    dup_count = int(unique_count * settings.duplicate_rate)
    total = unique_count + dup_count

    # Generate unique events
    unique_events: list[dict] = []
    for i in range(unique_count):
        topic = random.choice(TOPICS)
        event_id = f"{topic}-{uuid.uuid4()}"
        unique_events.append(make_event(topic, event_id, batch=i // settings.batch_size))

    # Build duplicate pool by re-picking from existing events
    dup_events: list[dict] = []
    for i in range(dup_count):
        original = random.choice(unique_events)
        dup_events.append(
            make_event(original["topic"], original["event_id"], batch=-1)
        )

    all_events = unique_events + dup_events
    random.shuffle(all_events)

    # Chunk into batches
    batches = [
        all_events[i : i + settings.batch_size]
        for i in range(0, len(all_events), settings.batch_size)
    ]

    semaphore = asyncio.Semaphore(settings.concurrency)
    start = time.perf_counter()
    sent = 0
    log_interval = max(1, 1000 // settings.batch_size)

    async with httpx.AsyncClient() as client:
        tasks = [send_batch(client, batch, semaphore) for batch in batches]
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            count = await coro
            sent += count
            if (i + 1) % log_interval == 0:
                elapsed = time.perf_counter() - start
                rate = sent / elapsed if elapsed > 0 else 0
                print(f"[publisher] sent={sent}/{total}  rate={rate:.0f} ev/s")

    elapsed = time.perf_counter() - start
    rate = total / elapsed if elapsed > 0 else 0
    print(
        f"\n[publisher] DONE — total={total}  unique={unique_count}  "
        f"duplicates_injected={dup_count}  elapsed={elapsed:.2f}s  rate={rate:.0f} ev/s"
    )


if __name__ == "__main__":
    asyncio.run(main())
