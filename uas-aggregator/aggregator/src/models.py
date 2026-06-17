"""Pydantic models and enums for the aggregator service."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, field_validator


class EventIn(BaseModel):
    topic: str
    event_id: str
    timestamp: datetime
    source: str
    payload: Dict[str, Any]

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        if isinstance(v, str):
            return datetime.fromisoformat(v.rstrip("Z"))
        return v


class EventOut(BaseModel):
    id: int
    topic: str
    event_id: str
    timestamp: datetime
    source: str
    payload: Dict[str, Any]
    received_at: datetime

    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    received: int
    unique_processed: int
    duplicate_dropped: int
    topics: list
    uptime_seconds: float


class ProcessResult(str, Enum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
