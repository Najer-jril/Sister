import asyncio
import logging
from src.dedup_store import DedupStore
from src.models import Event

logger = logging.getLogger(__name__)

class QueueManager:
    def __init__(self, dedup_store: DedupStore, stats):
        self.queue = asyncio.Queue()
        self.dedup_store = dedup_store
        self.stats = stats
        self.processed_events: dict[str, list] = {}

    async def publish(self, event: Event):
        self.stats.received += 1
        await self.queue.put(event)

    async def start_consumer(self):
        while True:
            event = await self.queue.get()
            
            is_new_event = self.dedup_store.register_event(event.topic, event.event_id)

            if not is_new_event:
                logger.warning(
                    f"[DUPLICATE DROPPED] topic={event.topic} event_id={event.event_id}"
                )
                self.stats.duplicate_dropped += 1
            else:
                # Jika True, event berhasil masuk database
                self.stats.unique_processed += 1
                self.stats.topics.add(event.topic)
                self.processed_events.setdefault(event.topic, []).append(
                    event.model_dump()
                )
                logger.info(
                    f"[PROCESSED] topic={event.topic} event_id={event.event_id}"
                )
            
            self.queue.task_done()