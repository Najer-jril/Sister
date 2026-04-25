import time

class Stats:
    def __init__(self):
        self.received = 0
        self.unique_processed = 0
        self.duplicate_dropped = 0
        self.topics = set()
        self._start_time = time.time()

    def uptime(self) -> float:
        return round(time.time() - self._start_time, 2)

    def to_dict(self) -> dict:
        return {
            "received": self.received,
            "unique_processed": self.unique_processed,
            "duplicate_dropped": self.duplicate_dropped,
            "topics": list(self.topics),
            "uptime_seconds": self.uptime()
        }