"""GET /events — retrieve unique processed events, optionally filtered by topic."""

import json
from typing import List, Optional

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/events")
async def get_events(
    request: Request,
    topic: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
):
    """Return events from PostgreSQL. Filter by topic when provided."""
    pool = request.app.state.db_pool

    if topic:
        rows = await pool.fetch(
            """
            SELECT id, topic, event_id, timestamp, source, payload, received_at
            FROM events
            WHERE topic = $1
            ORDER BY received_at DESC
            LIMIT $2 OFFSET $3
            """,
            topic,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT id, topic, event_id, timestamp, source, payload, received_at
            FROM events
            ORDER BY received_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )

    return [
        {
            "id": r["id"],
            "topic": r["topic"],
            "event_id": r["event_id"],
            "timestamp": r["timestamp"].isoformat(),
            "source": r["source"],
            "payload": json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
            "received_at": r["received_at"].isoformat(),
        }
        for r in rows
    ]
