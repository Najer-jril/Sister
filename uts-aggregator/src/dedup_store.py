import sqlite3
import threading

class DedupStore:
    def __init__(self, db_path="data/dedup.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                topic TEXT NOT NULL,
                event_id TEXT NOT NULL,
                processed_at TEXT,
                PRIMARY KEY (topic, event_id)
            )
        """)
        self.conn.commit()

    def register_event(self, topic: str, event_id: str) -> bool:
        """
        Mencoba menyimpan event.
        Return True jika event baru berhasil disimpan.
        Return False jika event sudah ada (duplikat).
        """
        with self.lock:
            try:
                self.conn.execute(
                    "INSERT INTO processed_events (topic, event_id, processed_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (topic, event_id)
                )
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False