"""GET /stats — return aggregated counters and uptime from PostgreSQL."""

import time

from fastapi import APIRouter, Request

router = APIRouter()

_start_time = time.time()


@router.get("/stats")
async def get_stats(request: Request):
    """Return received, unique_processed, duplicate_dropped, topic list, and uptime."""
    pool = request.app.state.db_pool

    row = await pool.fetchrow(
        "SELECT received, unique_processed, duplicate_dropped FROM stats WHERE id = 1"
    )
    topics = await pool.fetch("SELECT DISTINCT topic FROM events ORDER BY topic")

    return {
        "received": row["received"],
        "unique_processed": row["unique_processed"],
        "duplicate_dropped": row["duplicate_dropped"],
        "topics": [t["topic"] for t in topics],
        "uptime_seconds": round(time.time() - _start_time, 2),
    }
