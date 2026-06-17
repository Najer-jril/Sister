"""In-process metric counters for observability (supplementary to DB stats)."""

import threading


class Metrics:
    """Thread-safe in-process counters. DB stats are authoritative; these are fast-path gauges."""

    def __init__(self):
        self._lock = threading.Lock()
        self.enqueued = 0
        self.processed = 0
        self.duplicates = 0

    def inc_enqueued(self, n: int = 1) -> None:
        with self._lock:
            self.enqueued += n

    def inc_processed(self) -> None:
        with self._lock:
            self.processed += 1

    def inc_duplicate(self) -> None:
        with self._lock:
            self.duplicates += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "enqueued": self.enqueued,
                "processed": self.processed,
                "duplicates": self.duplicates,
            }


metrics = Metrics()
